from itertools import pairwise

import pytest
from pydantic import ValidationError

from app.api.http_headers import attachment_disposition
from app.core.documents.lifecycle import ALLOWED_TRANSITIONS, DocumentStatus, ensure_transition
from app.exceptions import InvalidStatusTransitionError
from app.schemas.collection import CollectionCreate, CollectionUpdate

S = DocumentStatus
PIPELINE = [S.PENDING, S.PARSING, S.CHUNKING, S.EMBEDDING, S.INDEXING, S.READY]


@pytest.mark.parametrize(("current", "target"), list(pairwise(PIPELINE)))
def test_documents_advance_one_stage_at_a_time(current: S, target: S) -> None:
    ensure_transition(current, target)


@pytest.mark.parametrize("stage", [S.PENDING, S.PARSING, S.CHUNKING, S.EMBEDDING, S.INDEXING])
def test_any_in_progress_stage_can_fail(stage: S) -> None:
    ensure_transition(stage, S.FAILED)


@pytest.mark.parametrize("finished", [S.READY, S.FAILED])
def test_finished_documents_can_be_requeued(finished: S) -> None:
    ensure_transition(finished, S.PENDING)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.PENDING, S.READY),
        (S.PENDING, S.EMBEDDING),
        (S.READY, S.FAILED),
        (S.READY, S.PARSING),
        (S.FAILED, S.READY),
        (S.FAILED, S.FAILED),
        (S.READY, S.READY),
        (S.INDEXING, S.PARSING),
    ],
)
def test_invalid_transitions_are_rejected(current: S, target: S) -> None:
    with pytest.raises(InvalidStatusTransitionError) as error:
        ensure_transition(current, target)

    assert error.value.status_code == 409


def test_every_status_has_a_defined_set_of_transitions() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(DocumentStatus)


def test_download_header_is_attachment_with_ascii_fallback_and_encoded_name() -> None:
    header = attachment_disposition("héllo wörld.pdf")

    assert header.startswith("attachment;")
    assert 'filename="h_llo w_rld.pdf"' in header
    assert "filename*=UTF-8''h%C3%A9llo%20w%C3%B6rld.pdf" in header


@pytest.mark.parametrize(
    "name", ['evil".pdf', 'a\\b".pdf', "a;b.pdf", "x\r\nSet-Cookie: pwned=1.pdf", "x\ny.pdf"]
)
def test_download_header_cannot_be_injected_through_the_filename(name: str) -> None:
    header = attachment_disposition(name)

    assert "\r" not in header and "\n" not in header
    assert header.count('"') == 2
    assert header.startswith("attachment; filename=")
    assert header.count("\n") == 0 and header.count("\r") == 0


def test_collection_names_are_normalised() -> None:
    assert CollectionCreate(name="  My   \t Papers  ").name == "My Papers"


@pytest.mark.parametrize("name", ["", "   ", "x" * 256, "bad\x00name"])
def test_invalid_collection_names_are_rejected(name: str) -> None:
    with pytest.raises(ValidationError):
        CollectionCreate(name=name)


def test_blank_description_becomes_none() -> None:
    assert CollectionCreate(name="a", description="   ").description is None


def test_collection_update_requires_a_change_and_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        CollectionUpdate()
    with pytest.raises(ValidationError):
        CollectionUpdate(user_id="x")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        CollectionUpdate(name=None)
