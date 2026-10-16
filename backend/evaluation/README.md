# Evaluation

How the retrieval and answer pipeline is measured, what each number means, and what must never
regress. Everything here runs against the real application (HTTP API, PostgreSQL/pgvector, the
processing and embedding pipeline); nothing reads real users' documents.

## What it answers

| Question | Where it is measured |
|---|---|
| Does retrieval find the right evidence? | `retrieval_matrix` (dense, sparse, hybrid) on the retrieval datasets: Recall@k, MRR@10, nDCG@10, candidate recall, per category, with intervals |
| Does reranking earn its cost? | the same candidates before and after reranking at depths 10, 20 and 50: quality, where it helps and where it hurts, cold and warm latency, memory |
| Are answers supported, cited and abstained correctly? | `rag_eval` / `suite` answer section: fact recall, abstention, citation precision, forbidden strings, latency; plus the citation audit and the scenario checks |
| Do citations point at the exact version? | scenarios `cit-*` and `ver-*` (hard checks) |
| Does conversation history help without becoming evidence? | scenarios `his-*` (hard checks on the prompt, behaviour checks on answers) |
| Do superseded versions stay out? | scenarios `ver-*`, `cit-*`, retrieval and answers both |
| Does it behave when parts fail? | scenarios `flt-*` (provider timeout, outage, rate limit, dense retrieval down) |
| Which regressions block a change? | `compare_runs` hard gates (below) |

No single aggregate score is reported. Read the per-category tables and the failure lists.

## Commands

From `backend/`, with Docker running (a throwaway PostgreSQL and Redis are started):

```bash
python -m evaluation.cards                                   # dataset cards (counts, hashes)
python -m evaluation.suite --profile deterministic           # fast, no model, no ML package
python -m evaluation.suite --profile deterministic --baseline evaluation/baselines/deterministic.json
python -m evaluation.suite --profile models --only retrieval # real embedding model + reranker (cached)
python -m evaluation.suite --profile models --llm-model qwen3.5:9b   # also real answers (opt-in)
python -m evaluation.compare_runs BASELINE.json CANDIDATE.json [--strict]
python -m evaluation.citation_audit --results evaluation/results/rag_qwen3.5_9b_gpu.json --sample 20 --out review.json
python -m pytest tests/integration/test_evaluation_scenarios.py tests/unit/test_evaluation_framework.py
```

The older single-purpose tools remain and keep their recorded results in `evaluation/results/`
(`retrieval_eval`, `hybrid_eval`, `rerank_eval`, `compare_models`, `rag_eval`). Those results were
produced before document versions existed and with a different harness; they are not rewritten and
are **not** compared with the new runs unless the dataset hash, model revision and settings match
(`compare_runs` checks this and says "not comparable" otherwise).

### Profiles

| | `deterministic` | `models` |
|---|---|---|
| Embeddings | hashing stand-in (`test-hash`) | `intfloat/multilingual-e5-small`, pinned revision |
| Reranker | word-count fake | `BAAI/bge-reranker-v2-m3`, pinned revision |
| Generation | scripted reader (`evaluation/scripted.py`) | none, or a real model with `--llm-model` |
| Needs | Docker only | Docker, the ML packages, the local model cache (no download unless `--allow-download`) |
| Meaning of the numbers | the harness and the application's gates; **not** model quality | model quality on these small synthetic datasets |
| Reproducible | exactly (same data, same numbers) | up to hardware and library versions |

CI runs the deterministic profile only. It imports no ML package (a test starts a clean interpreter
and checks) and the lightweight CI requirements do not list one (also tested).

## Datasets

`python -m evaluation.cards` prints and `--json` writes the cards; each card records name, version,
counts of documents, passages and questions, answerable and unanswerable counts, categories,
languages, the ground-truth identifiers, the relevance methodology, origin, contamination notes,
limitations, a content hash and how to reproduce.

