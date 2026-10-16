"""Unit tests of the evaluation framework itself (no database, no model, no network)."""

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from app.llm.types import GenerationRequest
from evaluation import cards, citation_audit, compare_runs, runmeta, scripted, stats
from evaluation.dataset import DATA_DIR, HELDOUT_DIR, LEXICAL_DIR, EvalQuery, load_dataset
from evaluation.retrieval_matrix import candidate_recall, per_query_metrics, summarise_arm
from evaluation.scenario_runner import load_scenarios, prompt_regions, validate

BACKEND = Path(__file__).resolve().parents[2]


# --- statistics ---------------------------------------------------------------------------------


def test_wilson_interval_matches_known_values_and_needs_enough_trials() -> None:
    low, high = stats.wilson_interval(8, 10) or (0, 0)
    assert (round(low, 3), round(high, 3)) == (0.49, 0.943)
    assert stats.wilson_interval(0, 9) is None
    assert stats.wilson_interval(10, 10) == (0.7225, 1.0)


def test_bootstrap_interval_is_reproducible_and_brackets_the_mean() -> None:
    values = [0.0, 1.0] * 20
    first = stats.bootstrap_mean_ci(values)
    assert first == stats.bootstrap_mean_ci(values)
    assert first is not None and first[0] < 0.5 < first[1]
    assert stats.bootstrap_mean_ci([1.0] * 9) is None


def test_paired_delta_uses_the_same_questions() -> None:
    delta, interval = stats.paired_bootstrap_delta([0.0] * 12, [1.0] * 12)
    assert delta == 1.0 and interval == (1.0, 1.0)
    with pytest.raises(ValueError, match="same length"):
        stats.paired_bootstrap_delta([1.0], [1.0, 2.0])


def test_tail_percentiles_are_reported_only_with_enough_samples() -> None:
    assert "p99" not in stats.latency_summary([1.0] * 99)
    assert stats.latency_summary([float(i) for i in range(100)])["p99"] == 98.0
    assert stats.latency_summary([]) == {"n": 0}


# --- dataset cards ------------------------------------------------------------------------------


def test_cards_are_computed_from_the_data() -> None:
    by_name = {c["name"]: c for c in cards.dataset_cards()}
    for name, directory in (
        ("retrieval-v1", DATA_DIR),
        ("retrieval-lexical", LEXICAL_DIR),
        ("retrieval-heldout", HELDOUT_DIR),
    ):
        dataset = load_dataset(directory)
        card = by_name[name]
        assert card["questions"] == len(dataset.queries)
        assert card["answerable"] + card["unanswerable"] == card["questions"]
        assert card["passages"] == len(dataset.passages)
        assert len(card["sha256"]) == 64 and card["limitations"] and card["origin"]
    assert {"rag-answer-eval", "pipeline-scenarios"} <= set(by_name)
    assert by_name["pipeline-scenarios"]["scenarios"] == len(load_scenarios()["scenarios"])


def test_card_hashes_change_when_the_data_changes(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text("{}")
    first = cards._sha256(tmp_path / "a.json")
    (tmp_path / "a.json").write_text("{ }")
    assert cards._sha256(tmp_path / "a.json") != first


# --- the scenario dataset ------------------------------------------------------------------------


def test_the_scenario_dataset_validates_and_every_scenario_checks_something() -> None:
    data = load_scenarios()
    ids = [s["id"] for s in data["scenarios"]]
    assert len(ids) == len(set(ids)) >= 30
    for scenario in data["scenarios"]:
        checks = [step for step in scenario["steps"] if step.get("expect")]
        assert checks, f"{scenario['id']} checks nothing"
        assert any("hard" in step["expect"] for step in checks), scenario["id"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda d: d["scenarios"][0]["steps"].append({"op": "nope"}), "unknown op"),
        (lambda d: d["scenarios"][0]["steps"][0].update(text="missing"), "unknown text"),
        (lambda d: d["scenarios"][0].update(id=d["scenarios"][1]["id"]), "duplicate"),
        (lambda d: d["scenarios"][0]["steps"][1].update(script="magic"), "unknown script"),
        (
            lambda d: d["scenarios"][0]["steps"][1]["expect"]["hard"].update(bogus=1),
            "unknown or empty hard expectation",
        ),
        (
            lambda d: d["scenarios"][0]["steps"].insert(
                0, {"op": "stage", "version": "v9", "stage": "embed"}
            ),
            "unknown version",
        ),
    ],
)
def test_a_malformed_scenario_dataset_is_refused(mutation: Any, message: str) -> None:
    data = copy.deepcopy(load_scenarios())
    mutation(data)
    with pytest.raises(ValueError, match=message):
        validate(data)


