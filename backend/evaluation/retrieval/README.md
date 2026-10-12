# Retrieval evaluation dataset

A small, version-controlled, synthetic dataset for measuring semantic retrieval quality in Bangla,
English and mixed Bangla-English text. It is original text written for this project (CC0-1.0): no
real documents, no personal data, nothing copied from other sources.

## Contents

| File | What it holds |
|---|---|
| `corpus.json` | 12 documents (4 English, 4 Bangla, 4 mixed), 4 sections each; every section holds exactly one passage, so a passage is exactly one chunk after processing. 48 passages. |
| `queries.json` | 52 queries with the passages that answer them. |

Query categories: `english` (10), `bangla` (12), `mixed` (10), `punctuation_unicode` (6), `cross_lingual`
(4, query and answer in different languages), `confusable` (4, similar wording with a different
meaning, for example *river bank* against *savings bank*), and `no_answer` (6, nothing in the corpus
answers them).

Within the corpus, documents of different languages deliberately share topics (rice, indexing,
dengue, banks, hilsa, ...) with *different facts*, so a model that only matches topics, not the asked
fact, is penalised.

## Relevance model

- `relevant`: passage ids that answer the query. A hit is a retrieved relevant passage.
- `neutral`: passages that are acceptable but not the intended answer (for example the passage that
  states the same fact in another language). They are removed from the ranking before scoring, so
  retrieving them is neither a hit nor a miss.
- An empty `relevant` list means the corpus has no answer. These queries are not part of
  Recall/MRR/nDCG; they are used for the *answerability AUROC*: how well the best passage's score
  separates answerable from unanswerable queries.

Relevance is binary. nDCG@k therefore uses gain 1 for every relevant passage.

## Running it

From `backend/` with the ML dependencies installed (`pip install torch sentence-transformers`; on
CPU use the PyTorch CPU wheel index):

```bash
python -m evaluation.retrieval_eval --mode memory      # configured model, ranking in numpy
python -m evaluation.retrieval_eval --mode database    # real pipeline: pgvector + SearchService
python -m evaluation.compare_models                    # compare candidate models
python -m pytest tests/unit/test_retrieval_evaluation.py   # dataset and metric checks
```

The first run downloads the model (see `docs/` for size and first-run behaviour). The runner
reports Recall@1/3/5/10, MRR@10, nDCG@10, the answerability AUROC, embedding throughput, query
latency, embedding failures, truncated passages, per-category results and the queries whose top
result was wrong.

## Adding cases

1. **A new query**: append to `queries.json`. Use a new unique `id` (`<category prefix>-NN`), one of
   the categories above, and the passage ids that answer it. Write the query so it is *not* a
   copy of the passage: paraphrase, ask for the fact, change the word order. Put acceptable
   alternatives in `neutral` instead of leaving them to count as misses.
2. **A new passage or document**: append to `corpus.json`. Keep one passage per section, give it a
   unique id (`<doc id>-<n>`), and keep it under about 300 characters so it stays one chunk. Give
   it facts that no other passage states, then add at least one query that needs it.
3. **Validate**: `python -m pytest tests/unit/test_retrieval_evaluation.py` checks for duplicate
   ids, unknown passage references, empty queries and passages that overlap `relevant`/`neutral`.
4. **Re-measure** and record the new numbers; never edit old results to match a changed dataset.
   Bump `version` in both files when the content changes, since numbers are only comparable for the
   same version.

## Limitations (read before quoting any number)

- **Small and easy.** 46 answerable queries over 48 short passages. One query is about 2 percentage
  points of Recall@1, so differences of a few points between models are noise.
- **Synthetic and written by one author**, so it reflects one writing style and vocabulary. Real
  documents are longer, noisier, OCR-damaged, in other registers and dialects, and have many more
  distractors.
- **Short passages.** Real chunks are up to 1000 characters; truncation, long-range context and
  chunk boundaries are not exercised here.
- **Bangla coverage is narrow**: 12 Bangla queries and 16 Bangla passages across a handful of
  topics. A good score here shows the model separates these passages; it is not evidence of strong
  Bangla retrieval in general.
- **Binary, single-answer relevance.** Some queries have more than one defensible answer.
- Scores depend on the exact model revision, the prefixes and the embedding settings; the runner
  prints them.