| Dataset | Questions | Role |
|---|---|---|
| `retrieval-v1` (+ `retrieval-lexical` as one corpus, "combined") | 87 (77 answerable) | development: English, Bangla, mixed, cross-lingual, confusable, exact identifiers |
| `retrieval-heldout` | 33 (30 answerable) | final comparison only; never used to choose a setting |
| `rag-answer-eval` | 48 (38 answerable) | answers: unanswerable, contradiction, injected documents, long chunk, identifiers |
| `pipeline-scenarios` | 34 scenarios | pipeline behaviour: versions, citations, history, abstention, injection, faults, isolation |

All of it is synthetic, original text written by this project's author (CC0-1.0), single author,
no independent review, no real or personal data. Overlap with a model's training data cannot be
excluded; models were chosen while looking at the development sets. Dataset files are never edited
to suit a model: a change bumps `version`, changes the hash, and makes older numbers not comparable.

**Stable identity.** Ground truth names *passages* (`<document id>-<n>`), not chunks. A chunk is mapped
to its passage through the evaluation corpus' fixed identifiers (one passage per section), so
re-chunking or re-processing never invalidates a label. Scenarios name documents and versions
symbolically (`rice`, `v2`) and the runner maps them to whatever ids the system created.

**No leakage into the system.** The application never reads `relevant`, `expected`, `forbidden` or a
scenario's expectation: they are used only after the fact, in this package.

## Metrics

All rank metrics use binary relevance, one ranking per question, `neutral` passages removed
before scoring (acceptable alternatives are neither hits nor misses).

- **Recall@k**: the share of a question's relevant passages in the first k results.
- **MRR@10**: reciprocal rank of the first relevant passage within 10 (0 if none).
- **nDCG@10**: discounted gain with gain 1 per relevant passage, normalised by the ideal ranking.
- **Candidate recall@N** (before reranking): the share of answerable questions with at least one
  relevant passage among the first N hybrid candidates, and the mean Recall@N. Reranking can only
  reorder these, so it caps what reranking can achieve.
- **Unanswerable questions** are excluded from Recall, MRR and nDCG (they have no relevant passage,
  so the metrics are undefined). They are counted, and for the dense retriever the *answerability
  AUROC* of the top similarity score is reported. In answer evaluation they are the denominator of
  *abstention accuracy*.
- **Intervals.** 95% percentile bootstrap over questions for means, Wilson for proportions; none is
  printed below 10 questions. They describe sampling noise over these questions only, not
  generalisation to other documents. Per-category intervals are wide; treat categories with fewer
  than about 10 questions as anecdotes.
- **Answer metrics** (`rag_metrics.py`): fact recall = every expected group has an alternative in the
  answer (case-folded, Bengali digits folded); abstention accuracy = unanswerable questions answered
  with the fixed insufficient-evidence reply; citation precision = cited passages that are gold;
  forbidden-string rate; label validity and metadata matching the database. These are lexical
  checks: a correct answer worded differently can be missed, a wrong answer containing the expected
  words passes. They are **not** proof of factual correctness.
- **Latency**: median and p95 (p99 only with at least 100 samples), per stage where the service
  reports it. Cold start (first reranker call, which includes model load) is reported separately
  from warm calls.

## Retrieval and reranking

Arms: `dense`, `sparse`, `hybrid` (RRF), `hybrid+rerank@10/20/50`, `hybrid+filter` (an oracle filter to
the relevant documents) and `hybrid+foreign-filter` (a filter excluding every relevant document).
The last is a check, not a quality arm: nothing outside the filter may ever be returned, and a
reranker may only reorder candidates retrieval returned (both counted as violations; any violation
is a hard failure). Reranked arms keep the rest of the baseline list behind the reranked head, so
every arm is scored over the same candidate pool.

Reranking is reported as a trade-off: quality before and after on the same candidates, the questions
it helped and hurt (by category), candidate recall, first-call (model load) and warm latency, and
resident memory when measurable. Nothing here changes a production default: reranking stays opt-in.

