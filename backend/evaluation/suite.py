"""One command for the whole evaluation: retrieval matrix, scenarios, answers, citation audit.

    python -m evaluation.suite --profile deterministic --json out.json
    python -m evaluation.suite --profile models --llm-model qwen3.5:9b --json run.json
    python -m evaluation.suite --profile deterministic --baseline baselines/deterministic.json

Profiles (see evaluation/stack.py): `deterministic` needs Docker only (no model, no ML package) and
is exactly reproducible; `models` uses the cached real embedding model and reranker and, with
`--llm-model`, a real generation model. The run is bounded: fixed datasets, one pass per question
(`--repeat` only repeats retrieval timing), no network access beyond the configured provider.

The JSON report has a stable schema (`schema_version`), the run metadata needed to compare runs
fairly, and no secrets, prompts or private data: only synthetic dataset content.
"""

import argparse
import asyncio
import json
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from evaluation import citation_audit, compare_runs, rag_eval, runmeta
from evaluation.cards import dataset_cards
from evaluation.dataset import (
    HELDOUT_DIR,
    load_combined,
    load_dataset,
    load_rag_corpus,
    load_rag_questions,
)
from evaluation.rag_metrics import group_by, summarise
from evaluation.retrieval_matrix import evaluate_dataset
from evaluation.scenario_runner import ScenarioRunner
from evaluation.scenario_runner import summarise as summarise_scenarios
from evaluation.scripted import SCRIPTS
from evaluation.stack import PROFILES, Stack, open_stack
from evaluation.stats import latency_summary
from tests.helpers import create_user_in_db

SECTIONS = ("retrieval", "scenarios", "rag")


def _compact_retrieval(report: dict[str, Any], full: bool) -> dict[str, Any]:
    if full:
        return report
    for entry in report["arms"].values():
        entry.pop("per_question", None)
    return report


def run_retrieval(
    stack: Stack,
    full: bool,
    repeat: int,
    names: list[str],
    depths: tuple[int, ...],
    legacy_ids: bool,
) -> dict[str, Any]:
    available = {
        "combined": lambda: load_combined()[0],
        "heldout": lambda: load_dataset(HELDOUT_DIR),
    }
    datasets = {name: available[name]() for name in names}
    out: dict[str, Any] = {}
    for name, dataset in datasets.items():
        user = uuid.UUID(
            create_user_in_db(stack.database_url, email=f"{name}-matrix@eval.example.com")
        )
        started = time.perf_counter()
        report = asyncio.run(
            evaluate_dataset(
                stack.settings,
                name,
                dataset,
                user,
                repeat=repeat,
                rerank_depths=depths,
                legacy_ids=legacy_ids,
            )
        )
        report["wall_seconds"] = round(time.perf_counter() - started, 1)
        out[name] = _compact_retrieval(report, full)
    return out


