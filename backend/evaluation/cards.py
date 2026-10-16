"""Dataset cards: what each evaluation dataset is, computed from the files themselves.

Counts, categories and content hashes are measured every time (never typed in), so a card cannot
drift from its data. The descriptive fields (origin, methodology, limitations) are written here once
and reviewed with the data. Run `python -m evaluation.cards [--json cards.json]`.
"""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.rag.tokens import bengali_share
from evaluation.dataset import (
    DATA_DIR,
    HELDOUT_DIR,
    LEXICAL_DIR,
    RAG_DIR,
    load_corpus_only,
    load_dataset,
    load_rag_questions,
)

SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"
_ORIGIN = (
    "synthetic; original text written for this project by its author (CC0-1.0); "
    "no real documents, no personal data, not externally sourced, no independent review"
)
_CONTAMINATION = (
    "The text is original and unpublished, so overlap with a model's training data is unlikely, "
    "but it cannot be excluded. Models were chosen while looking at the development sets "
    "(retrieval-v1, retrieval-lexical); only retrieval-heldout was kept for a final comparison."
)
_RELEVANCE = (
    "Binary relevance written by the author with the data. `relevant` lists the passages that "
    "answer a query; `neutral` lists acceptable alternatives that are removed from the ranking "
    "before scoring; an empty `relevant` list means the corpus has no answer."
)


def _sha256(*paths: Path) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _language(text: str) -> str:
    share = bengali_share(text)
    return "bn" if share >= 0.7 else ("en" if share < 0.1 else "mixed")


def _retrieval_card(name: str, directory: Path, limitations: list[str]) -> dict[str, Any]:
    dataset = load_dataset(directory)
    meta = json.loads((directory / "queries.json").read_text(encoding="utf-8"))
    answerable = [q for q in dataset.queries if q.relevant]
    return {
        "name": name,
        "kind": "retrieval",
        "dataset_id": meta["dataset"],
        "version": meta["version"],
        "documents": len(dataset.documents),
        "passages": len(dataset.passages),
        "questions": len(dataset.queries),
        "answerable": len(answerable),
        "unanswerable": len(dataset.queries) - len(answerable),
        "categories": dict(sorted(Counter(q.category for q in dataset.queries).items())),
        "question_languages": dict(
            sorted(Counter(_language(q.query) for q in dataset.queries).items())
        ),
        "document_languages": dict(sorted(Counter(d.language for d in dataset.documents).items())),
        "ground_truth": "passage ids (<document id>-<n>); stable across re-chunking because a "
        "retrieved chunk is mapped to its passage through its document and section ordinal",
        "relevance_methodology": _RELEVANCE,
        "origin": _ORIGIN,
        "contamination": _CONTAMINATION,
        "limitations": limitations,
        "sha256": _sha256(directory / "corpus.json", directory / "queries.json"),
        "reproduce": "python -m evaluation.suite --profile models  (or --profile deterministic)",
    }


def _rag_card() -> dict[str, Any]:
    questions = load_rag_questions()
    meta = json.loads((RAG_DIR / "questions.json").read_text(encoding="utf-8"))
    extra = load_corpus_only(RAG_DIR)
    base, lexical = load_dataset(DATA_DIR), load_dataset(LEXICAL_DIR)
    return {
        "name": "rag-answer-eval",
        "kind": "answer generation",
        "dataset_id": meta["dataset"],
        "version": meta["version"],
        "documents": len(base.documents) + len(lexical.documents) + len(extra.documents),
        "passages": len(base.passages) + len(lexical.passages) + len(extra.passages),
        "questions": len(questions),
        "answerable": sum(1 for q in questions if not q.abstain),
        "unanswerable": sum(1 for q in questions if q.abstain),
        "categories": dict(sorted(Counter(q.category for q in questions).items())),
        "question_languages": dict(
            sorted(Counter(_language(q.question) for q in questions).items())
        ),
        "ground_truth": "passage ids for citation correctness; `expected` groups of alternative "
        "substrings for fact recall; `forbidden` strings; `abstain` for unanswerable questions",
        "relevance_methodology": "As the retrieval datasets; expected facts are author-written "
        "substrings (case-folded, Bengali digits folded), so a correct answer worded differently "
        "can be missed (recall is a lower bound) and a wrong answer containing the substring can "
        "pass; faithfulness is a separate human judgement, never inferred from these checks.",
        "origin": _ORIGIN,
        "contamination": _CONTAMINATION,
        "limitations": [
            "48 questions: one question is about 2 points; categories of 2 to 10 are anecdotal",
            "the generation model, the validation rules and the default settings were tuned while "
            "looking at these questions (no untouched test split for answers)",
            "the injected-instruction documents are author-written and cover few styles of attack",
            "single author, single reviewer for the recorded faithfulness review",
        ],
        "sha256": _sha256(
            DATA_DIR / "corpus.json",
            LEXICAL_DIR / "corpus.json",
            RAG_DIR / "corpus.json",
            RAG_DIR / "questions.json",
        ),
        "reproduce": "python -m evaluation.rag_eval --model <model>  (opt-in)",
    }