## Scenarios

`scenarios/scenarios.json` holds scenarios as ordered steps (`upload`, `replace` with outcome
`ready | processed | pending | failed`, `stage`, `restore`, `reprocess`, `delete_version`,
`delete_document`, `search`, `ask`, `converse`, `check_citations`, `check_messages`, `check_document`,
`rewrite_last_answer`, `fault`, `versions_of`). Each checked step lists expectations:

- **hard**: pipeline guarantees independent of any language model: status codes, which version a
  citation names, which text may reach the prompt (`sources_include/exclude`), that text from a
  stored answer or an injected document stays inside its block (`prompt_only_inside`), whether a
  stored citation resolves, which documents are searched, degradation reporting.
- **behaviour**: answer content (`answer_contains`, `answer_forbidden`, `abstain`). Exact with the
  scripted reader; reported as rates with a real model.

Each scenario gets fresh users, and anything a scenario patches in the shared application (a failing
embedding model, the scripted reader) is undone afterwards even if the scenario errors, so scenarios
cannot influence one another. The expected behaviour is
written from the documented product rules (activation policy, citation resolution, history policy),
not from observed output. Real-model runs skip steps that simulate provider faults or a misbehaving
model.

What the scripted reader can and cannot show: it proves the application rejects fabricated labels and
quotations, keeps history and injected text inside their blocks, never lets superseded text reach
the prompt, and resolves citations to the right version. It does **not** show that a real model
resists injection: a model that obeys an instruction inside a retrieved document and repeats words
that really are in that document produces a "supported" sentence, which the deterministic
validators cannot see. That is measured only with a real model (`rag_eval` adversarial category) and
remains a documented risk.

## Citation and evidence audit

Two questions are kept apart. *Valid*: the label was supplied, the passage exists and, in the live
pipeline, belongs to the caller and names the exact version; the application checks this and the
scenario gates verify it. *Supporting*: whether the cited passage states the claim; no program can
prove this for every language and claim type. `citation_audit` therefore raises **flags** for a
person to look at: a sentence without a citation, a label that was not supplied, a number (Bengali
and Latin digits equal), quotation or identifier that is not in the cited passages, or very low word
overlap with them. A flag is a reason to review, not an error; an unflagged sentence is not verified.

`--out` writes a **review template**: a reproducible sample (seeded; flagged answers first, the rest
stratified by category) with the question, answer, the supplied sources and empty judgement fields,
plus the rubric (`supported`, `citation_correct`, `numbers_and_names`). `reviewed` is 0 until a
person fills it in; this repository records no review results and invents no reviewer. No LLM judge
is used. If one is ever added it is an auxiliary signal only (it shares the generation model's
blind spots, is sensitive to prompt wording, favours fluent text and cannot be audited per claim),
must record its model and prompt version, and must never be the only correctness metric.

## Regression gates

`compare_runs` (also run by `suite --baseline`) separates three classes. A quality score never
overrides a hard failure.

| Class | Rule | Effect |
|---|---|---|
| **Hard** | every scenario hard check passes; where the answering model is scripted (exact) every behaviour check passes too; no scenario errors; no result outside an applied filter or outside the retrieved candidates; no embedding failure; schema version matches | exit 1 |
| **Not skippable** (with a baseline) | a baseline section (scenarios, a dataset, an arm) missing from the run; fewer hard checks than the baseline overall or in any scenario; more skipped steps than the baseline; a scenario that disappeared; a filter check that returned nothing (it would pass vacuously) | exit 1 |
| **Quality (warning)** | an arm's Recall@1, MRR@10 or nDCG@10 fell by more than the baseline's own 95% half-width (0.10 when it has none; 0.005 for the exactly reproducible deterministic profile); with a real model the scenario behaviour pass rate fell | printed; exit 2 only with `--strict` |
| **Performance (warning)** | a median latency grew by more than 50% and by more than 5 ms | printed; exit 2 only with `--strict` |

