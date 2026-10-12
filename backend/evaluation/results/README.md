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
