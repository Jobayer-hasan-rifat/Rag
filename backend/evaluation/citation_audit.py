"""Citation and evidence audit of stored answers: automatic flags plus a human review template.

Two different questions are kept apart on purpose:

1. Is a citation *valid*? (the label was supplied, the cited passage exists, belongs to this
   evaluation corpus, and, in the live pipeline, names the exact document version). That is checked
   deterministically by the application and by the scenario gates.
2. Does a cited passage *support* the claim next to it? No program can prove this for every
   language and claim type. This module only raises **flags** for cases a person should look at:
   a sentence without a citation, a number, quotation or identifier that does not occur in the cited
   passages, or a sentence that shares almost no content words with them. A flag is a reason to
   review, not a verdict; an unflagged sentence is not proven supported.

Run: `python -m evaluation.citation_audit --results evaluation/results/rag_qwen3.5_9b_gpu.json
--sample 20 --out review.json`. The output is a review template whose judgement fields are empty;
it records how the sample was chosen. Nothing in this file is a human review result.
"""

import argparse
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evaluation.dataset import load_rag_corpus
from evaluation.rag_metrics import fold
from evaluation.scripted import content_tokens
from evaluation.stats import wilson_interval

_LABEL = re.compile(r"\[S(\d+)\]")
_SENTENCE = re.compile(r"(?<=[.!?।])\s+|\n+")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_QUOTE = re.compile(r'"([^"]{3,200})"')
_IDENTIFIER = re.compile(r"\b[A-Za-z][A-Za-z0-9]*-\d+\b")
OVERLAP_FLAG_BELOW = 0.25  # a screening threshold chosen to flag, not a measure of quality

RUBRIC = {
    "supported": "yes = every claim in the sentence is stated by the cited passage(s); "
    "partial = some claims are, others go beyond them; no = the passage does not state it",
    "citation_correct": "yes = the label points at a passage that states the claim; "
    "wrong_source = another supplied passage states it; none = no supplied passage does",
    "numbers_and_names": "ok = every number, name and identifier matches the source exactly "
    "(Bengali and Latin digits count as equal); changed = any differs",
    "notes": "free text; do not paste private data",
}


@dataclass
class SentenceAudit:
    text: str
    labels: list[int]
    flags: list[str] = field(default_factory=list)


def audit_sentences(answer: str, sources: list[str]) -> list[SentenceAudit]:
    """`sources[i]` is the text behind label S(i+1) in the answer's own request."""
    pieces: list[str] = []
    for raw in _SENTENCE.split(answer.strip()):
        piece = raw.strip()
        if not piece:
            continue
        # a marker after the full stop ("... July. [S1]") belongs to the sentence before it
        if pieces and not _LABEL.sub("", piece).strip():
            pieces[-1] = f"{pieces[-1]} {piece}"
        else:
            pieces.append(piece)
    audits: list[SentenceAudit] = []
    for sentence in pieces:
        labels = [int(n) for n in _LABEL.findall(sentence)]
        audit = SentenceAudit(sentence, labels)
        clean = _LABEL.sub("", sentence).strip()
        if not clean:
            continue
        if not labels:
            audit.flags.append("no_citation")
            audits.append(audit)
            continue
        invalid = [n for n in labels if not 1 <= n <= len(sources)]
        if invalid:
            audit.flags.append("label_not_supplied")
        cited = " ".join(fold(sources[n - 1]) for n in labels if 1 <= n <= len(sources))
        folded = fold(clean)
        if not cited.strip() and not invalid:
            audit.flags.append("cited_source_text_missing")
        if cited:
            if set(_NUMBER.findall(folded)) - set(_NUMBER.findall(cited)):
                audit.flags.append("number_not_in_cited_source")
            if any(fold(q) not in cited for q in _QUOTE.findall(clean)):
                audit.flags.append("quotation_not_in_cited_source")
            if any(fold(i) not in cited for i in _IDENTIFIER.findall(clean)):
                audit.flags.append("identifier_not_in_cited_source")
            words = content_tokens(folded)
            if words and len(words & content_tokens(cited)) / len(words) < OVERLAP_FLAG_BELOW:
                audit.flags.append("low_word_overlap_with_cited_source")
        audits.append(audit)
    return audits