def _scenario_card() -> dict[str, Any] | None:
    path = SCENARIO_DIR / "scenarios.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    scenarios = data["scenarios"]
    return {
        "name": "pipeline-scenarios",
        "kind": "end-to-end behaviour (versions, conversations, abstention, injection, faults)",
        "dataset_id": data["dataset"],
        "version": data["version"],
        "documents": len(data["texts"]),
        "scenarios": len(scenarios),
        "questions": sum(
            1 for s in scenarios for step in s["steps"] if step["op"] in ("ask", "converse")
        ),
        "categories": dict(sorted(Counter(s["category"] for s in scenarios).items())),
        "ground_truth": "each step states its expected outcome (substrings, abstention, cited "
        "version by symbolic name, citation availability); no label is read by the application",
        "relevance_methodology": "Expected behaviour is specified from the product's documented "
        "rules (version activation, citation resolution, history policy), not from observed "
        "output.",
        "origin": _ORIGIN,
        "contamination": "not applicable: the checks are about pipeline behaviour, not model "
        "knowledge",
        "limitations": [
            "the deterministic profile uses a scripted generation model, so it verifies the "
            "application's gates (retrieval eligibility, validation, citation resolution), not a "
            "language model's quality or robustness",
            "the scenario count is small and hand-written; passing shows the specified cases work, "
            "not that no other case fails",
        ],
        "sha256": _sha256(path),
        "reproduce": "python -m evaluation.suite --profile deterministic",
    }


def dataset_cards() -> list[dict[str, Any]]:
    cards = [
        _retrieval_card(
            "retrieval-v1",
            DATA_DIR,
            [
                "46 answerable questions over 48 short passages; one question is about 2 points of "
                "Recall@1",
                "short passages (one chunk each): truncation and chunk boundaries are not tested",
                "narrow Bangla coverage (12 questions); not evidence of general Bangla retrieval",
                "binary, single-answer relevance",
            ],
        ),
        _retrieval_card(
            "retrieval-lexical",
            LEXICAL_DIR,
            [
                "identifiers, rare terms and near-duplicate passages written to need exact words",
                "evaluated together with retrieval-v1 as one corpus in the hybrid evaluation",
            ],
        ),
        _retrieval_card(
            "retrieval-heldout",
            HELDOUT_DIR,
            [
                "kept for the final reranker comparison only; reuse for tuning would end its role",
                "33 questions over a small corpus",
            ],
        ),
        _rag_card(),
    ]
    scenario = _scenario_card()
    if scenario:
        cards.append(scenario)
    return cards


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    cards = dataset_cards()
    for card in cards:
        print(
            f"{card['name']} v{card['version']}: {card.get('questions')} questions "
            f"({card.get('answerable', '-')} answerable, {card.get('unanswerable', '-')} "
            f"unanswerable), {card.get('documents')} documents, sha256 {card['sha256'][:12]}"
        )
    if args.json:
        args.json.write_text(json.dumps(cards, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
