"""Compare candidate embedding models on the synthetic retrieval dataset.

Run from `backend/`:
    python -m evaluation.compare_models                      # all candidates
    python -m evaluation.compare_models --only e5-small      # one candidate
    python -m evaluation.compare_models --json results.json  # also write raw numbers

Models are loaded exactly as the application loads them: a pinned revision, safetensors weights
only and no remote code. The first load of a model downloads it (time reported separately).
Everything here is measured on the machine that runs it; nothing is quoted from model cards.
"""

import argparse
import json
import platform
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from evaluation.dataset import Dataset, EvalQuery, load_dataset
from evaluation.metrics import QueryResult, answerability_auroc, strip_neutral, summarise


@dataclass(frozen=True)
class Candidate:
    key: str
    model: str
    revision: str
    query_prefix: str = ""
    passage_prefix: str = ""
    note: str = ""


# Revisions are the commit SHAs read from the Hugging Face API on 2026-10-12.
CANDIDATES = [
    Candidate(
        "minilm-multilingual",
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "e8f8c211226b894fcb81acc59f3b34ba3efd5f42",
        note="documents 50 languages, Bengali not among them (control)",
    ),
    Candidate(
        "e5-small",
        "intfloat/multilingual-e5-small",
        "614241f622f53c4eeff9890bdc4f31cfecc418b3",
        "query: ",
        "passage: ",
    ),
    Candidate(
        "e5-base",
        "intfloat/multilingual-e5-base",
        "d128750597153bb5987e10b1c3493a34e5a4502a",
        "query: ",
        "passage: ",
    ),
    Candidate(
        "labse",
        "sentence-transformers/LaBSE",
        "836121a0533e5664b21c7aacc5d22951f2b8b25b",
        note="trained for translation pairs, not retrieval",
    ),
]


def load_model(candidate: Candidate, device: str = "cpu") -> Any:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(
        candidate.model,
        revision=candidate.revision,
        trust_remote_code=False,
        device=device,
        model_kwargs={"use_safetensors": True},
    )


def passage_text(candidate: Candidate, passage: Any, with_heading: bool) -> str:
    body = f"{passage.heading}\n{passage.text}" if with_heading else passage.text
    return candidate.passage_prefix + body


def encode(model: Any, texts: list[str], batch_size: int) -> np.ndarray:
    vectors = model.encode(
        texts, batch_size=batch_size, normalize_embeddings=True, convert_to_numpy=True
    )
    return np.asarray(vectors, dtype=np.float32)


def rank(
    dataset: Dataset, query_vectors: np.ndarray, passage_vectors: np.ndarray
) -> list[QueryResult]:
    passage_ids = [p.id for p in dataset.passages]
    results: list[QueryResult] = []
    scores = query_vectors @ passage_vectors.T  # cosine: both sides are unit length
    for query, row in zip(dataset.queries, scores, strict=True):
        order = np.argsort(-row, kind="stable")
        ranking = strip_neutral([passage_ids[i] for i in order], query.neutral)
        results.append(
            QueryResult(
                query_id=query.id,
                category=query.category,
                relevant=query.relevant,
                ranking=tuple(ranking),
                top_score=float(row[order[0]]),
            )
        )
    return results


def token_stats(model: Any, texts: list[str]) -> dict[str, Any]:
    tokenizer = model.tokenizer
    limit = int(model.max_seq_length)
    counts = [len(tokenizer(t, add_special_tokens=True)["input_ids"]) for t in texts]
    return {
        "max_seq_length": limit,
        "tokens_max": max(counts),
        "tokens_mean": round(statistics.fmean(counts), 1),
        "truncated": sum(c > limit for c in counts),
    }