def test_prompt_regions_separate_system_sources_and_history() -> None:
    user = (
        "<conversation_history>\n<turn>H-TEXT</turn>\n</conversation_history>\n\n"
        '<question>\nQ\n</question>\n\n<sources>\n<source id="S1">\nS-TEXT\n</source>\n</sources>'
    )
    regions = prompt_regions("SYS", user)
    assert "H-TEXT" in regions["conversation_history"] and "H-TEXT" not in regions["sources"]
    assert "S-TEXT" in regions["sources"] and "S-TEXT" not in regions["other"]
    assert regions["system"] == "SYS" and "Q" in regions["other"]


# --- scripted models -----------------------------------------------------------------------------


def _request(question: str, *sources: str, history: str = "") -> GenerationRequest:
    blocks = "\n".join(
        f'<source id="S{i}" document="d">\n{t}\n</source>' for i, t in enumerate(sources, 1)
    )
    user = f"{history}<question>\n{question}\n</question>\n\n<sources>\n{blocks}\n</sources>"
    return GenerationRequest(system="rules", user=user, max_output_tokens=100)


def _decode(text: str) -> dict[str, Any]:
    return json.loads(text)  # type: ignore[no-any-return]


def test_best_overlap_answers_with_the_best_sentence_and_cites_its_source() -> None:
    out = _decode(
        scripted.best_overlap(
            _request(
                "When are seedlings transplanted?",
                "Fish live in rivers.",
                "Rice is grown. Seedlings are transplanted in July.",
            )
        )
    )
    assert out["answerable"] and out["citations"] == ["S2"]
    assert out["answer"] == "Seedlings are transplanted in July. [S2]"


def test_best_overlap_abstains_when_nothing_overlaps_and_handles_bangla() -> None:
    assert not _decode(scripted.best_overlap(_request("zebra giraffe", "Rice is grown.")))[
        "answerable"
    ]
    out = _decode(
        scripted.best_overlap(_request("ইলিশ মাছ কখন ধরা পড়ে?", "ইলিশ মাছ বর্ষাকালে ধরা পড়ে।"))
    )
    assert out["answerable"] and "বর্ষাকালে" in out["answer"]


def test_history_echo_repeats_the_stored_answer_and_misbehaving_scripts_are_listed() -> None:
    history = "<conversation_history>\n<turn>\n<user>q</user>\n<assistant>Spacing is 99 centimetres.</assistant>\n</turn>\n</conversation_history>\n\n"  # noqa: E501
    out = _decode(
        scripted.history_echo(_request("and spacing?", "Rice is grown.", history=history))
    )
    assert "99" in out["answer"] and out["citations"] == ["S1"]
    assert {"fabricate_label", "fabricate_quote", "timeout"} <= scripted.SIMULATED_FAULTS
    assert "best_overlap" not in scripted.SIMULATED_FAULTS


# --- citation audit ------------------------------------------------------------------------------


def test_the_audit_flags_what_a_person_should_check_and_not_more() -> None:
    sources = [
        "Seedlings are transplanted in July. Ticket ERR-4721 means bloat. "
        "Spacing is 20 centimetres."
    ]
    clean = citation_audit.audit_sentences("Seedlings are transplanted in July. [S1]", sources)
    assert [a.flags for a in clean] == [[]]  # a marker after the full stop belongs to its sentence
    flags: dict[str, str] = {
        "Seedlings need 99 centimetres. [S1]": "number_not_in_cited_source",
        'The manual says "plant in March". [S1]': "quotation_not_in_cited_source",
        "Ticket ERR-9999 means bloat. [S1]": "identifier_not_in_cited_source",
        "Seedlings are transplanted in July.": "no_citation",
        "Seedlings are transplanted in July. [S4]": "label_not_supplied",
        "Aircraft engines are unrelated to this. [S1]": "low_word_overlap_with_cited_source",
    }
    for sentence, flag in flags.items():
        found = citation_audit.audit_sentences(sentence, sources)
        assert flag in [f for a in found for f in a.flags], sentence


def test_bengali_digits_are_compared_as_latin_digits() -> None:
    sources = ["বীজতলায় ২০ সেন্টিমিটার দূরত্ব রাখা হয়।"]
    ok = citation_audit.audit_sentences("বীজতলায় 20 সেন্টিমিটার দূরত্ব রাখা হয়। [S1]", sources)
    bad = citation_audit.audit_sentences("বীজতলায় ৩০ সেন্টিমিটার দূরত্ব রাখা হয়। [S1]", sources)
    assert not [f for a in ok for f in a.flags]
    assert "number_not_in_cited_source" in [f for a in bad for f in a.flags]


