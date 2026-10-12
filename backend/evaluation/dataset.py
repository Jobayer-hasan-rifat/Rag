"""Loads the retrieval evaluation dataset (see evaluation/retrieval/README.md)."""

import json
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "retrieval"


@dataclass(frozen=True)
class Passage:
    id: str
    document_id: str
    document_title: str
    language: str
    heading: str
    text: str


@dataclass(frozen=True)
class EvalQuery:
    id: str
    category: str
    query: str
    relevant: tuple[str, ...]
    neutral: tuple[str, ...]


@dataclass(frozen=True)
class EvalDocument:
    id: str
    title: str
    language: str
    passages: tuple[Passage, ...]


@dataclass(frozen=True)
class Dataset:
    documents: tuple[EvalDocument, ...]
    queries: tuple[EvalQuery, ...]

    @property
    def passages(self) -> tuple[Passage, ...]:
        return tuple(p for document in self.documents for p in document.passages)


def load_dataset(directory: Path = DATA_DIR) -> Dataset:
    corpus = json.loads((directory / "corpus.json").read_text(encoding="utf-8"))
    queries = json.loads((directory / "queries.json").read_text(encoding="utf-8"))
    documents = tuple(
        EvalDocument(
            id=d["id"],
            title=d["title"],
            language=d["language"],
            passages=tuple(
                Passage(
                    id=s["passage"]["id"],
                    document_id=d["id"],
                    document_title=d["title"],
                    language=d["language"],
                    heading=s["heading"],
                    text=s["passage"]["text"],
                )
                for s in d["sections"]
            ),
        )
        for d in corpus["documents"]
    )
    dataset = Dataset(
        documents=documents,
        queries=tuple(
            EvalQuery(
                id=q["id"],
                category=q["category"],
                query=q["query"],
                relevant=tuple(q["relevant"]),
                neutral=tuple(q.get("neutral", [])),
            )
            for q in queries["queries"]
        ),
    )
    validate(dataset)
    return dataset


def validate(dataset: Dataset) -> None:
    """Fail loudly on a malformed dataset instead of producing misleading scores."""
    passage_ids = [p.id for p in dataset.passages]
    if len(passage_ids) != len(set(passage_ids)):
        raise ValueError("duplicate passage ids")
    known = set(passage_ids)
    query_ids = [q.id for q in dataset.queries]
    if len(query_ids) != len(set(query_ids)):
        raise ValueError("duplicate query ids")
    for q in dataset.queries:
        unknown = (set(q.relevant) | set(q.neutral)) - known
        if unknown:
            raise ValueError(f"query {q.id} references unknown passages {sorted(unknown)}")
        if set(q.relevant) & set(q.neutral):
            raise ValueError(f"query {q.id} lists a passage as both relevant and neutral")
        if not q.query.strip():
            raise ValueError(f"query {q.id} is empty")