def audit_results(results: list[dict[str, Any]], passage_text: dict[str, str]) -> dict[str, Any]:
    """Flags for every answered question of a stored `evaluation.rag_eval` report."""
    answered = [r for r in results if r.get("answered") and not r.get("error")]
    flagged_questions: list[dict[str, Any]] = []
    sentences = flagged_sentences = 0
    by_flag: dict[str, int] = {}
    for result in answered:
        sources = [passage_text.get(p, "") for p in result["source_passages"]]
        audits = audit_sentences(result["answer"], sources)
        sentences += len(audits)
        bad = [a for a in audits if a.flags]
        flagged_sentences += len(bad)
        for a in bad:
            for flag in a.flags:
                by_flag[flag] = by_flag.get(flag, 0) + 1
        if bad:
            flagged_questions.append(
                {
                    "id": result["id"],
                    "category": result["category"],
                    "flags": {a.text[:120]: a.flags for a in bad},
                }
            )
    interval = wilson_interval(len(flagged_questions), len(answered))
    return {
        "answered_questions": len(answered),
        "sentences": sentences,
        "flagged_sentences": flagged_sentences,
        "flagged_questions": len(flagged_questions),
        "flagged_question_rate": (
            round(len(flagged_questions) / len(answered), 3) if answered else None
        ),
        "flagged_question_rate_ci95": list(interval) if interval else None,
        "flags": dict(sorted(by_flag.items())),
        "flagged": flagged_questions,
        "interpretation": "flags select answers for human review; they are not errors and an "
        "unflagged sentence is not verified",
    }


def review_template(
    results: list[dict[str, Any]],
    passage_text: dict[str, str],
    questions: dict[str, str],
    *,
    sample: int,
    seed: int,
) -> dict[str, Any]:
    """A reproducible sample for human review: every flagged answer first (up to half the sample),
    then a seeded random draw from the rest, stratified by category. Judgement fields are empty."""
    answered = [r for r in results if r.get("answered") and not r.get("error")]
    flagged_ids = {f["id"] for f in audit_results(answered, passage_text)["flagged"]}
    rng = random.Random(seed)  # noqa: S311  (reproducible sampling, not security)
    flagged = [r for r in answered if r["id"] in flagged_ids]
    rng.shuffle(flagged)
    chosen = flagged[: sample // 2]
    rest = [r for r in answered if r["id"] not in {c["id"] for c in chosen}]
    rng.shuffle(rest)
    by_category: dict[str, list[dict[str, Any]]] = {}
    for r in rest:
        by_category.setdefault(r["category"], []).append(r)
    while len(chosen) < sample and any(by_category.values()):
        for category in sorted(by_category):
            if by_category[category] and len(chosen) < sample:
                chosen.append(by_category[category].pop())
    items = []
    for r in sorted(chosen, key=lambda r: r["id"]):
        sources = [passage_text.get(p, "") for p in r["source_passages"]]
        items.append(
            {
                "id": r["id"],
                "category": r["category"],
                "language": r["language"],
                "question": questions.get(r["id"], ""),
                "answer": r["answer"],
                "supplied_sources": {f"S{i + 1}": t for i, t in enumerate(sources)},
                "automatic_flags": [
                    a.flags for a in audit_sentences(r["answer"], sources) if a.flags
                ],
                "review": {
                    "supported": None,
                    "citation_correct": None,
                    "numbers_and_names": None,
                    "notes": "",
                },
            }
        )
    return {
        "method": f"seed {seed}; up to {sample // 2} flagged answers, rest stratified by category",
        "answered_questions_available": len(answered),
        "sample_size": len(items),
        "reviewed": 0,
        "reviewer": None,
        "rubric": RUBRIC,
        "items": items,
        "limitations": "synthetic questions and passages only; one answer per question; a "
        "single reviewer cannot estimate agreement; a sample of this size bounds an error rate "
        "only roughly",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--results", type=Path, required=True, help="a rag_eval JSON report")
    parser.add_argument("--sample", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20261016)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = json.loads(args.results.read_text(encoding="utf-8"))
    corpus = {p.id: p.text for p in load_rag_corpus().passages}
    questions = {
        q["id"]: q["question"]
        for q in json.loads(
            (Path(__file__).parent / "rag" / "questions.json").read_text(encoding="utf-8")
        )["questions"]
    }
    audit = audit_results(report["results"], corpus)
    print(
        f"{audit['answered_questions']} answered, {audit['flagged_questions']} flagged "
        f"({audit['flagged_question_rate']}), flags {audit['flags']}"
    )
    if args.out:
        template = review_template(
            report["results"], corpus, questions, sample=args.sample, seed=args.seed
        )
        args.out.write_text(
            json.dumps({"audit": audit, "review": template}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
