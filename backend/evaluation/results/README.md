# Recorded results

Raw output of the evaluation tools, kept so the numbers in the documentation can be checked.
They were measured on one Windows desktop (AMD64 Family 25 Model 33, 6 threads, CPU only,
Python 3.12.13, torch 2.14.1+cpu, sentence-transformers 6.1.0) on 2026-10-12/13 and describe the
synthetic dataset in `evaluation/retrieval/` (version 1). They are not comparable to results on
other data or hardware, and the dataset is small (see its README for limitations).

| File | Produced by |
|---|---|
| `model_comparison.json` | `python -m evaluation.compare_models --heading both` |
| `retrieval_e5-small_memory.json` | `python -m evaluation.retrieval_eval --mode memory` |
| `retrieval_e5-small_database.json` | `python -m evaluation.retrieval_eval --mode database` |
| `phase12_models_qwen3.5_9b.json` | `python -m evaluation.suite --profile models --llm-model qwen3.5:9b` (Phase 12: retrieval arms with intervals, reranking cost, scenarios, answers; same machine; produced on a working tree one step ahead of its recorded commit) |
| `citation_review_template_qwen3.5_9b.json` | `python -m evaluation.citation_audit --results evaluation/results/rag_qwen3.5_9b_gpu.json --sample 20`: a review sample with empty judgement fields (no review has been done) |

The Phase 12 harness uses its own document ids, so its numbers are comparable with the files above
only through `--legacy-ids` (the README of `evaluation/` explains the one-question tie sensitivity).
Committed baselines for regression gates live in `evaluation/baselines/`.
