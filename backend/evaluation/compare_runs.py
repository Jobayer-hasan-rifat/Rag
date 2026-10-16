"""Compare an evaluation run with a baseline and apply the regression gates.

    python -m evaluation.compare_runs BASELINE.json CANDIDATE.json [--strict] [--json OUT]

Exit code 0: every hard gate holds (warnings may be printed); 1: a hard gate failed; 2: no hard
gate failed but there are warnings and `--strict` was given.

Three classes, deliberately kept apart:

HARD gates (never overridden by any quality score)
  - scenario `hard` checks all pass, behaviour checks pass where the answering model is scripted
    (exact), and no scenario errored;
  - no retrieval arm returned a result outside an applied document filter or reranked in a
    candidate that retrieval did not return, a filter check did not pass vacuously (it returned
    results), and there were no embedding failures;
  - with a baseline, a gate cannot be passed by not running it: a missing section, dataset, arm or
    scenario, fewer hard checks than the baseline (overall or per scenario) or more skipped steps is
    a failure;
  - the run itself is well formed (schema version).

QUALITY warnings (small samples are noisy, so these never fail a run)
  - an arm's Recall@1, MRR@10 or nDCG@10 fell by more than a tolerance. The tolerance is the
    baseline's own 95% bootstrap half-width when it has one (the noise the baseline itself
    carried), otherwise 0.10; for the deterministic profile, whose numbers are exactly
    reproducible, it is 0.005, because any change there means ranking behaviour changed.
  - the scenario `behaviour` pass rate fell (with a real model; exact where it is scripted).
  - Quality is compared only when profile, dataset versions and hashes and every recorded
    setting match; otherwise the comparison is reported as not comparable instead of as a number,
    and `--strict` counts that as exit 2.

PERFORMANCE warnings (latency is noisy)
  - a median latency grew by more than 50% and by more than 5 ms.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
QUALITY_METRICS = ("recall_at_1", "mrr_at_10", "ndcg_at_10")
FALLBACK_TOLERANCE = 0.10
DETERMINISTIC_TOLERANCE = 0.005
LATENCY_RATIO = 1.5
LATENCY_ABSOLUTE_MS = 5.0


def _scenario_ids(run: dict[str, Any]) -> set[str]:
    return {o["id"] for o in run.get("scenarios", {}).get("outcomes", []) if not o.get("skipped")}


def _hard_gates_of_scenarios(
    scenarios: dict[str, Any], baseline: dict[str, Any] | None, candidate: dict[str, Any]
) -> list[str]:
    failures: list[str] = []
    if scenarios["hard_failed"]:
        failures.append(f"{scenarios['hard_failed']} scenario hard check(s) failed")
    if scenarios.get("behaviour_gate_failed"):
        failures.append(
            f"{scenarios['behaviour_gate_failed']} behaviour check(s) failed where the answering "
            "model is scripted (exact)"
        )
    if scenarios["scenario_errors"]:
        failures.append(f"{scenarios['scenario_errors']} scenario(s) errored")
    if scenarios["scenarios_run"] == 0:
        failures.append("no scenario ran")
    base = (baseline or {}).get("scenarios")
    if base and baseline is not None and baseline.get("profile") == candidate.get("profile"):
        missing = _scenario_ids(baseline) - _scenario_ids(candidate)
        if missing:
            failures.append(f"scenarios missing from the run: {sorted(missing)[:5]}")
        # a run that checks less than the baseline did is not a pass, whatever it reports
        if scenarios["hard_checks"] < base["hard_checks"]:
            failures.append(
                f"fewer hard checks ran ({scenarios['hard_checks']} < {base['hard_checks']})"
            )
        if scenarios.get("steps_skipped", 0) > base.get("steps_skipped", 0):
            failures.append(
                f"more steps were skipped ({scenarios.get('steps_skipped')} > "
                f"{base.get('steps_skipped', 0)})"
            )
        before = {o["id"]: o["hard_checks"] for o in base["outcomes"] if not o.get("skipped")}
        for outcome in scenarios["outcomes"]:
            if not outcome.get("skipped") and outcome["hard_checks"] < before.get(outcome["id"], 0):
                failures.append(f"{outcome['id']}: fewer hard checks than the baseline")
    return failures


def _hard_gates_of_retrieval(
    candidate: dict[str, Any], baseline: dict[str, Any] | None
) -> list[str]:
    failures: list[str] = []
    for name, dataset in candidate.get("retrieval", {}).items():
        if dataset.get("embedding_failures") is None:
            failures.append(f"{name}: embedding_failures missing")
        elif dataset["embedding_failures"]:
            failures.append(f"{name}: {dataset['embedding_failures']} embedding failure(s)")
        for arm, entry in dataset.get("arms", {}).items():
            violations = entry.get("filter_or_candidate_violations")
            if violations is None:
                failures.append(f"{name}/{arm}: violation count missing")
            elif violations:
                failures.append(
                    f"{name}/{arm}: {violations} result(s) outside the filter or the "
                    "retrieved candidates"
                )
            if entry.get("empty_results"):
                failures.append(
                    f"{name}/{arm}: {entry['empty_results']} filtered question(s) returned nothing "
                    "(the filter check would pass vacuously)"
                )
            if entry.get("kind") == "check" and not entry.get("results_returned"):
                failures.append(f"{name}/{arm}: the check returned nothing, so it proves nothing")
    if baseline is not None:
        for name, base_dataset in baseline.get("retrieval", {}).items():
            cand_dataset = candidate.get("retrieval", {}).get(name)
            if cand_dataset is None:
                failures.append(f"retrieval dataset {name!r} is missing from the run")
                continue
            absent = set(base_dataset.get("arms", {})) - set(cand_dataset.get("arms", {}))
            if absent:
                failures.append(f"{name}: arms missing from the run: {sorted(absent)}")
    return failures


def hard_gates(candidate: dict[str, Any], baseline: dict[str, Any] | None) -> list[str]:
    """Reasons the candidate must not be accepted (empty list: every hard gate holds).

    With a baseline, a section the baseline has but the candidate lacks (scenarios, a dataset, an
    arm) is itself a failure: a gate cannot be passed by not running it."""
    failures: list[str] = []
    if candidate.get("schema_version") != SCHEMA_VERSION:
        failures.append(
            f"schema_version {candidate.get('schema_version')!r} is not {SCHEMA_VERSION}"
        )
    if baseline is not None and "scenarios" in baseline and "scenarios" not in candidate:
        failures.append("the scenario section is missing from the run")
    if baseline is not None and "retrieval" in baseline and "retrieval" not in candidate:
        failures.append("the retrieval section is missing from the run")
    scenarios = candidate.get("scenarios")
    if scenarios is not None:
        failures += _hard_gates_of_scenarios(scenarios, baseline, candidate)
    failures += _hard_gates_of_retrieval(candidate, baseline)
    return failures


def _comparable(base: dict[str, Any], cand: dict[str, Any]) -> str | None:
    """None when quality numbers may be compared, otherwise why not."""
    if base.get("profile") != cand.get("profile"):
        return f"profile differs ({base.get('profile')} vs {cand.get('profile')})"
    base_data = {d["name"]: (d["version"], d["sha256"]) for d in base.get("datasets", [])}
    cand_data = {d["name"]: (d["version"], d["sha256"]) for d in cand.get("datasets", [])}
    changed = sorted(n for n in base_data if base_data[n] != cand_data.get(n))
    if changed:
        return f"dataset content or version changed: {changed}"
    a, b = base.get("settings", {}), cand.get("settings", {})
    differing = sorted(k for k in a.keys() | b.keys() if a.get(k) != b.get(k))
    if differing:
        return f"settings differ: {differing[:8]}"
    return None


def quality_warnings(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> tuple[list[str], str | None]:
    reason = _comparable(baseline, candidate)
    if reason:
        return [], reason
    deterministic = candidate.get("profile") == "deterministic"
    warnings: list[str] = []
    for name, dataset in candidate.get("retrieval", {}).items():
        base_dataset = baseline.get("retrieval", {}).get(name)
        if not base_dataset:
            continue
        for arm, entry in dataset.get("arms", {}).items():
            base_overall = base_dataset.get("arms", {}).get(arm, {}).get("overall")
            if not base_overall or "overall" not in entry:
                continue
            for metric in QUALITY_METRICS:
                before, after = base_overall.get(metric), entry["overall"].get(metric)
                if before is None or after is None:
                    continue
                interval = base_overall.get(f"{metric}_ci95")
                tolerance = (
                    DETERMINISTIC_TOLERANCE
                    if deterministic
                    else (interval[1] - interval[0]) / 2 if interval else FALLBACK_TOLERANCE
                )
                if before - after > tolerance:
                    warnings.append(
                        f"{name}/{arm} {metric}: {before:.3f} -> {after:.3f} "
                        f"(tolerance {tolerance:.3f})"
                    )
    base_s, cand_s = baseline.get("scenarios"), candidate.get("scenarios")
    if base_s and cand_s and base_s["behaviour_checks"] and cand_s["behaviour_checks"]:
        before = base_s["behaviour_passed"] / base_s["behaviour_checks"]
        after = cand_s["behaviour_passed"] / cand_s["behaviour_checks"]
        if before - after > (0.0 if deterministic else FALLBACK_TOLERANCE):
            warnings.append(f"scenario behaviour pass rate: {before:.3f} -> {after:.3f}")
    return warnings, None


def performance_warnings(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if baseline.get("profile") != candidate.get("profile"):
        return warnings
    for name, dataset in candidate.get("retrieval", {}).items():
        base_dataset = baseline.get("retrieval", {}).get(name, {})
        for arm, entry in dataset.get("arms", {}).items():
            before = base_dataset.get("arms", {}).get(arm, {}).get("latency_ms", {}).get("median")
            after = entry.get("latency_ms", {}).get("median")
            if (
                before
                and after
                and after > before * LATENCY_RATIO
                and after - before > LATENCY_ABSOLUTE_MS
            ):
                warnings.append(f"{name}/{arm} median latency {before} ms -> {after} ms")
    return warnings


def compare(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    failures = hard_gates(candidate, baseline)
    quality, not_comparable = quality_warnings(baseline, candidate)
    return {
        "hard_failures": failures,
        "quality_warnings": quality,
        "quality_not_comparable": not_comparable,
        "performance_warnings": performance_warnings(baseline, candidate),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--strict", action="store_true", help="exit 2 when there are warnings")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    outcome = compare(baseline, candidate)
    for title, key in (
        ("HARD GATE FAILURES", "hard_failures"),
        ("quality warnings", "quality_warnings"),
        ("performance warnings", "performance_warnings"),
    ):
        for line in outcome[key]:
            print(f"{title}: {line}")
    if outcome["quality_not_comparable"]:
        print(f"quality not compared: {outcome['quality_not_comparable']}")
    if args.json:
        args.json.write_text(json.dumps(outcome, indent=2), encoding="utf-8")
    if outcome["hard_failures"]:
        sys.exit(1)
    if args.strict and (
        outcome["quality_warnings"]
        or outcome["performance_warnings"]
        or outcome["quality_not_comparable"]
    ):
        sys.exit(2)
    print("hard gates: all hold")


if __name__ == "__main__":
    main()
