import json
import uuid
from typing import Any

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.helpers import create_user_in_db, db_execute, db_rows, db_scalar, downgrade, upgrade

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_auth_state")]

FAIL = (IntegrityError, DBAPIError)


def new_user(url: str, email: str) -> str:
    return create_user_in_db(url, email=email)


def new_conversation(url: str, user: str, title: str = "T") -> str:
    cid = str(uuid.uuid4())
    db_execute(
        url,
        "INSERT INTO conversations (id, user_id, title) "
        "VALUES (CAST(:c AS uuid), CAST(:u AS uuid), :t)",
        c=cid,
        u=user,
        t=title,
    )
    return cid


def add_message(url: str, cid: str, user: str, sequence: int, **values: Any) -> str:
    mid = str(uuid.uuid4())
    fields: dict[str, Any] = {
        "role": "user",
        "status": "answered",
        "content": "hello",
        "reply_to_id": None,
        "idempotency_key": None,
        "request_hash": None,
        "failure_reason": None,
        "citations": "[]",
    }
    fields.update(values)
    db_execute(
        url,
        "INSERT INTO messages (id, conversation_id, user_id, sequence, role, status, content, "
        "reply_to_id, idempotency_key, request_hash, failure_reason, citations) VALUES "
        "(CAST(:id AS uuid), CAST(:c AS uuid), CAST(:u AS uuid), :seq, :role, :status, :content, "
        "CAST(:reply AS uuid), :key, :hash, :reason, CAST(:citations AS jsonb))",
        id=mid, c=cid, u=user, seq=sequence, role=fields["role"], status=fields["status"],
        content=fields["content"], reply=fields["reply_to_id"], key=fields["idempotency_key"],
        hash=fields["request_hash"], reason=fields["failure_reason"], citations=fields["citations"],
    )  # fmt: skip
    return mid


# --- ownership is enforced by the database ----------------------------------------------------


def test_a_message_cannot_carry_another_users_id(migrated_database_url: str) -> None:
    alice = new_user(migrated_database_url, "alice@example.com")
    bob = new_user(migrated_database_url, "bob@example.com")
    conversation = new_conversation(migrated_database_url, alice)

    with pytest.raises(FAIL):
        add_message(migrated_database_url, conversation, bob, 1)  # bob's id, alice's conversation
    add_message(migrated_database_url, conversation, alice, 1)


def test_a_message_needs_an_existing_conversation(migrated_database_url: str) -> None:
    alice = new_user(migrated_database_url, "alice@example.com")

    with pytest.raises(FAIL):
        add_message(migrated_database_url, str(uuid.uuid4()), alice, 1)


def test_deleting_a_conversation_deletes_its_messages_and_only_its_own(
    migrated_database_url: str,
) -> None:
    url = migrated_database_url
    alice = new_user(url, "alice@example.com")
    first, second = new_conversation(url, alice), new_conversation(url, alice)
    question = add_message(url, first, alice, 1)
    add_message(url, first, alice, 2, role="assistant", status="complete", reply_to_id=question)
    add_message(url, second, alice, 1)

    db_execute(url, "DELETE FROM conversations WHERE id = CAST(:c AS uuid)", c=first)

    assert (
        db_scalar(
            url, "SELECT count(*) FROM messages WHERE conversation_id = CAST(:c AS uuid)", c=first
        )
        == 0
    )
    assert (
        db_scalar(
            url, "SELECT count(*) FROM messages WHERE conversation_id = CAST(:c AS uuid)", c=second
        )
        == 1
    )


def test_deleting_a_user_deletes_conversations_and_messages(migrated_database_url: str) -> None:
    url = migrated_database_url
    alice = new_user(url, "alice@example.com")
    conversation = new_conversation(url, alice)
    add_message(url, conversation, alice, 1)

    db_execute(url, "DELETE FROM users WHERE id = CAST(:u AS uuid)", u=alice)

    assert db_scalar(url, "SELECT count(*) FROM conversations") == 0
    assert db_scalar(url, "SELECT count(*) FROM messages") == 0


# --- ordering, answers and concurrency rules --------------------------------------------------


