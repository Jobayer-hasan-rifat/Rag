"""Run metadata: what is needed to reproduce a run or to judge a comparison fairly.

A report from `evaluation.suite` carries this block. Settings are reported by name and value for the
parameters that change results (models, revisions, limits, weights); secrets and URLs are never
included.
"""

import datetime
import importlib.metadata
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.config import Settings
from evaluation.cards import dataset_cards

SCHEMA_VERSION = 1
_ROOT = Path(__file__).resolve().parents[2]
_PACKAGES = (
    "fastapi", "sqlalchemy", "pydantic", "numpy", "pgvector", "torch", "sentence-transformers",
    "transformers", "httpx",
)  # fmt: skip
_SETTING_NAMES = (
    "embedding_provider", "embedding_model", "embedding_model_revision", "embedding_dimension",
    "embedding_query_prefix", "embedding_passage_prefix", "embedding_max_seq_length",
    "embedding_include_heading", "embedding_device", "embedding_batch_size",
    "hybrid_dense_candidates", "hybrid_sparse_candidates", "hybrid_rrf_k", "hybrid_dense_weight",
    "hybrid_sparse_weight", "sparse_remove_stopwords", "search_max_top_k",
    "rerank_enabled", "rerank_provider", "rerank_model", "rerank_model_revision",
    "rerank_device", "rerank_batch_size", "rerank_candidates", "rerank_max_candidates",
    "rerank_timeout_seconds", "llm_provider", "llm_model", "llm_temperature",
    "llm_max_output_tokens", "llm_context_tokens", "llm_structured_output", "rag_retrieval_mode",
    "rag_candidates", "rag_max_sources", "rag_max_chunk_tokens", "rag_context_token_budget",
    "conversation_history_turns", "conversation_history_token_budget",
    "conversation_history_message_tokens", "conversation_followup_max_words",
    "max_versions_per_document", "chunking_max_chars", "chunking_overlap_chars",
)  # fmt: skip


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=_ROOT, capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def rss_mb() -> float | None:
    """Resident memory of this process in MB (working set on Windows), when measurable."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("page_faults", wintypes.DWORD),
                ("peak_working_set", ctypes.c_size_t),
                ("working_set", ctypes.c_size_t),
                ("peak_paged_pool", ctypes.c_size_t),
                ("paged_pool", ctypes.c_size_t),
                ("peak_non_paged_pool", ctypes.c_size_t),
                ("non_paged_pool", ctypes.c_size_t),
                ("pagefile", ctypes.c_size_t),
                ("peak_pagefile", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        psapi = ctypes.WinDLL("psapi")
        handle = ctypes.c_void_p(ctypes.WinDLL("kernel32").GetCurrentProcess())
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
        ok = psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        return round(counters.working_set / 1048576, 1) if ok else None
    status = Path("/proc/self/status")
    if status.exists():
        for line in status.read_text().splitlines():
            if line.startswith("VmRSS:"):
                return round(int(line.split()[1]) / 1024, 1)
    return None


def _version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def hardware() -> dict[str, Any]:
    info: dict[str, Any] = {
        "platform": platform.platform(),
        "processor": platform.processor(),
        "logical_cpus": os.cpu_count(),
        "accelerator": "none used by the deterministic profile",
    }
    try:
        import torch  # only when the ML runtime is installed

        info["torch_threads"] = torch.get_num_threads()
        info["cuda_available"] = bool(torch.cuda.is_available())
        info["accelerator"] = (
            torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu only"
        )
    except ImportError:
        pass
    return info


def collect(
    command: str, profile: str, settings: Settings, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    values = settings.model_dump()
    status = _git("status", "--porcelain")
    meta: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "command": command,
        "profile": profile,
        "git": {
            "commit": _git("rev-parse", "HEAD"),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": None if status is None else bool(status),
        },
        "python": platform.python_version(),
        "packages": {name: _version(name) for name in _PACKAGES},
        "hardware": hardware(),
        "settings": {name: values[name] for name in _SETTING_NAMES if name in values},
        "datasets": [
            {k: card[k] for k in ("name", "version", "questions", "documents", "sha256")}
            for card in dataset_cards()
        ],
    }
    if extra:
        meta.update(extra)
    return meta