def _stored(results: int) -> list[dict[str, Any]]:
    return [
        {
            "id": f"q{i}",
            "category": "english" if i % 2 else "bangla",
            "language": "en",
            "answered": True,
            "error": None,
            "answer": "Seedlings are transplanted in July. [S1]" if i % 3 else "No citation here.",
            "source_passages": ["p1"],
        }
        for i in range(results)
    ]


def test_the_review_template_is_reproducible_and_contains_no_judgements() -> None:
    texts = {"p1": "Seedlings are transplanted in July."}
    first = citation_audit.review_template(_stored(30), texts, {}, sample=10, seed=7)
    second = citation_audit.review_template(_stored(30), texts, {}, sample=10, seed=7)
    assert first == second
    assert first["reviewed"] == 0 and first["reviewer"] is None and first["sample_size"] == 10
    assert all(item["review"]["supported"] is None for item in first["items"])
    assert set(first["rubric"]) >= {"supported", "citation_correct", "numbers_and_names"}
    flagged = [i for i in first["items"] if i["automatic_flags"]]
    assert flagged, "flagged answers are sampled first"


# --- retrieval matrix helpers ---------------------------------------------------------------------


def _query(qid: str, relevant: tuple[str, ...], neutral: tuple[str, ...] = ()) -> EvalQuery:
    return EvalQuery(qid, "english", "q", relevant, neutral)


def test_unanswerable_questions_are_left_out_of_rank_metrics() -> None:
    queries = [_query("a", ("p1",)), _query("b", ())]
    summary = summarise_arm(queries, {"a": ["p1", "p2"], "b": ["p9"]})
    assert summary["overall"]["questions"] == 1 and summary["overall"]["recall_at_1"] == 1.0
    assert set(summary["per_question"]) == {"a"}


def test_neutral_passages_are_neither_hits_nor_misses() -> None:
    query = _query("a", ("p2",), ("p1",))
    assert per_query_metrics(query, ["p1", "p2"])["recall_at_1"] == 1.0


def test_candidate_recall_counts_relevant_passages_among_the_first_candidates() -> None:
    queries = [_query("a", ("p3",)), _query("b", ("p1",))]
    rankings = {"a": ["p1", "p2", "p3"], "b": ["p1", "p2", "p3"]}
    assert candidate_recall(queries, rankings, 1)["any_relevant_in_candidates"] == 0.5
    assert candidate_recall(queries, rankings, 3)["any_relevant_in_candidates"] == 1.0


# --- regression gates -----------------------------------------------------------------------------


def _run(**overrides: Any) -> dict[str, Any]:
    arm = {
        "overall": {"recall_at_1": 0.8, "mrr_at_10": 0.85, "ndcg_at_10": 0.86},
        "latency_ms": {"median": 10.0},
        "filter_or_candidate_violations": 0,
    }
    run: dict[str, Any] = {
        "schema_version": compare_runs.SCHEMA_VERSION,
        "profile": "deterministic",
        "settings": {"embedding_model": "m", "embedding_model_revision": "r"},
        "datasets": [{"name": "retrieval-v1", "version": 1, "sha256": "x"}],
        "retrieval": {"combined": {"embedding_failures": 0, "arms": {"hybrid": arm}}},
        "scenarios": {
            "hard_checks": 8,
            "hard_failed": 0,
            "behaviour_gate_failed": 0,
            "steps_skipped": 0,
            "scenario_errors": 0,
            "scenarios_run": 2,
            "behaviour_checks": 10,
            "behaviour_passed": 10,
            "outcomes": [
                {"id": "a", "skipped": None, "hard_checks": 4},
                {"id": "b", "skipped": None, "hard_checks": 4},
            ],
        },
    }
    run.update(overrides)
    return run


def test_a_clean_run_has_no_findings() -> None:
    outcome = compare_runs.compare(_run(), _run())
    assert outcome == {
        "hard_failures": [],
        "quality_warnings": [],
        "quality_not_comparable": None,
        "performance_warnings": [],
    }