def test_sequence_numbers_are_unique_per_conversation(migrated_database_url: str) -> None:
    url = migrated_database_url
    alice = new_user(url, "alice@example.com")
    one, two = new_conversation(url, alice), new_conversation(url, alice)
    add_message(url, one, alice, 1)

    with pytest.raises(FAIL):
        add_message(url, one, alice, 1)
    add_message(url, two, alice, 1)  # the same number in another conversation is fine


def test_a_question_has_at_most_one_answer(migrated_database_url: str) -> None:
    url = migrated_database_url
    alice = new_user(url, "alice@example.com")
    conversation = new_conversation(url, alice)
    question = add_message(url, conversation, alice, 1)
    add_message(
        url, conversation, alice, 2, role="assistant", status="complete", reply_to_id=question
    )

    with pytest.raises(FAIL):
        add_message(
            url, conversation, alice, 3, role="assistant", status="complete", reply_to_id=question
        )


def test_an_answer_must_reply_to_a_question_of_the_same_conversation(
    migrated_database_url: str,
) -> None:
    url = migrated_database_url
    alice = new_user(url, "alice@example.com")
    one, two = new_conversation(url, alice), new_conversation(url, alice)
    question = add_message(url, one, alice, 1)

    with pytest.raises(FAIL):
        add_message(url, two, alice, 1, role="assistant", status="complete", reply_to_id=question)
    with pytest.raises(FAIL):  # an answer with no question
        add_message(url, one, alice, 2, role="assistant", status="complete")


def test_only_one_question_per_conversation_can_be_in_flight(migrated_database_url: str) -> None:
    url = migrated_database_url
    alice = new_user(url, "alice@example.com")
    one, two = new_conversation(url, alice), new_conversation(url, alice)
    add_message(url, one, alice, 1, status="processing")

    with pytest.raises(FAIL):
        add_message(url, one, alice, 2, status="processing")
    add_message(url, one, alice, 2, status="answered")  # finished questions are unlimited
    add_message(url, two, alice, 1, status="processing")  # another conversation is independent


def test_an_idempotency_key_is_unique_per_conversation_and_needs_its_hash(
    migrated_database_url: str,
) -> None:
    url = migrated_database_url
    alice = new_user(url, "alice@example.com")
    one, two = new_conversation(url, alice), new_conversation(url, alice)
    add_message(url, one, alice, 1, idempotency_key="k1", request_hash="a" * 64)

    with pytest.raises(FAIL):
        add_message(url, one, alice, 2, idempotency_key="k1", request_hash="a" * 64)
    with pytest.raises(FAIL):  # a key without a hash
        add_message(url, one, alice, 3, idempotency_key="k2")
    add_message(url, two, alice, 1, idempotency_key="k1", request_hash="b" * 64)


# --- value rules -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "values",
    [
        {"role": "system"},
        {"role": "user", "status": "complete"},
        {"role": "user", "status": "failed"},  # failed needs a reason
        {"role": "user", "status": "answered", "failure_reason": "timeout"},  # only if failed
        {"content": ""},
        {"content": "x" * 20_001},
        {"citations": '[{"label": "S1"}]'},  # citations belong to answers
        {"citations": "{}"},
    ],
    ids=["bad-role", "user-complete", "failed-no-reason", "reason-not-failed", "empty",
         "too-long", "citations-on-question", "citations-not-array"],
)  # fmt: skip
def test_message_values_are_constrained(migrated_database_url: str, values: dict[str, Any]) -> None:
    url = migrated_database_url
    alice = new_user(url, "alice@example.com")
    conversation = new_conversation(url, alice)

    with pytest.raises(FAIL):
        add_message(url, conversation, alice, 1, **values)


def test_failed_questions_carry_their_reason(migrated_database_url: str) -> None:
    url = migrated_database_url
    alice = new_user(url, "alice@example.com")
    conversation = new_conversation(url, alice)

    add_message(url, conversation, alice, 1, status="failed", failure_reason="timeout")

    assert db_scalar(url, "SELECT failure_reason FROM messages") == "timeout"