Quality is compared only when profile, dataset versions and hashes and every recorded setting
match; otherwise the report says *not comparable* instead of showing a number, and `--strict` treats
that as exit 2 (a strict comparison that compared nothing is not a pass). The thresholds come from the
sample sizes above (a baseline's own noise) and are deliberately conservative; they are not claims
about acceptable quality. Re-baseline deliberately: regenerate with `--anonymise` (it strips the
machine, the time and the git state), review the diff and say why in the commit.

Every scenario step that declares an expectation must produce at least one check, expectation keys
are validated per operation when the dataset loads (a misplaced or misspelt key is an error, not a
check that never runs), and version names are unique within a scenario so that "cited version v1"
cannot be satisfied by another document's first version.

CI runs `tests/integration/test_evaluation_scenarios.py` (every scenario is a test),
`tests/unit/test_evaluation_framework.py` and the deterministic suite against the committed
baseline (`python -m evaluation.suite --profile deterministic --baseline
evaluation/baselines/deterministic.json`, using the job's PostgreSQL and Redis service containers).
Real-model evaluations need `--profile models` and the cached models; they never run in CI.

## Data handling

Only the synthetic datasets are read. Reports contain dataset content, metrics and run metadata
(commit, versions, hardware, settings by name, the reproducing command rebuilt from a fixed list of
options); they never contain prompts, secrets, provider URLs, API keys or local paths. Prompts are held in memory for the checks and discarded. Runs are bounded:
fixed datasets, one pass per question, `--rag-limit`, no loops. Document text goes to a generation
model only when `--llm-model` is given, and then to the configured provider (a local Ollama server
by default); use a hosted provider only if you accept that the synthetic texts leave the machine.

## Known limits and gaps

- Small datasets: one question is 1 to 3 points; categories of a few questions are anecdotal; Bangla
  coverage is narrow; passages are short, so chunk boundaries and truncation are barely exercised.
- Author-written data, one author, no inter-annotator agreement; the answer-generation model and the
  validators were tuned while looking at the answer questions (no untouched answer test split).
- Scripted-reader results measure the application's gates, not model robustness; real injection
  resistance is measured with a real model only, on few attacks.
- Hardware: the recorded real-model numbers are from one CPU-only Windows desktop; a GPU run was not
  measured here. Latency of a hosted provider is not covered.
- Concurrency: one request at a time. Throughput under load is not evaluated.
- Conversation history truncation is measured by the Phase 10 benchmark (the token budget fits one of
  four offered turns at its maximum); only the previous question extends a short follow-up's
  retrieval, so a follow-up that refers to an earlier topic after an unrelated turn is not resolved.

## Recorded results (Phase 12)

Measured on one machine, once each (the reranker latencies come from 87 and 33 queries per depth, so
they are medians over those, not a load test). Environment: Windows-11-10.0.26200-SP0, AMD64 Family 25 Model 33 Stepping 0, AuthenticAMD, 12 logical CPUs, cpu only (torch 2.14.1+cpu, sentence-transformers 6.1.0, Python 3.12.13); commit 4d2237d6cb (working tree not clean); one request at a time; embedding model intfloat/multilingual-e5-small @ 614241f622; reranker BAAI/bge-reranker-v2-m3 @ 953dc6f6f8 on CPU; generation model qwen3.5:9b through a local Ollama server. Nothing here is a claim about other
data, hardware or load. The files are `evaluation/results/phase12_models_qwen3.5_9b.json` (this run, `python -m
evaluation.suite --profile models --llm-model qwen3.5:9b`) and `evaluation/baselines/deterministic.json`
(the committed baseline of the deterministic profile).

### Retrieval, development corpus ("combined": retrieval-v1 + retrieval-lexical)

| Arm (77 answerable questions) | Recall@1 | Recall@5 | MRR@10 (95% CI) | nDCG@10 | median ms |
|---|---:|---:|---:|---:|---:|
| dense | 0.896 | 1.000 | 0.940 (0.90-0.98) | 0.955 | 24 |
| sparse | 0.805 | 0.857 | 0.829 (0.75-0.90) | 0.836 | 6 |
| hybrid | 0.883 | 1.000 | 0.926 (0.88-0.97) | 0.944 | 29 |
| hybrid+filter | 0.961 | 1.000 | 0.978 (0.95-1.00) | 0.984 | 31 |
| hybrid+rerank@10 | 0.961 | 1.000 | 0.981 (0.95-1.00) | 0.986 | 1123 |
| hybrid+rerank@20 | 0.961 | 0.987 | 0.976 (0.94-1.00) | 0.982 | 2144 |
| hybrid+rerank@50 | 0.961 | 0.987 | 0.976 (0.94-1.00) | 0.981 | 5301 |

### Retrieval, held-out corpus

| Arm (30 answerable questions) | Recall@1 | Recall@5 | MRR@10 (95% CI) | nDCG@10 | median ms |
|---|---:|---:|---:|---:|---:|
| dense | 1.000 | 1.000 | 1.000 (1.00-1.00) | 1.000 | 25 |
| sparse | 0.800 | 0.800 | 0.800 (0.63-0.93) | 0.800 | 6 |
| hybrid | 0.933 | 1.000 | 0.961 (0.90-1.00) | 0.971 | 27 |
| hybrid+filter | 0.967 | 1.000 | 0.983 (0.95-1.00) | 0.988 | 27 |
| hybrid+rerank@10 | 1.000 | 1.000 | 1.000 (1.00-1.00) | 1.000 | 1068 |
| hybrid+rerank@20 | 1.000 | 1.000 | 1.000 (1.00-1.00) | 1.000 | 1988 |
| hybrid+rerank@50 | 1.000 | 1.000 | 1.000 (1.00-1.00) | 1.000 | 2351 |

On the held-out set the dense retriever alone is perfect (30 of 30) while hybrid fusion leaves two
questions off rank 1. The lexical retriever's weaker ordering costs a little there; its value showed
in the earlier Phase 7 evaluation (exact words and identifiers). Differences of a question or two are
noise at these sizes.

### Reranking: quality against cost

Candidate recall before reranking is 1.00 at 10 candidates already (every answerable
question has a relevant passage among the first ten hybrid results), so reranking can only reorder, and
deeper candidate lists add cost, not recall.

Development corpus, hybrid against hybrid + rerank@10: Recall@1 0.883 to 0.961, MRR@10 0.926 to 0.981.
Paired change over the same 77 questions: Recall@1 +0.078 (95% CI +0.000 to +0.156), MRR@10 +0.055 (95% CI +0.009 to +0.105). At 50
candidates: MRR@10 +0.050 (95% CI -0.002 to +0.103) (no better than 10). Held-out: Recall@1 0.933 to 1.000.

| Candidates reranked | Warm median / p95 ms | Resident memory after (MB) |
|---:|---:|---:|
| 10 | 1098 / 1301 | 3795.2 |
| 20 | 2116 / 2481 | 3783.2 |
| 50 | 5270 / 6048 | 3781.0 |
First call (cold, includes loading the model from the local cache): 6233 ms, of which 4906 ms model load.

- combined: reranking the top 20 improved the rank of 8 questions and worsened 2 (worsened: bi-01, pa-07); by category bangla_inflection +0/-1, confusable +2/-0, cross_lingual +3/-0, mixed +1/-0, paraphrase +1/-1, punctuation_unicode +1/-0.
- heldout: reranking the top 20 improved the rank of 2 questions and worsened 0 (worsened: none); by category english +2/-0.

Reading: on this data reranking 10 candidates is the best trade-off (a median of 1.1 s per query on CPU,
3.8 GB resident, 6.2 s the first time); 20 and 50 candidates cost
1.9 and 4.8 times as much for no gain; it made some questions worse, so it is not a free
improvement. Reranking stays opt-in. A GPU was not measured here.

### Comparison with the earlier recorded results

The earlier tools' recorded results (`results/hybrid_e5-small_database.json`, README Phase 7 table) were
produced with fixed chunk ids. Run again through the new harness with those ids (`--legacy-ids`) the
dense, lexical and hybrid arms reproduce them exactly; with new ids the dense arm is identical and the
other two move by one question (Recall@1 0.896 to 0.883 for hybrid; equal-score ties break by chunk id).
That one-question sensitivity is the noise floor of these datasets.

| Same ids as the earlier tools | Recall@1 | MRR@10 | nDCG@10 |
|---|---:|---:|---:|
| dense | 0.896 | 0.940 | 0.955 |
| sparse | 0.805 | 0.831 | 0.838 |
| hybrid | 0.896 | 0.931 | 0.948 |

### Scenarios (versions, citations, history, abstention, injection, faults, isolation)

Deterministic profile (scripted reader, exact): 34 scenarios run, 1 skipped, 0 errored; hard checks 169/169; behaviour checks 38/38.

| Category | Scenarios | Hard checks failed / total | Behaviour passed / total |
|---|---:|---:|---:|
| abstention | 3 | 0 / 7 | 4 / 4 |
| citation | 6 | 0 / 38 | 2 / 2 |
| fault | 4 | 0 / 14 | 2 / 2 |
| history | 7 | 0 / 36 | 10 / 10 |
| injection | 3 | 0 / 6 | 5 / 5 |
| isolation | 2 | 0 / 20 | 2 / 2 |
| version | 9 | 0 / 48 | 13 / 13 |

Real generation model (`qwen3.5:9b`): 31 scenarios run, 4 skipped, 0 errored; hard checks 159/159; behaviour checks 36/36.

| Category | Scenarios | Hard checks failed / total | Behaviour passed / total |
|---|---:|---:|---:|
| abstention | 4 | 0 / 9 | 5 / 5 |
| citation | 6 | 0 / 38 | 2 / 2 |
| fault | 2 | 0 / 6 | 2 / 2 |
| history | 7 | 0 / 36 | 10 / 10 |
| injection | 1 | 0 / 2 | 2 / 2 |
| isolation | 2 | 0 / 20 | 2 / 2 |
| version | 9 | 0 / 48 | 13 / 13 |

Behaviour checks are rates with a real model; the hard checks are pipeline guarantees and must be
100%.

### Answers (`rag-answer-eval`, 48 questions, real model)

48 questions (38 answerable, 10 unanswerable); answered 0.974, fact recall 0.947, abstained correctly 0.9, citation precision 0.975, label validity 1.0, citations matching the database 1.0, forbidden-string rate 0.0, provider errors 0; latency median / p95 2893 / 3981 ms (first request 3239 ms), mean input tokens 830.0.

Citation audit of these answers: 6 of 38 answered questions were flagged for review
({'low_word_overlap_with_cited_source': 4, 'no_citation': 2}); flags are reasons to read an answer, not errors. A review
template of 20 answers (seeded, flagged first) is in `results/citation_review_template_qwen3.5_9b.json`; no
review has been done, and its judgement fields are empty. The earlier faithfulness review in the project
documentation is a single-author reading and is not repeated here.

The deterministic profile's answer section (hashing retrieval and a scripted reader) is a harness
check: its fact recall of 0.816 and abstention accuracy of
0.1 describe that reader, not any model.

**Run-to-run variation.** An earlier run of the identical configuration the same day (generation temperature
0.1, so not deterministic) gave abstention accuracy 1.0, citation precision 1.0 and fact recall
0.947; this run gave 0.9, 0.975 and 0.947. One
unanswerable question answered in one run and not the other: differences of this size between model runs
are noise, and any single run overstates how certain an abstention number is. Repeat the run and report
the spread before drawing a conclusion from a few points.
