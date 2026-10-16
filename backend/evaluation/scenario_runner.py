"""Runs the scenario dataset (evaluation/scenarios/scenarios.json) against the real application.

Every step goes through the public HTTP API; only the worker stages (processing, embedding,
re-chunking) are run in process, exactly as the integration tests do, so the order of events is
under the scenario's control. Each scenario gets fresh users, so nothing carries over.

Expectations are split in two classes (see the dataset notes):

- `hard`: guarantees of the pipeline that must hold with any generation model: status codes, which
  version a citation names, what text may reach the prompt, whether a stored citation still
  resolves, which documents are searched.
- `behaviour`: the content of an answer (wording, abstention). With the scripted model of the
  deterministic profile they are exact; with a real model they are reported as rates.
"""

import html
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.rag.tokens import estimate_tokens
from evaluation.rag_metrics import fold
from evaluation.scripted import SCRIPTS, SIMULATED_FAULTS
from evaluation.stack import Stack, make_user
from tests.helpers import db_execute
from tests.llm_fakes import sources_of
from tests.processing_helpers import embed_now, process_now, rechunk_now

SCENARIO_FILE = Path(__file__).resolve().parent / "scenarios" / "scenarios.json"
BLANK = b"   \n\n   \n"  # uploads fine, extracts to nothing, so processing fails
DOCS = "/api/v1/documents"
OPS = frozenset(
    {
        "upload",
        "replace",
        "stage",
        "restore",
        "reprocess",
        "delete_version",
        "delete_document",
        "search",
        "ask",
        "converse",
        "check_citations",
        "check_messages",
        "check_document",
        "rewrite_last_answer",
        "fault",
        "versions_of",
        "probe",
    }
)
_ANSWER_HARD = frozenset(
    {
        "status", "reason", "no_provider_detail", "cited_versions", "sources_include",
        "sources_exclude", "prompt_only_inside", "degraded", "history_turns", "retrieval_extended",
    }
)  # fmt: skip
_BEHAVIOUR = frozenset({"answer_contains", "answer_forbidden", "abstain"})
# Only these ops carry expectations, and only these keys, so a misplaced or misspelt key is an
# error when the dataset loads instead of a check that silently never runs.
ALLOWED: dict[str, dict[str, frozenset[str]]] = {
    "ask": {"hard": _ANSWER_HARD, "behaviour": _BEHAVIOUR},
    "converse": {"hard": _ANSWER_HARD, "behaviour": _BEHAVIOUR},
    "search": {
        "hard": frozenset(
            {"status", "texts_include", "texts_exclude", "versions_only", "max_results"}
        )
    },
    "versions_of": {"hard": frozenset({"status"})},
    "probe": {"hard": frozenset({"status"})},
    "check_citations": {"hard": frozenset({"citations"})},
    "check_messages": {"hard": frozenset({"messages"})},
    "check_document": {
        "hard": frozenset({"searchable", "active_version", "latest_status", "version_count"})
    },
}
CITATION_KEYS = frozenset(
    {
        "version", "superseded", "available", "removed", "contains", "not_contains", "no_text",
        "no_name",
    }
)  # fmt: skip


class StepSkipped(Exception):
    """A step that depends on an answer step this run did not perform."""


@dataclass
class Check:
    name: str
    klass: str
    passed: bool
    detail: str = ""
    gating: bool = True  # False: a rate to report (behaviour with a real model)


@dataclass
class StepOutcome:
    index: int
    op: str
    checks: list[Check] = field(default_factory=list)
    skipped: str | None = None
    error: str | None = None
    latency_ms: int | None = None
    expected: bool = False  # the step declared expectations


@dataclass
class ScenarioOutcome:
    id: str
    category: str
    steps: list[StepOutcome] = field(default_factory=list)
    skipped: str | None = None
    error: str | None = None

    @property
    def checks(self) -> list[Check]:
        return [c for s in self.steps for c in s.checks]


def load_scenarios(path: Path = SCENARIO_FILE) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    validate(data)
    return data