def test_an_assistant_message_cannot_hold_an_idempotency_key(migrated_database_url: str) -> None:
    url = migrated_database_url
    alice = new_user(url, "alice@example.com")
    conversation = new_conversation(url, alice)
    question = add_message(url, conversation, alice, 1)

    with pytest.raises(FAIL):
        add_message(
            url, conversation, alice, 2, role="assistant", status="complete", reply_to_id=question,
            idempotency_key="k", request_hash="a" * 64,
        )  # fmt: skip


@pytest.mark.parametrize("title", ["", "x" * 201, "line\nbreak", "tab\there", "bell\x07"])
def test_titles_are_bounded_and_single_line(migrated_database_url: str, title: str) -> None:
    alice = new_user(migrated_database_url, "alice@example.com")

    with pytest.raises(FAIL):
        new_conversation(migrated_database_url, alice, title)


def test_bengali_titles_are_accepted(migrated_database_url: str) -> None:
    alice = new_user(migrated_database_url, "alice@example.com")

    new_conversation(migrated_database_url, alice, "ইলিশ মাছ নিয়ে প্রশ্ন")


# --- document deletion scrubs stored citations -----------------------------------------------


def test_scrubbing_reduces_only_the_deleted_documents_citations_to_identifiers(
    migrated_database_url: str,
) -> None:
    url = migrated_database_url
    alice, bob = new_user(url, "alice@example.com"), new_user(url, "bob@example.com")
    conversation = new_conversation(url, alice)
    other_conversation = new_conversation(url, bob)
    doc, other_doc = str(uuid.uuid4()), str(uuid.uuid4())
    citations = json.dumps(
        [
            {
                "label": "S1", "document_id": doc, "chunk_id": str(uuid.uuid4()),
                "document_name": "secret.pdf", "heading": "Salaries",
            },
            {
                "label": "S2", "document_id": other_doc, "chunk_id": str(uuid.uuid4()),
                "document_name": "keep.pdf",
            },
        ]
    )  # fmt: skip
    question = add_message(url, conversation, alice, 1)
    add_message(url, conversation, alice, 2, role="assistant", status="complete",
                reply_to_id=question, citations=citations)  # fmt: skip
    other_question = add_message(url, other_conversation, bob, 1)
    add_message(url, other_conversation, bob, 2, role="assistant", status="complete",
                reply_to_id=other_question, citations=citations)  # fmt: skip

    from app.db.repositories.conversation_repository import ConversationRepository

    async def run() -> int:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        engine = create_async_engine(url)
        try:
            async with async_sessionmaker(engine)() as session:
                changed = await ConversationRepository(session).scrub_document_citations(
                    uuid.UUID(alice), uuid.UUID(doc)
                )
                await session.commit()
                return changed
        finally:
            await engine.dispose()

    import asyncio

    assert (
        asyncio.run(run()) == 1
    )  # only alice's message: bob's identical citations are not touched

    stored = db_rows(
        url,
        "SELECT user_id::text, citations FROM messages WHERE role = 'assistant' ORDER BY user_id",
    )
    by_user = {row[0]: row[1] for row in stored}
    scrubbed = by_user[alice]
    assert scrubbed[0] == {
        "label": "S1",
        "document_id": doc,
        "chunk_id": scrubbed[0]["chunk_id"],
        "removed": True,
    }
    assert "secret.pdf" not in json.dumps(scrubbed) and scrubbed[1]["document_name"] == "keep.pdf"
    assert by_user[bob][0]["document_name"] == "secret.pdf"


# --- migration -------------------------------------------------------------------------------


def test_the_migration_is_reversible_and_repeatable(
    postgres_url: str, migrated_database_url: str
) -> None:
    url = migrated_database_url
    alice = new_user(url, "alice@example.com")
    add_message(url, new_conversation(url, alice), alice, 1)

    downgrade(url, "0007")

    assert db_scalar(url, "SELECT to_regclass('public.messages')") is None
    assert db_scalar(url, "SELECT to_regclass('public.conversations')") is None
    assert db_scalar(url, "SELECT count(*) FROM users") == 1  # earlier tables untouched
    upgrade(url, "head")
    assert db_scalar(url, "SELECT to_regclass('public.messages')") is not None
    assert db_scalar(url, "SELECT count(*) FROM conversations") == 0