def run_scenarios(stack: Stack) -> dict[str, Any]:
    runner = ScenarioRunner(stack)
    outcomes = runner.run_all()
    summary = summarise_scenarios(outcomes)
    latency: dict[str, list[float]] = {}
    for outcome in outcomes:
        for step in outcome.steps:
            if step.latency_ms is not None and step.op in ("ask", "converse", "search"):
                latency.setdefault(step.op, []).append(float(step.latency_ms))
    summary["latency_ms_by_op"] = {
        op: latency_summary(values) for op, values in sorted(latency.items())
    }
    by_turns: dict[int, list[int]] = {}
    for item in runner.usage:
        by_turns.setdefault(int(item["history_turns"] or 0), []).append(
            item["prompt_tokens_estimate"]
        )
    summary["history_usage"] = {
        str(turns): {
            "answers": len(sizes),
            "prompt_tokens_estimate_median": sorted(sizes)[len(sizes) // 2],
        }
        for turns, sizes in sorted(by_turns.items())
    }
    summary["outcomes"] = [
        {
            "id": o.id,
            "category": o.category,
            "skipped": o.skipped,
            "error": o.error,
            "hard_failed": sum(1 for c in o.checks if c.klass == "hard" and not c.passed),
            "behaviour_failed": sum(1 for c in o.checks if c.klass == "behaviour" and not c.passed),
            "checks": len(o.checks),
            "hard_checks": sum(1 for c in o.checks if c.klass == "hard"),
            "steps_skipped": sum(1 for st in o.steps if st.skipped),
        }
        for o in outcomes
    ]
    return summary


def run_rag(stack: Stack, full: bool, limit: int | None) -> dict[str, Any] | None:
    if not stack.settings.rag_enabled:
        return None
    if stack.profile == "deterministic":
        stack.llm.script = SCRIPTS["best_overlap"]
    questions = load_rag_questions()[:limit]
    user = uuid.UUID(create_user_in_db(stack.database_url, email="rag-eval@eval.example.com"))
    results, meta = asyncio.run(rag_eval.run(stack.settings, questions, user, "hybrid"))
    report: dict[str, Any] = {
        "questions": len(results),
        "retrieval_meta": meta,
        "first_request_ms": results[0].latency_ms if results else None,
        "overall": summarise(results),
        "by_category": group_by(results, "category"),
        "by_language": group_by(results, "language"),
    }
    if full:
        report["results"] = [asdict(r) for r in results]
    else:
        passage_text = {p.id: p.text for p in load_rag_corpus().passages}
        report["citation_audit"] = citation_audit.audit_results(
            [asdict(r) for r in results], passage_text
        )
    return report


def _command(args: argparse.Namespace) -> str:
    """The reproducing command from a fixed list of options: never a path, URL or key."""
    parts = ["python -m evaluation.suite", f"--profile {args.profile}"]
    if args.only:
        parts.append("--only " + " ".join(args.only))
    parts.append("--datasets " + " ".join(args.datasets))
    parts.append(f"--rerank-depths {args.rerank_depths}")
    if args.repeat != 1:
        parts.append(f"--repeat {args.repeat}")
    if args.rag_limit:
        parts.append(f"--rag-limit {args.rag_limit}")
    if args.llm_model:
        parts.append(f"--llm-model {args.llm_model} --llm-provider {args.llm_provider}")
    parts += [flag for flag, on in (("--full", args.full), ("--legacy-ids", args.legacy_ids)) if on]
    return " ".join(parts)


def anonymise(report: dict[str, Any]) -> dict[str, Any]:
    """A report fit to commit as a baseline: nothing that identifies a machine, a person, a time
    or an uncommitted state (what is compared is profile, datasets, settings and results)."""
    meta = report["meta"]
    for key in ("created_at", "git", "wall_seconds"):
        meta.pop(key, None)
    meta["hardware"] = {"note": "removed from a committed baseline"}
    meta["command"] = meta["command"].split(" --json")[0]
    return report


def build_report(stack: Stack, args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    sections = set(args.only or SECTIONS)
    meta = runmeta.collect(_command(args), stack.profile, stack.settings)
    report: dict[str, Any] = {
        "schema_version": runmeta.SCHEMA_VERSION,
        "profile": stack.profile,
        "settings": meta["settings"],
        "datasets": meta["datasets"],
        "meta": meta,
    }
    if "retrieval" in sections:
        depths = tuple(int(d) for d in args.rerank_depths.split(",") if d.strip().isdigit())
        report["retrieval"] = run_retrieval(
            stack, args.full, args.repeat, args.datasets, depths, args.legacy_ids
        )
    if "scenarios" in sections:
        report["scenarios"] = run_scenarios(stack)
    if "rag" in sections:
        rag = run_rag(stack, args.full, args.rag_limit)
        if rag is not None:
            report["rag"] = rag
    report["meta"]["wall_seconds"] = round(time.perf_counter() - started, 1)
    return report


def print_summary(report: dict[str, Any]) -> None:
    print(f"profile={report['profile']} wall={report['meta']['wall_seconds']}s")
    for name, data in report.get("retrieval", {}).items():
        print(
            f"\n[retrieval:{name}] {data['questions']} questions ({data['answerable']} answerable)"
        )
        for arm, entry in data["arms"].items():
            if entry.get("kind") == "check":
                print(
                    f"  {arm:24s} check: {entry['results_returned']} results, "
                    f"violations={entry['filter_or_candidate_violations']}"
                )
                continue
            o = entry["overall"]
            ci = o.get("mrr_at_10_ci95")
            print(
                f"  {arm:24s} R@1={o['recall_at_1']:.3f} R@5={o['recall_at_5']:.3f} "
                f"MRR@10={o['mrr_at_10']:.3f}{f' (95% CI {ci[0]:.3f}-{ci[1]:.3f})' if ci else ''} "
                f"nDCG@10={o['ndcg_at_10']:.3f} median={entry['latency_ms'].get('median')} ms "
                f"violations={entry['filter_or_candidate_violations']}"
            )
    s = report.get("scenarios")
    if s:
        print(
            f"\n[scenarios] run={s['scenarios_run']} skipped={s['scenarios_skipped']} "
            f"errors={s['scenario_errors']} "
            f"hard={s['hard_checks'] - s['hard_failed']}/{s['hard_checks']} "
            f"behaviour={s['behaviour_passed']}/{s['behaviour_checks']}"
        )
        for failure in s["failures"][:10]:
            print(f"  FAIL [{failure['class']}] {failure['check']} {failure['detail']}")
    rag = report.get("rag")
    if rag:
        o = rag["overall"]
        print(
            f"\n[rag] {rag['questions']} questions: fact_recall={o['fact_recall_on_answerable']} "
            f"abstention={o['abstention_accuracy_on_unanswerable']} "
            f"citation_precision={o['citation_precision']} errors={o['provider_errors']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--profile", choices=PROFILES, default="deterministic")
    parser.add_argument("--only", nargs="+", choices=SECTIONS)
    parser.add_argument("--llm-model", help="real generation model (profile models); opt-in")
    parser.add_argument("--llm-provider", default="ollama")
    parser.add_argument("--llm-base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--allow-download", action="store_true", help="let models be downloaded")
    parser.add_argument(
        "--datasets", nargs="+", choices=["combined", "heldout"], default=["combined", "heldout"]
    )
    parser.add_argument("--rerank-depths", default="10,20,50", help="candidate depths to rerank")
    parser.add_argument("--legacy-ids", action="store_true", help="ids of the earlier tools")
    parser.add_argument("--repeat", type=int, default=1, help="repeat each retrieval for timing")
    parser.add_argument("--rag-limit", type=int)
    parser.add_argument("--full", action="store_true", help="keep per-question results (large)")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--anonymise", action="store_true", help="strip machine and git details")
    parser.add_argument("--baseline", type=Path, help="compare with a baseline run and gate")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    for card in dataset_cards():
        print(f"dataset {card['name']} v{card['version']} sha256 {card['sha256'][:12]}")
    with open_stack(
        args.profile,
        llm_model=args.llm_model,
        llm_provider=args.llm_provider,
        llm_base_url=args.llm_base_url,
        allow_download=args.allow_download,
    ) as stack:
        report = build_report(stack, args)
    print_summary(report)
    if args.anonymise:
        report = anonymise(report)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    code = 0
    failures = compare_runs.hard_gates(report, None)
    if args.baseline:
        outcome = compare_runs.compare(
            json.loads(args.baseline.read_text(encoding="utf-8")), report
        )
        failures = outcome["hard_failures"]
        for key in ("quality_warnings", "performance_warnings"):
            for line in outcome[key]:
                print(f"{key}: {line}")
        if outcome["quality_not_comparable"]:
            print(f"quality not compared: {outcome['quality_not_comparable']}")
        if args.strict and (
            outcome["quality_warnings"]
            or outcome["performance_warnings"]
            or outcome["quality_not_comparable"]
        ):
            code = 2
    for line in failures:
        print(f"HARD GATE FAILURE: {line}")
    sys.exit(1 if failures else code)


if __name__ == "__main__":
    main()