def test_hard_failures_are_reported_whatever_the_quality() -> None:
    better = _run()
    better["retrieval"]["combined"]["arms"]["hybrid"]["overall"]["recall_at_1"] = 1.0
    better["scenarios"]["hard_failed"] = 1
    better["retrieval"]["combined"]["arms"]["hybrid"]["filter_or_candidate_violations"] = 2
    failures = compare_runs.hard_gates(better, _run())
    assert any("hard check" in f for f in failures) and any(
        "outside the filter" in f for f in failures
    )


def test_a_dropped_scenario_and_errors_are_hard_failures() -> None:
    candidate = _run()
    candidate["scenarios"]["outcomes"] = [{"id": "a", "skipped": None, "hard_checks": 4}]
    candidate["scenarios"]["scenario_errors"] = 1
    failures = compare_runs.hard_gates(candidate, _run())
    assert any("missing" in f for f in failures) and any("errored" in f for f in failures)


def test_quality_drops_warn_with_a_tolerance_and_never_fail() -> None:
    candidate = _run(profile="models")
    baseline = _run(profile="models")
    arm = baseline["retrieval"]["combined"]["arms"]["hybrid"]["overall"]
    arm["recall_at_1_ci95"] = [0.7, 0.9]  # half-width 0.1
    candidate["retrieval"]["combined"]["arms"]["hybrid"]["overall"]["recall_at_1"] = 0.72
    assert compare_runs.compare(baseline, candidate)["quality_warnings"] == []
    candidate["retrieval"]["combined"]["arms"]["hybrid"]["overall"]["recall_at_1"] = 0.65
    outcome = compare_runs.compare(baseline, candidate)
    assert len(outcome["quality_warnings"]) == 1 and outcome["hard_failures"] == []


def test_the_deterministic_profile_is_held_to_an_exact_tolerance() -> None:
    candidate = _run()
    candidate["retrieval"]["combined"]["arms"]["hybrid"]["overall"]["mrr_at_10"] = 0.84
    assert compare_runs.compare(_run(), candidate)["quality_warnings"]


def test_quality_is_not_compared_across_profiles_or_changed_datasets() -> None:
    models = _run(profile="models")
    assert compare_runs.compare(_run(), models)["quality_not_comparable"]
    changed = _run(datasets=[{"name": "retrieval-v1", "version": 1, "sha256": "y"}])
    outcome = compare_runs.compare(_run(), changed)
    assert outcome["quality_warnings"] == [] and "dataset" in outcome["quality_not_comparable"]


def test_latency_warns_only_beyond_noise() -> None:
    slow = _run()
    slow["retrieval"]["combined"]["arms"]["hybrid"]["latency_ms"]["median"] = 12.0
    assert compare_runs.compare(_run(), slow)["performance_warnings"] == []
    slow["retrieval"]["combined"]["arms"]["hybrid"]["latency_ms"]["median"] = 30.0
    assert compare_runs.compare(_run(), slow)["performance_warnings"]


# --- run metadata and the CI constraint ---------------------------------------------


def test_run_metadata_never_carries_secrets_or_urls() -> None:
    forbidden = {"llm_api_key", "jwt_secret_key", "database_url", "redis_url", "celery_broker_url"}
    assert not forbidden & set(runmeta._SETTING_NAMES)
    assert runmeta.SCHEMA_VERSION == compare_runs.SCHEMA_VERSION


def test_the_deterministic_tooling_imports_no_ml_package() -> None:
    code = (
        "import sys, evaluation.cards, evaluation.compare_runs, evaluation.stats, "
        "evaluation.scripted, evaluation.citation_audit, evaluation.scenario_runner, "
        "evaluation.retrieval_matrix, evaluation.suite; "
        "bad = {'torch', 'sentence_transformers', 'transformers'} & set(sys.modules); "
        "sys.exit(1 if bad else 0)"
    )
    result = subprocess.run(  # noqa: S603  (fixed arguments, no user input)
        [sys.executable, "-c", code],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-500:]


def test_the_lightweight_ci_requirements_do_not_include_ml_packages() -> None:
    lines = (BACKEND / "requirements-dev.txt").read_text(encoding="utf-8").lower().splitlines()
    names = {line.split("==")[0].strip() for line in lines if "==" in line}
    assert not names & {"torch", "sentence-transformers", "transformers"}