def validate(data: dict[str, Any]) -> None:
    """Fail loudly on a malformed dataset instead of producing misleading results."""
    texts = data["texts"]
    ids = [s["id"] for s in data["scenarios"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate scenario ids")
    for scenario in data["scenarios"]:
        label = scenario["id"]
        if not scenario["steps"]:
            raise ValueError(f"{label}: no steps")
        docs: set[str] = set()
        versions: set[str] = set()
        for step in scenario["steps"]:
            op = step["op"]
            if op not in OPS:
                raise ValueError(f"{label}: unknown op {op!r}")
            if "text" in step and step["text"] not in texts:
                raise ValueError(f"{label}: unknown text {step['text']!r}")
            if "script" in step and step["script"] not in SCRIPTS:
                raise ValueError(f"{label}: unknown script {step['script']!r}")
            if op in ("upload", "replace"):
                name = step.get("as", "v1") if op == "upload" else step["as"]
                if name in versions:
                    raise ValueError(
                        f"{label}: version name {name!r} is used twice; citations are matched by "
                        "version name, so every upload and replacement needs its own"
                    )
                docs.add(step["doc"])
                versions.add(name)
            for name in step.get("docs", []) + ([step["doc"]] if "doc" in step else []):
                if name not in docs:
                    raise ValueError(f"{label}: step uses document {name!r} before it exists")
            expect = step.get("expect", {})
            cited = expect.get("hard", {}).get("cited_versions", [])
            for name in [step.get("version"), *cited]:
                if name and name not in versions:
                    raise ValueError(f"{label}: unknown version {name!r}")
            if expect and op not in ALLOWED:
                raise ValueError(f"{label}: {op} takes no expectations")
            for klass, expectation in expect.items():
                allowed = ALLOWED.get(op, {}).get(klass, frozenset())
                unknown = set(expectation) - allowed
                if unknown or not expectation:
                    raise ValueError(
                        f"{label}: unknown or empty {klass} expectation {sorted(unknown)} for {op}"
                    )
            for key in ("citations", "messages"):
                listed = expect.get("hard", {}).get(key)
                if listed is not None and not listed:
                    raise ValueError(f"{label}: {key} expectation is empty")
            for item in expect.get("hard", {}).get("citations", []):
                if set(item) - CITATION_KEYS or "version" not in item or len(item) < 2:
                    raise ValueError(f"{label}: bad citation expectation {sorted(item)}")


# --- prompt inspection -----------------------------------------------------------------------

_SOURCES = re.compile(r"<sources>.*?</sources>", re.DOTALL)
_HISTORY = re.compile(r"<conversation_history>.*?</conversation_history>", re.DOTALL)


def prompt_regions(system: str, user: str) -> dict[str, str]:
    """The prompt split into the regions the policy distinguishes."""
    sources = "".join(_SOURCES.findall(user))
    history = "".join(_HISTORY.findall(user))
    rest = _HISTORY.sub("", _SOURCES.sub("", user))
    return {"system": system, "sources": sources, "conversation_history": history, "other": rest}


def source_text(requests: list[Any]) -> str:
    return "\n".join(
        html.unescape(body) for request in requests for _label, body in sources_of(request)
    )


@dataclass
class Answer:
    """What the API said, normalised across /rag/answer and a conversation message."""

    status: int
    reason: str | None
    text: str
    insufficient: bool | None
    citations: list[dict[str, Any]]
    degraded: list[str]
    details: dict[str, Any]
    raw: str


class ScenarioRunner:
    def __init__(self, stack: Stack) -> None:
        self.stack = stack
        self.client = stack.client
        self.dataset = load_scenarios()
        self.texts: dict[str, str] = self.dataset["texts"]
        self.usage: list[dict[str, Any]] = []  # per conversation answer: history shown, prompt size
        self._reset()

    def _reset(self) -> None:
        self.docs: dict[str, dict[str, str]] = {}
        self.versions: dict[str, dict[str, str]] = {}
        self.symbols: dict[str, str] = {}
        self.convs: dict[str, str] = {}
        self.scenario_id = ""

    # --- public --------------------------------------------------------------------------

    def run_all(self, only: set[str] | None = None) -> list[ScenarioOutcome]:
        return [self.run(s) for s in self.dataset["scenarios"] if not only or s["id"] in only]

    def run(self, scenario: dict[str, Any]) -> ScenarioOutcome:
        self._reset()
        self.scenario_id = scenario["id"]
        outcome = ScenarioOutcome(scenario["id"], scenario["category"])
        profiles = scenario.get("profiles", ["deterministic", "models"])
        if self.stack.profile not in profiles:
            outcome.skipped = f"not meaningful in the {self.stack.profile} profile"
            return outcome
        for index, step in enumerate(scenario["steps"]):
            result = StepOutcome(index, step["op"])
            outcome.steps.append(result)
            try:
                started = time.perf_counter()
                handler: Callable[[dict[str, Any], StepOutcome], None] = getattr(
                    self, f"_op_{step['op']}"
                )
                result.expected = bool(step.get("expect"))
                handler(step, result)
                result.latency_ms = round((time.perf_counter() - started) * 1000)
                if result.expected and not result.skipped and not result.checks:
                    raise RuntimeError("the step declared expectations but produced no check")
            except StepSkipped as skipped:
                result.skipped = str(skipped)
            except Exception as error:  # a harness or setup failure is a failure, never a pass
                result.error = f"{type(error).__name__}: {str(error)[:200]}"
                outcome.error = f"step {index} ({step['op']}) failed: {result.error}"
                break
        self._cleanup()
        if outcome.error is None and not outcome.checks:
            outcome.skipped = "no check ran (its steps were skipped in this profile)"
        return outcome

    # --- helpers -------------------------------------------------------------------------

    def _cleanup(self) -> None:
        """Undo anything a scenario patched in the shared application, even after an error."""
        from app.embeddings.registry import get_provider

        get_provider(self.stack.settings).__dict__.pop("embed_query", None)
        if self.stack.profile == "deterministic":
            self.stack.llm.script = None

    def _headers(self, step: dict[str, Any]) -> dict[str, str]:
        return make_user(self.stack, f"{self.scenario_id}/{step.get('user', 'alice')}")

    def _expect(
        self,
        result: StepOutcome,
        step: dict[str, Any],
        klass: str,
        name: str,
        ok: bool,
        detail: str = "",
    ) -> None:
        # a behaviour check gates only where the answering model is scripted (exact)
        gating = klass == "hard" or self.stack.profile == "deterministic"
        label = f"{self.scenario_id}#{result.index} {name}"
        result.checks.append(Check(label, klass, ok, detail, gating))

    def _symbol(self, version_id: str | None) -> str:
        return self.symbols.get(str(version_id), "?")

    def _doc_ids(self, step: dict[str, Any]) -> list[str] | None:
        names = step.get("docs")
        return [self.docs[n]["id"] for n in names] if names else None

    def _register_version(self, name: str, doc: str, version_id: str, user: str) -> None:
        self.versions[name] = {"id": version_id, "doc": doc, "user": user}
        self.symbols[version_id] = name

    def _conv(self, step: dict[str, Any]) -> str:
        if step["conv"] not in self.convs:
            raise StepSkipped("the conversation was not created (answer steps were skipped)")
        return self.convs[step["conv"]]

    def _stage(self, version_id: str, stage: str) -> None:
        if stage == "process":
            assert process_now(self.client, version_id).status == "chunked"
        else:
            assert embed_now(self.client, version_id).status == "ready"

    def _script(self, step: dict[str, Any]) -> str | None:
        """Selects the scripted model's behaviour (deterministic profile); None skips the step."""
        name = step.get("script")
        if self.stack.profile == "deterministic":
            self.stack.llm.script = SCRIPTS[name or "best_overlap"]
            return name or "best_overlap"
        return None if name in SIMULATED_FAULTS else (name or "")

    # --- document operations ---------------------------------------------------------------

    def _op_upload(self, step: dict[str, Any], result: StepOutcome) -> None:
        headers = self._headers(step)
        content = self.texts[step["text"]].encode("utf-8")
        name = step.get("name", f"{step['doc']}.txt")
        response = self.client.post(DOCS, headers=headers, files={"file": (name, content, "")})
        assert response.status_code == 201, response.text
        doc_id = response.json()["data"]["id"]
        self.docs[step["doc"]] = {"id": doc_id, "user": step.get("user", "alice")}
        self._register_version(step.get("as", "v1"), step["doc"], doc_id, step.get("user", "alice"))
        if step.get("ready", True):
            self._stage(doc_id, "process")
            self._stage(doc_id, "embed")

    def _op_replace(self, step: dict[str, Any], result: StepOutcome) -> None:
        headers = self._headers(step)
        outcome = step.get("outcome", "ready")
        content = BLANK if outcome == "failed" else self.texts[step["text"]].encode("utf-8")
        doc = self.docs[step["doc"]]
        response = self.client.post(
            f"{DOCS}/{doc['id']}/versions", headers=headers, files={"file": ("v.txt", content, "")}
        )
        assert response.status_code == 201, response.text
        version_id = response.json()["data"]["version"]["id"]
        self._register_version(step["as"], step["doc"], version_id, doc["user"])
        if outcome in ("processed", "ready", "failed"):
            if outcome == "failed":
                assert process_now(self.client, version_id).status == "failed"
            else:
                self._stage(version_id, "process")
        if outcome == "ready":
            self._stage(version_id, "embed")

    def _op_stage(self, step: dict[str, Any], result: StepOutcome) -> None:
        self._stage(self.versions[step["version"]]["id"], step["stage"])

    def _op_restore(self, step: dict[str, Any], result: StepOutcome) -> None:
        version = self.versions[step["version"]]
        doc = self.docs[version["doc"]]
        response = self.client.post(
            f"{DOCS}/{doc['id']}/versions/{version['id']}/activate", headers=self._headers(step)
        )
        assert response.status_code == 200, response.text

    def _op_reprocess(self, step: dict[str, Any], result: StepOutcome) -> None:
        version_id = self.versions[step["version"]]["id"]
        assert rechunk_now(self.client, version_id).status == "chunked"
        self._stage(version_id, "embed")

    def _op_delete_version(self, step: dict[str, Any], result: StepOutcome) -> None:
        version = self.versions[step["version"]]
        response = self.client.delete(
            f"{DOCS}/{self.docs[version['doc']]['id']}/versions/{version['id']}",
            headers=self._headers(step),
        )
        assert response.status_code == 204, response.text

    def _op_delete_document(self, step: dict[str, Any], result: StepOutcome) -> None:
        response = self.client.delete(
            f"{DOCS}/{self.docs[step['doc']]['id']}", headers=self._headers(step)
        )
        assert response.status_code == 204, response.text

    def _op_versions_of(self, step: dict[str, Any], result: StepOutcome) -> None:
        response = self.client.get(
            f"{DOCS}/{self.docs[step['doc']]['id']}/versions", headers=self._headers(step)
        )
        self._expect(
            result,
            step,
            "hard",
            "status",
            response.status_code == step["expect"]["hard"]["status"],
            str(response.status_code),
        )

    def _op_probe(self, step: dict[str, Any], result: StepOutcome) -> None:
        """Another user's reads and writes on a document, its version and a conversation: every
        one must be a 404 (and, being a probe, the scenario then checks nothing changed)."""
        headers = self._headers(step)
        doc = self.docs[step["doc"]]
        assert doc["user"] != step.get("user", "alice"), "a probe must come from another user"
        version = next(v for v in self.versions.values() if v["doc"] == step["doc"])
        base = f"{DOCS}/{doc['id']}"
        requests: list[tuple[str, str, dict[str, Any]]] = [
            ("GET", base, {}),
            ("GET", f"{base}/versions", {}),
            ("GET", f"{base}/versions/{version['id']}", {}),
            ("POST", f"{base}/versions/{version['id']}/activate", {}),
            ("POST", f"{base}/versions/{version['id']}/retry", {}),
            ("DELETE", f"{base}/versions/{version['id']}", {}),
            ("GET", f"{base}/chunks", {}),
            ("GET", f"{base}/download", {}),
            ("PATCH", base, {"json": {"filename": "x.txt"}}),
            ("POST", f"{base}/retry", {}),
            ("DELETE", base, {}),
        ]
        if step.get("conv"):
            conv = f"/api/v1/conversations/{self._conv(step)}"
            requests += [
                ("GET", conv, {}),
                ("PATCH", conv, {"json": {"title": "mine now"}}),
                ("POST", f"{conv}/messages", {"json": {"content": "hello there"}}),
                ("DELETE", conv, {}),
            ]
        statuses = [
            (method, self.client.request(method, url, headers=headers, **kwargs).status_code)
            for method, url, kwargs in requests
        ]
        wrong = [f"{m} {code}" for m, code in statuses if code != step["expect"]["hard"]["status"]]
        self._expect(
            result,
            step,
            "hard",
            f"all {len(statuses)} foreign requests answered",
            not wrong,
            str(wrong[:5]),
        )

    def _op_fault(self, step: dict[str, Any], result: StepOutcome) -> None:
        from app.embeddings.registry import get_provider
        from app.embeddings.types import EmbeddingFailedError

        provider = get_provider(self.stack.settings)
        if step["on"]:

            def failing(text: str) -> Any:
                raise EmbeddingFailedError("simulated query embedding failure")

            provider.embed_query = failing  # type: ignore[method-assign]
        else:
            provider.__dict__.pop("embed_query", None)

    def _op_rewrite_last_answer(self, step: dict[str, Any], result: StepOutcome) -> None:
        db_execute(
            self.stack.database_url,
            "UPDATE messages SET content = :c WHERE id = (SELECT id FROM messages WHERE "
            "conversation_id = CAST(:v AS uuid) AND role = 'assistant' "
            "ORDER BY sequence DESC LIMIT 1)",
            c=step["content"],
            v=self._conv(step),
        )

    def _op_check_document(self, step: dict[str, Any], result: StepOutcome) -> None:
        response = self.client.get(
            f"{DOCS}/{self.docs[step['doc']]['id']}", headers=self._headers(step)
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        expected = step["expect"]["hard"]
        actual = {
            "searchable": data["searchable"],
            "active_version": (
                self._symbol(data["active_version_id"]) if data["active_version_id"] else None
            ),
            "latest_status": (data["latest_version"] or {}).get("status"),
            "version_count": data["version_count"],
        }
        for key, want in expected.items():
            self._expect(
                result, step, "hard", key, actual[key] == want, f"{actual[key]!r} != {want!r}"
            )

    # --- search ------------------------------------------------------------------------------

    def _op_search(self, step: dict[str, Any], result: StepOutcome) -> None:
        body: dict[str, Any] = {"query": step["q"], "top_k": 10}
        if step.get("docs"):
            body["document_ids"] = self._doc_ids(step)
        if step.get("rerank"):
            body["rerank"] = True
        mode = step.get("mode", "hybrid")
        response = self.client.post(
            f"/api/v1/search/{mode}", headers=self._headers(step), json=body
        )
        expected = step["expect"]["hard"]
        self._expect(
            result,
            step,
            "hard",
            "status",
            response.status_code == expected.get("status", 200),
            str(response.status_code),
        )
        if response.status_code != 200:
            return
        results = response.json()["data"]["results"]
        texts = fold(" ".join(r["text"] for r in results))
        for group in expected.get("texts_include", []):
            self._expect(
                result, step, "hard", f"texts_include {group}", any(fold(o) in texts for o in group)
            )
        for forbidden in expected.get("texts_exclude", []):
            self._expect(
                result, step, "hard", f"texts_exclude {forbidden}", fold(forbidden) not in texts
            )
        if "versions_only" in expected:
            seen = {self._symbol(r["version_id"]) for r in results}
            self._expect(
                result,
                step,
                "hard",
                "versions_only",
                bool(seen) and seen <= set(expected["versions_only"]),
                str(sorted(seen)),
            )
        if "max_results" in expected:
            self._expect(
                result,
                step,
                "hard",
                "max_results",
                len(results) <= expected["max_results"],
                str(len(results)),
            )

    # --- answers -----------------------------------------------------------------------------

    def _post_answer(
        self, step: dict[str, Any], conversation: bool
    ) -> tuple[Answer, list[Any]] | None:
        if not self.stack.settings.rag_enabled or self._script(step) is None:
            return None
        headers = self._headers(step)
        before = len(self.stack.llm.requests)
        if conversation:
            name = step["conv"]
            if name not in self.convs:
                created = self.client.post("/api/v1/conversations", headers=headers, json={})
                assert created.status_code == 201, created.text
                self.convs[name] = created.json()["data"]["id"]
            body: dict[str, Any] = {"content": step["q"]}
            if step.get("docs"):
                body["document_ids"] = self._doc_ids(step)
            response = self.client.post(
                f"/api/v1/conversations/{self.convs[name]}/messages", headers=headers, json=body
            )
        else:
            body = {"question": step["q"]}
            if step.get("docs"):
                body["document_ids"] = self._doc_ids(step)
            response = self.client.post("/api/v1/rag/answer", headers=headers, json=body)
        return self._normalise(response, conversation), self.stack.llm.requests[before:]

    @staticmethod
    def _normalise(response: Any, conversation: bool) -> Answer:
        payload = response.json()
        if response.status_code >= 400:
            error = payload.get("error", {})
            return Answer(
                response.status_code,
                (error.get("details") or {}).get("reason"),
                "",
                None,
                [],
                [],
                {},
                response.text,
            )
        data = payload["data"]
        message = data["assistant_message"] if conversation else data
        text = message["content"] if conversation else message["answer"]
        details = message.get("details") or {}
        retrieval = details.get("retrieval") if conversation else message.get("retrieval")
        return Answer(
            response.status_code,
            None,
            text,
            message["insufficient_evidence"],
            message["citations"],
            list((retrieval or {}).get("degraded", [])),
            details,
            response.text,
        )

    def _op_ask(self, step: dict[str, Any], result: StepOutcome) -> None:
        self._answer(step, result, conversation=False)

    def _op_converse(self, step: dict[str, Any], result: StepOutcome) -> None:
        self._answer(step, result, conversation=True)

    def _answer(self, step: dict[str, Any], result: StepOutcome, *, conversation: bool) -> None:
        posted = self._post_answer(step, conversation)
        if posted is None:
            result.skipped = "needs answer generation and a real-model-compatible step"
            return
        answer, requests = posted
        if conversation and requests:
            self.usage.append(
                {
                    "history_turns": answer.details.get("history_turns", 0),
                    "prompt_tokens_estimate": estimate_tokens(requests[-1].user),
                    "retrieval_extended": answer.details.get("retrieval_extended"),
                }
            )
        hard = step.get("expect", {}).get("hard", {})
        behaviour = step.get("expect", {}).get("behaviour", {})
        want_status = hard.get("status")
        ok = answer.status in (200, 201) if want_status is None else answer.status == want_status
        self._expect(result, step, "hard", "status", ok, str(answer.status))
        if "reason" in hard:
            self._expect(
                result, step, "hard", "reason", answer.reason == hard["reason"], str(answer.reason)
            )
        if hard.get("no_provider_detail"):
            self._expect(
                result, step, "hard", "no_provider_detail", "secret-host-name" not in answer.raw
            )
        if answer.status >= 400:
            return
        if "cited_versions" in hard:
            cited = {self._symbol(c.get("version_id")) for c in answer.citations}
            self._expect(
                result,
                step,
                "hard",
                "cited_versions",
                cited == set(hard["cited_versions"]),
                str(sorted(cited)),
            )
        sources = fold(source_text(requests))
        for needle in hard.get("sources_include", []):
            self._expect(result, step, "hard", f"sources_include {needle}", fold(needle) in sources)
        for needle in hard.get("sources_exclude", []):
            self._expect(
                result, step, "hard", f"sources_exclude {needle}", fold(needle) not in sources
            )
        if "prompt_only_inside" in hard:
            self._check_prompt(result, step, hard["prompt_only_inside"], requests)
        if "degraded" in hard:
            self._expect(
                result,
                step,
                "hard",
                "degraded",
                set(hard["degraded"]) <= set(answer.degraded),
                str(answer.degraded),
            )
        for key in ("history_turns", "retrieval_extended"):
            if key in hard:
                self._expect(
                    result,
                    step,
                    "hard",
                    key,
                    answer.details.get(key) == hard[key],
                    str(answer.details.get(key)),
                )
        folded = fold(answer.text)
        for group in behaviour.get("answer_contains", []):
            self._expect(
                result,
                step,
                "behaviour",
                f"answer_contains {group}",
                any(fold(o) in folded for o in group),
            )
        for needle in behaviour.get("answer_forbidden", []):
            self._expect(
                result, step, "behaviour", f"answer_forbidden {needle}", fold(needle) not in folded
            )
        if "abstain" in behaviour:
            self._expect(
                result,
                step,
                "behaviour",
                "abstain",
                bool(answer.insufficient) == behaviour["abstain"],
                str(answer.insufficient),
            )

    def _check_prompt(
        self, result: StepOutcome, step: dict[str, Any], spec: dict[str, str], requests: list[Any]
    ) -> None:
        needle = spec["text"]
        if not requests:
            self._expect(
                result,
                step,
                "hard",
                f"prompt_only_inside {spec['block']}",
                False,
                "no request reached the model",
            )
            return
        regions = prompt_regions(requests[-1].system, requests[-1].user)
        counts = {name: text.count(needle) for name, text in regions.items()}
        ok = (
            counts[spec["block"]] >= 1
            and sum(v for k, v in counts.items() if k != spec["block"]) == 0
        )
        self._expect(
            result,
            step,
            "hard",
            f"prompt_only_inside {spec['block']}",
            ok,
            str({k: v for k, v in counts.items() if v}),
        )

    def _op_check_messages(self, step: dict[str, Any], result: StepOutcome) -> None:
        response = self.client.get(
            f"/api/v1/conversations/{self._conv(step)}", headers=self._headers(step)
        )
        messages = response.json()["data"]["messages"]
        actual = [[m["role"], m["status"]] for m in messages]
        want = step["expect"]["hard"]["messages"]
        self._expect(result, step, "hard", "messages", actual == want, str(actual))

    def _op_check_citations(self, step: dict[str, Any], result: StepOutcome) -> None:
        response = self.client.get(
            f"/api/v1/conversations/{self._conv(step)}",
            headers=self._headers(step),
            params={"page_size": 100},
        )
        messages = response.json()["data"]["messages"]
        cited = [c for m in messages if m["role"] == "assistant" for c in m["citations"]]
        for position, want in enumerate(step["expect"]["hard"]["citations"]):
            if position >= len(cited):
                self._expect(result, step, "hard", f"citation {position} exists", False)
                continue
            got = cited[position]
            excerpt = got.get("excerpt")
            checks: dict[str, bool] = {
                "version": self._symbol(got.get("version_id")) == want["version"]
            }
            if "superseded" in want:
                checks["superseded"] = got["superseded"] == want["superseded"]
            if "available" in want:
                checks["available"] = got["source_available"] == want["available"]
            if "removed" in want:
                checks["removed"] = got["removed"] == want["removed"]
            if "contains" in want:
                checks["contains"] = bool(excerpt) and fold(want["contains"]) in fold(excerpt)
            if "not_contains" in want:
                checks["not_contains"] = not excerpt or fold(want["not_contains"]) not in fold(
                    excerpt
                )
            if want.get("no_text"):
                checks["no_text"] = excerpt is None
            if want.get("no_name"):
                checks["no_name"] = got.get("document_name") is None
            for name, ok in checks.items():
                self._expect(result, step, "hard", f"citation {position} {name}", ok)


def summarise(outcomes: list[ScenarioOutcome]) -> dict[str, Any]:
    ran = [o for o in outcomes if not o.skipped]
    hard = [c for o in ran for c in o.checks if c.klass == "hard"]
    behaviour = [c for o in ran for c in o.checks if c.klass == "behaviour"]
    skipped_steps = sum(1 for o in ran for st in o.steps if st.skipped)
    by_category: dict[str, dict[str, int]] = {}
    for o in ran:
        entry = by_category.setdefault(
            o.category,
            {
                "scenarios": 0,
                "hard_checks": 0,
                "hard_failed": 0,
                "behaviour_checks": 0,
                "behaviour_passed": 0,
                "errors": 0,
            },
        )
        entry["scenarios"] += 1
        entry["errors"] += 1 if o.error else 0
        for c in o.checks:
            if c.klass == "hard":
                entry["hard_checks"] += 1
                entry["hard_failed"] += 0 if c.passed else 1
            else:
                entry["behaviour_checks"] += 1
                entry["behaviour_passed"] += 1 if c.passed else 0
    return {
        "scenarios": len(outcomes),
        "scenarios_run": len(ran),
        "scenarios_skipped": len(outcomes) - len(ran),
        "scenario_errors": sum(1 for o in ran if o.error),
        "hard_checks": len(hard),
        "hard_failed": sum(1 for c in hard if not c.passed),
        "behaviour_checks": len(behaviour),
        "behaviour_passed": sum(1 for c in behaviour if c.passed),
        "behaviour_gate_failed": sum(1 for c in behaviour if c.gating and not c.passed),
        "steps_skipped": skipped_steps,
        "by_category": by_category,
        "failures": [
            {"check": c.name, "class": c.klass, "detail": c.detail[:160]}
            for o in ran
            for c in o.checks
            if not c.passed
        ][:100]
        + [
            {"check": o.id, "class": "error", "detail": (o.error or "")[:200]}
            for o in ran
            if o.error
        ],
    }