def evaluate_candidate(
    candidate: Candidate, dataset: Dataset, *, with_heading: bool, batch_size: int
) -> dict[str, Any]:
    started = time.perf_counter()
    model = load_model(candidate)
    first_load = time.perf_counter() - started  # includes the download on a cold cache
    del model
    started = time.perf_counter()
    model = load_model(candidate)
    warm_load = time.perf_counter() - started

    passages = [passage_text(candidate, p, with_heading) for p in dataset.passages]
    queries = [candidate.query_prefix + q.query for q in dataset.queries]
    encode(model, passages[:4], batch_size)  # warm-up, excluded from timings

    started = time.perf_counter()
    passage_vectors = encode(model, passages, batch_size)
    passage_seconds = time.perf_counter() - started
    query_times = []
    query_vectors = []
    for text in queries:
        started = time.perf_counter()
        query_vectors.append(encode(model, [text], 1)[0])
        query_times.append((time.perf_counter() - started) * 1000)

    results = rank(dataset, np.vstack(query_vectors), passage_vectors)
    by_category: dict[str, Any] = {}
    for category in sorted({r.category for r in results}):
        summary = summarise([r for r in results if r.category == category])
        if summary:
            by_category[category] = asdict(summary)
    overall = summarise(results)
    return {
        "key": candidate.key,
        "model": candidate.model,
        "revision": candidate.revision,
        "with_heading": with_heading,
        "dimension": int(passage_vectors.shape[1]),
        "parameters_m": round(sum(p.numel() for p in model.parameters()) / 1e6, 1),
        "first_load_s": round(first_load, 1),
        "warm_load_s": round(warm_load, 2),
        "passages_per_s": round(len(passages) / passage_seconds, 1),
        "query_ms_median": round(statistics.median(query_times), 1),
        "overall": asdict(overall) if overall else None,
        "answerability_auroc": answerability_auroc(results),
        "by_category": by_category,
        "tokens": token_stats(model, passages),
    }


def print_table(rows: list[dict[str, Any]]) -> None:
    header = (
        "| Model | Heading | Dim | Recall@1 | Recall@3 | Recall@5 | MRR@10 | nDCG@10 | "
        "AUROC(no-answer) | Passages/s | Query ms | Load s (cold/warm) | Max tokens |"
    )
    print(header)
    print("|" + "---|" * (header.count("|") - 1))
    for r in rows:
        o = r["overall"]
        print(
            f"| {r['key']} | {'yes' if r['with_heading'] else 'no'} | {r['dimension']} | "
            f"{o['recall_at_1']:.3f} | {o['recall_at_3']:.3f} | {o['recall_at_5']:.3f} | "
            f"{o['mrr_at_10']:.3f} | {o['ndcg_at_10']:.3f} | {r['answerability_auroc']:.3f} | "
            f"{r['passages_per_s']} | {r['query_ms_median']} | "
            f"{r['first_load_s']}/{r['warm_load_s']} | {r['tokens']['tokens_max']}"
            f"/{r['tokens']['max_seq_length']} |"
        )
    print("\nBy category (Recall@1 / MRR@10):")
    categories = sorted({c for r in rows for c in r["by_category"]})
    print("| Model | Heading | " + " | ".join(categories) + " |")
    print("|" + "---|" * (len(categories) + 2))
    for r in rows:
        cells = [
            f"{r['by_category'][c]['recall_at_1']:.2f} / {r['by_category'][c]['mrr_at_10']:.2f}"
            for c in categories
        ]
        print(
            f"| {r['key']} | {'yes' if r['with_heading'] else 'no'} | " + " | ".join(cells) + " |"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--only", nargs="*", help="candidate keys to run")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--heading", choices=["no", "yes", "both"], default="no")
    parser.add_argument("--json", type=Path, help="write the raw results here")
    args = parser.parse_args()

    import torch

    dataset = load_dataset()
    print(
        f"Python {platform.python_version()} on {platform.platform()} ({platform.processor()}), "
        f"torch {torch.__version__}, {torch.get_num_threads()} threads, "
        f"cuda={torch.cuda.is_available()}"
    )
    print(
        f"{len(dataset.passages)} passages, {len(dataset.queries)} queries "
        f"({sum(1 for q in dataset.queries if not q.relevant)} unanswerable)\n"
    )
    rows: list[dict[str, Any]] = []
    for candidate in CANDIDATES:
        if args.only and candidate.key not in args.only:
            continue
        for with_heading in {"no": [False], "yes": [True], "both": [False, True]}[args.heading]:
            print(f"running {candidate.key} (heading={with_heading}) ...", file=sys.stderr)
            rows.append(
                evaluate_candidate(
                    candidate, dataset, with_heading=with_heading, batch_size=args.batch_size
                )
            )
    print_table(rows)
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")


def _unused(_: EvalQuery) -> None:  # keeps the import list honest for type checkers
    return None


if __name__ == "__main__":
    main()