def test_a_gate_cannot_be_passed_by_not_running_it() -> None:
    no_scenarios = _run()
    del no_scenarios["scenarios"]
    assert any("scenario section" in f for f in compare_runs.hard_gates(no_scenarios, _run()))
    no_dataset = _run()
    no_dataset["retrieval"] = {}
    assert any("missing" in f for f in compare_runs.hard_gates(no_dataset, _run()))
    no_arm = _run()
    no_arm["retrieval"]["combined"]["arms"] = {}
    assert any("arms missing" in f for f in compare_runs.hard_gates(no_arm, _run()))
    fewer = _run()
    fewer["scenarios"]["hard_checks"] = 7
    fewer["scenarios"]["outcomes"][0]["hard_checks"] = 3
    failures = compare_runs.hard_gates(fewer, _run())
    assert any("fewer hard checks ran" in f for f in failures)
    assert any("a: fewer hard checks" in f for f in failures)
    skipping = _run()
    skipping["scenarios"]["steps_skipped"] = 2
    assert any("skipped" in f for f in compare_runs.hard_gates(skipping, _run()))


def test_filter_checks_that_return_nothing_fail_instead_of_passing_vacuously() -> None:
    run = _run()
    run["retrieval"]["combined"]["arms"]["hybrid+filter"] = {
        "overall": {"recall_at_1": 1.0},
        "filter_or_candidate_violations": 0,
        "empty_results": 3,
    }
    run["retrieval"]["combined"]["arms"]["hybrid+foreign-filter"] = {
        "kind": "check",
        "results_returned": 0,
        "filter_or_candidate_violations": 0,
    }
    failures = compare_runs.hard_gates(run, None)
    assert any("vacuously" in f for f in failures) and any("proves nothing" in f for f in failures)
    missing = _run()
    del missing["retrieval"]["combined"]["arms"]["hybrid"]["filter_or_candidate_violations"]
    assert any("missing" in f for f in compare_runs.hard_gates(missing, None))


def test_exact_behaviour_failures_gate_with_the_scripted_model() -> None:
    run = _run()
    run["scenarios"]["behaviour_gate_failed"] = 1
    assert any("scripted" in f for f in compare_runs.hard_gates(run, None))


def test_any_changed_setting_makes_runs_not_comparable() -> None:
    other = _run(
        settings={"embedding_model": "m", "embedding_model_revision": "r", "hybrid_rrf_k": 30}
    )
    assert "hybrid_rrf_k" in (compare_runs.compare(_run(), other)["quality_not_comparable"] or "")


def test_percentiles_use_the_nearest_rank() -> None:
    assert stats.percentile([float(i) for i in range(1, 21)], 0.95) == 19.0
    assert stats.percentile([5.0], 0.99) == 5.0


def test_a_cited_passage_without_text_is_flagged() -> None:
    found = citation_audit.audit_sentences("Seedlings are transplanted in July. [S1]", [""])
    assert "cited_source_text_missing" in [f for a in found for f in a.flags]


def test_candidate_recall_uses_the_pool_reranking_receives() -> None:
    neutral = _query("a", ("p2",), ("p1",))
    # pool of the first 2 results as retrieved: p1 (neutral, set aside) and p2
    assert candidate_recall([neutral], {"a": ["p1", "p2"]}, 2)["any_relevant_in_candidates"] == 1.0
    assert candidate_recall([neutral], {"a": ["p1", "p2"]}, 1)["any_relevant_in_candidates"] == 0.0


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda d: d["scenarios"][0]["steps"].__setitem__(
                0, {**d["scenarios"][0]["steps"][0], "expect": {"hard": {"status": 1}}}
            ),
            "takes no expectations",
        ),
        (
            lambda d: d["scenarios"][2]["steps"][2]["expect"]["hard"].update(sources_exclude=["x"]),
            "unknown or empty",
        ),
        (
            lambda d: d["scenarios"][2]["steps"][2]["expect"].update(behaviour={"abstain": True}),
            "unknown or empty",
        ),
        (
            lambda d: d["scenarios"][0]["steps"].append(
                {"op": "upload", "doc": "x", "text": "rice.v1"}
            ),
            "used twice",
        ),
    ],
)
def test_expectations_that_would_never_run_are_refused(mutation: Any, message: str) -> None:
    data = copy.deepcopy(load_scenarios())
    mutation(data)
    with pytest.raises(ValueError, match=message):
        validate(data)


def test_citation_expectations_cannot_be_empty_or_misspelt() -> None:
    data = copy.deepcopy(load_scenarios())
    scenario = next(s for s in data["scenarios"] if s["id"].startswith("cit-01"))
    step = next(st for st in scenario["steps"] if st["op"] == "check_citations")
    step["expect"]["hard"]["citations"][0]["avialable"] = True
    with pytest.raises(ValueError, match="bad citation expectation"):
        validate(data)
    step["expect"]["hard"]["citations"] = []
    with pytest.raises(ValueError, match="empty"):
        validate(data)
