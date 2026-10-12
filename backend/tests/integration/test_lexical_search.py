import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.retrieval.stopwords import STOPWORDS
from tests.conftest import ClientFactory
from tests.files import create_collection, make_user
from tests.helpers import db_execute, db_rows, db_scalar
from tests.search_helpers import chunk_texts, lexical_candidates, ready_doc, user_id_of

pytestmark = pytest.mark.integration


def search(client: TestClient, headers: dict[str, str], query: str, **kw: object):  # type: ignore[no-untyped-def]
    return lexical_candidates(client, user_id_of(client, headers), query, **kw)  # type: ignore[arg-type]


# --- what matches ----------------------------------------------------------------------------


def test_a_word_finds_the_chunks_that_contain_it_and_only_those(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "Rice farmers plant seedlings in the monsoon", "rice.txt")
    ready_doc(auth_client, alice, "Database indexes speed up queries", "db.txt")

    found = search(auth_client, alice, "seedlings")

    assert chunk_texts(migrated_database_url, found) == [
        "Rice farmers plant seedlings in the monsoon"
    ]


def test_matching_ignores_latin_case_but_the_stored_text_is_never_changed(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc = ready_doc(auth_client, alice, "The Kestrel-9 Controller was ORDERED", "k.txt")

    assert len(search(auth_client, alice, "kestrel-9 controller")) == 1
    assert len(search(auth_client, alice, "ordered")) == 1
    stored = db_scalar(
        migrated_database_url, "SELECT text FROM document_chunks WHERE document_id = :d", d=doc
    )
    assert stored == "The Kestrel-9 Controller was ORDERED"  # original casing and punctuation kept


def test_bangla_words_match_whole_including_vowel_signs_and_conjuncts(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "ইলিশ বাংলাদেশের জাতীয় মাছ এবং ক্ষুদ্র যুক্তাক্ষর", "bn.txt")
    ready_doc(auth_client, alice, "সুন্দরবন বিশ্বের বৃহত্তম ম্যানগ্রোভ বন", "bn2.txt")

    assert len(search(auth_client, alice, "ইলিশ")) == 1
    assert len(search(auth_client, alice, "ক্ষুদ্র")) == 1  # a conjunct is not split
    assert len(search(auth_client, alice, "ম্যানগ্রোভ")) == 1
    assert search(auth_client, alice, "ইল") == []  # a prefix is not a match: no stemming


def test_bangla_has_no_stemming_and_the_limitation_is_real(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "বাংলাদেশের রাজধানী ঢাকা", "bn.txt")

    assert len(search(auth_client, alice, "বাংলাদেশের")) == 1
    assert search(auth_client, alice, "বাংলাদেশ") == []  # the inflected form is a different token


def test_mixed_script_text_and_queries(auth_client: TestClient, migrated_database_url: str) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "Query ধীর হলে প্রথমে EXPLAIN চালিয়ে দেখুন", "mx.txt")

    assert len(search(auth_client, alice, "explain")) == 1
    assert len(search(auth_client, alice, "ধীর")) == 1
    assert len(search(auth_client, alice, "EXPLAIN চালিয়ে")) == 1


@pytest.mark.parametrize(
    "query",
    ["ERR-4721", "err-4721", "error ERR-4721?!", "What does ERR-4721 mean?"],
    ids=["exact", "lowercase", "punctuated", "question"],
)
def test_the_exact_identifier_is_found_first_however_it_is_written(
    auth_client: TestClient, migrated_database_url: str, query: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "Fix for ERR-4721: restart the worker", "e.txt")
    ready_doc(auth_client, alice, "Fix for ERR-4712: clear the cache", "f.txt")

    found = search(auth_client, alice, query)

    assert chunk_texts(migrated_database_url, found)[0] == "Fix for ERR-4721: restart the worker"


def test_a_spaced_identifier_cannot_tell_similar_codes_apart(auth_client: TestClient) -> None:
    """PostgreSQL's parser reads `ERR-4721` as the words `err` and `-4721`; written with a space
    the digits become a different token. The limitation is real and documented."""
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "Fix for ERR-4721: restart the worker", "e.txt")
    ready_doc(auth_client, alice, "Fix for ERR-4712: clear the cache", "f.txt")

    found = search(auth_client, alice, "ERR 4721")

    assert len(found) == 2 and found[0].score == found[1].score  # only `err` matched, equally


def test_the_exact_identifier_outranks_a_near_identical_one(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "Fix for ERR-4712: clear the cache", "f.txt")
    ready_doc(auth_client, alice, "Fix for ERR-4721: restart the worker", "e.txt")

    found = search(auth_client, alice, "What does ERR-4721 mean?")

    assert chunk_texts(migrated_database_url, found)[0] == "Fix for ERR-4721: restart the worker"


def test_a_rare_term_outweighs_common_words_in_the_query(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    for i in range(6):
        ready_doc(
            auth_client, alice, f"what does the report say about the numbers {i}?", f"c{i}.txt"
        )
    ready_doc(auth_client, alice, "the hyperkalemia protocol", "rare.txt")

    found = search(auth_client, alice, "what does the report say about hyperkalemia")

    assert chunk_texts(migrated_database_url, found)[0] == "the hyperkalemia protocol"


def test_quoted_phrases_must_match_as_adjacent_words(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "the national fish of Bangladesh is the hilsa", "adjacent.txt")
    ready_doc(
        auth_client,
        alice,
        "fish is national in a way; Bangladesh and the national mood",
        "apart.txt",
    )

    phrase = search(auth_client, alice, '"national fish"')
    words = search(auth_client, alice, "national fish")

    assert chunk_texts(migrated_database_url, phrase) == [
        "the national fish of Bangladesh is the hilsa"
    ]
    assert len(words) == 2


def test_a_bangla_phrase(auth_client: TestClient, migrated_database_url: str) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "বাংলাদেশের রাজধানী ঢাকা একটি বড় শহর", "a.txt")
    ready_doc(auth_client, alice, "ঢাকা রাজধানী এবং বাংলাদেশের মানুষ", "b.txt")

    found = search(auth_client, alice, '"রাজধানী ঢাকা"')

    assert chunk_texts(migrated_database_url, found) == ["বাংলাদেশের রাজধানী ঢাকা একটি বড় শহর"]


def test_results_are_ordered_by_score_then_chunk_id_deterministically(
    auth_client: TestClient,
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    for i in range(5):
        ready_doc(auth_client, alice, f"shared keyword number {i}", f"d{i}.txt")

    first = search(auth_client, alice, "shared keyword")
    second = search(auth_client, alice, "shared keyword")

    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    keys = [(-c.score, str(c.chunk_id)) for c in first]
    assert keys == sorted(keys)


# --- queries that must not hurt -------------------------------------------------------------


@pytest.mark.parametrize("query", ["", "   ", "?!...", "--- *** ---", '""', "( ) [ ] { }", "‍"])
def test_empty_and_punctuation_only_queries_return_nothing_without_touching_the_database(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch, query: str
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "some content", "a.txt")
    user = user_id_of(auth_client, alice)
    executed: list[object] = []
    original = AsyncSession.execute

    async def spy(self, statement, *a, **kw):  # type: ignore[no-untyped-def]
        executed.append(statement)
        return await original(self, statement, *a, **kw)

    monkeypatch.setattr(AsyncSession, "execute", spy)

    assert lexical_candidates(auth_client, user, query) == []
    assert executed == []


@pytest.mark.parametrize(
    "query",
    [
        "'; DROP TABLE document_chunks; --",
        '" OR 1=1 --',
        "a' || (SELECT pg_sleep(5)) || '",
        "\\'; SELECT * FROM users; --",
        "$$ ; DELETE FROM documents; $$",
        "word:* & !neg | (a <-> b)",
    ],
)
def test_hostile_query_text_is_data_never_sql(
    auth_client: TestClient, migrated_database_url: str, query: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "ordinary content to keep", "a.txt")

    lexical_candidates(auth_client, user_id_of(auth_client, alice), query)

    assert db_scalar(migrated_database_url, "SELECT count(*) FROM document_chunks") >= 1
    assert db_scalar(migrated_database_url, "SELECT count(*) FROM documents") >= 1


def test_a_very_long_query_is_bounded_by_the_term_limit(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "needle in the haystack", "a.txt")
    query = " ".join(f"term{i}" for i in range(500)) + " needle"

    assert len(search(auth_client, alice, query, max_terms=12)) <= 50  # runs, bounded, no error


def test_the_candidate_limit_is_enforced(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    for i in range(8):
        ready_doc(auth_client, alice, f"limit keyword {i}", f"d{i}.txt")

    assert len(search(auth_client, alice, "limit keyword", limit=3)) == 3
    assert len(search(auth_client, alice, "limit keyword", limit=50)) == 8


# --- isolation, status and lifecycle --------------------------------------------------------


def test_a_user_never_matches_another_users_chunks(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    bob = make_user(auth_client, "bob@example.com")
    ready_doc(auth_client, alice, "alice confidential zebra figures", "a.txt")
    ready_doc(auth_client, bob, "bob public notes", "b.txt")

    assert search(auth_client, bob, "zebra") == []
    assert search(auth_client, bob, "confidential") == []
    assert len(search(auth_client, alice, "zebra")) == 1


def test_scores_and_counts_do_not_depend_on_other_users_content(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    bob = make_user(auth_client, "bob@example.com")
    ready_doc(auth_client, alice, "shared term appears here once", "a.txt")
    before = search(auth_client, alice, "shared term")
    for i in range(10):  # another tenant makes the term very common in the whole database
        ready_doc(auth_client, bob, f"shared term shared term number {i}", f"b{i}.txt")

    after = search(auth_client, alice, "shared term")

    assert [(c.chunk_id, c.score) for c in after] == [(c.chunk_id, c.score) for c in before]


def test_only_ready_documents_are_matched(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc = ready_doc(auth_client, alice, "findable content", "a.txt")
    assert len(search(auth_client, alice, "findable")) == 1

    for status in ("embedding", "chunked", "failed", "pending"):
        db_execute(
            migrated_database_url,
            "UPDATE documents SET status = :s, failure_reason = NULL, "
            "error_message = NULL WHERE id = :d",
            s=status,
            d=doc,
        )
        assert search(auth_client, alice, "findable") == [], status
    db_execute(migrated_database_url, "UPDATE documents SET status = 'ready' WHERE id = :d", d=doc)
    assert len(search(auth_client, alice, "findable")) == 1


def test_deleted_documents_and_chunks_disappear(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc = ready_doc(auth_client, alice, "temporary words here", "a.txt")
    other = ready_doc(auth_client, alice, "temporary words elsewhere", "b.txt")
    assert len(search(auth_client, alice, "temporary")) == 2

    db_execute(migrated_database_url, "DELETE FROM document_chunks WHERE document_id = :d", d=other)
    assert len(search(auth_client, alice, "temporary")) == 1
    assert auth_client.delete(f"/api/v1/documents/{doc}", headers=alice).status_code == 204
    assert search(auth_client, alice, "temporary") == []


def test_document_and_collection_filters(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    collection = create_collection(auth_client, alice, "Papers")
    inside = ready_doc(
        auth_client, alice, "filter topic inside", "in.txt", collection_ids=[collection]
    )
    outside = ready_doc(auth_client, alice, "filter topic outside", "out.txt")
    user = user_id_of(auth_client, alice)

    assert len(search(auth_client, alice, "filter topic")) == 2
    only_inside = lexical_candidates(
        auth_client, user, "filter topic", collection_id=uuid.UUID(collection)
    )
    only_outside = lexical_candidates(
        auth_client, user, "filter topic", document_ids=[uuid.UUID(outside)]
    )
    assert [str(c.document_id) for c in only_inside] == [inside]
    assert [str(c.document_id) for c in only_outside] == [outside]


def test_a_collection_of_another_user_matches_nothing(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    bob = make_user(auth_client, "bob@example.com")
    collection = create_collection(auth_client, alice, "Private")
    ready_doc(
        auth_client, alice, "inside the private collection", "a.txt", collection_ids=[collection]
    )

    stolen = lexical_candidates(
        auth_client,
        user_id_of(auth_client, bob),
        "private collection",
        collection_id=uuid.UUID(collection),
    )

    assert stolen == []


# --- the schema ------------------------------------------------------------------------------


def test_chunks_get_their_owner_and_search_vector_from_the_database(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc = ready_doc(auth_client, alice, "owner trigger content", "a.txt")

    row = db_rows(
        migrated_database_url,
        "SELECT c.user_id = d.user_id, c.search_vector @@ phraseto_tsquery('simple', 'trigger') "
        "FROM document_chunks c JOIN documents d ON d.id = c.document_id WHERE d.id = :d",
        d=doc,
    )[0]
    assert tuple(row) == (True, True)


def test_a_chunk_cannot_claim_another_owner(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    bob = make_user(auth_client, "bob@example.com")
    doc = ready_doc(auth_client, alice, "protected ownership", "a.txt")
    bob_id = str(user_id_of(auth_client, bob))

    with pytest.raises((IntegrityError, DBAPIError), match="fk_document_chunks_owner"):
        db_execute(
            migrated_database_url,
            "UPDATE document_chunks SET user_id = :u WHERE document_id = :d",
            u=bob_id,
            d=doc,
        )


def test_the_search_vector_is_generated_and_cannot_be_written(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc = ready_doc(auth_client, alice, "generated column", "a.txt")

    with pytest.raises((IntegrityError, DBAPIError), match="generated"):
        db_execute(
            migrated_database_url,
            "UPDATE document_chunks SET search_vector = to_tsvector('simple', 'x') "
            "WHERE document_id = :d",
            d=doc,
        )


def test_the_composite_index_can_serve_the_lexical_predicate(
    auth_client_factory: ClientFactory, migrated_database_url: str
) -> None:
    client = auth_client_factory()
    alice = make_user(client, "alice@example.com")
    ready_doc(client, alice, "index probe content", "a.txt")
    user = str(user_id_of(client, alice))
    db_execute(migrated_database_url, "ANALYZE document_chunks")

    plan = asyncio.run(_plan(migrated_database_url, user))
    index = db_scalar(
        migrated_database_url,
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_document_chunks_search'",
    )
    assert "USING gin" in index and "user_id" in index and "search_vector" in index
    assert "ix_document_chunks_search" in plan  # the planner can use it for this predicate


async def _plan(url: str, user: str) -> str:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SET LOCAL enable_seqscan = off"))
            rows = await connection.execute(
                text(
                    "EXPLAIN SELECT id FROM document_chunks WHERE user_id = CAST(:u AS uuid) "
                    "AND search_vector @@ phraseto_tsquery('simple', 'probe')"
                ),
                {"u": user},
            )
            return " ".join(row[0] for row in rows)
    finally:
        await engine.dispose()


def test_original_text_is_never_normalised_for_indexing(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    text = "ইলিশ... “Quoted” ZWJ‍ test, Mixed-Case!"
    doc = ready_doc(auth_client, alice, text, "a.txt")

    stored = db_scalar(
        migrated_database_url, "SELECT text FROM document_chunks WHERE document_id = :d", d=doc
    )
    assert stored == text


def test_function_words_do_not_create_candidates(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    user = user_id_of(auth_client, alice)
    ready_doc(auth_client, alice, "The tide is high and the boat is in the harbour", "a.txt")

    noisy = lexical_candidates(auth_client, user, "What is the")
    filtered = lexical_candidates(auth_client, user, "What is the", stopwords=STOPWORDS)

    assert len(noisy) == 1  # without the filter, "is" and "the" match
    assert filtered == []  # nothing distinctive left


def test_a_distinctive_word_still_matches_when_function_words_are_removed(
    auth_client: TestClient,
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    user = user_id_of(auth_client, alice)
    wanted = ready_doc(auth_client, alice, "The harbour of Mongla is the second port", "a.txt")
    ready_doc(auth_client, alice, "The weather is fine and the sky is blue", "b.txt")

    found = lexical_candidates(auth_client, user, "What is the harbour?", stopwords=STOPWORDS)

    assert [str(c.document_id) for c in found] == [wanted]


def test_bangla_function_words_are_removed_too(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    user = user_id_of(auth_client, alice)
    ready_doc(auth_client, alice, "এটি একটি সাধারণ বাক্য যা কেন হয়", "a.txt")

    assert lexical_candidates(auth_client, user, "কেন এটি কী", stopwords=STOPWORDS) == []
    assert len(lexical_candidates(auth_client, user, "কেন এটি কী")) == 1


def test_quoted_phrases_keep_their_function_words(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    user = user_id_of(auth_client, alice)
    wanted = ready_doc(auth_client, alice, "to be or not to be, that is the question", "a.txt")

    found = lexical_candidates(auth_client, user, '"to be or not to be"', stopwords=STOPWORDS)

    assert [str(c.document_id) for c in found] == [wanted]


def test_a_rare_word_is_found_even_in_a_query_with_very_many_other_words(
    auth_client: TestClient,
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    user = user_id_of(auth_client, alice)
    wanted = ready_doc(auth_client, alice, "the outage code was zx9981 on the gateway", "a.txt")
    ready_doc(auth_client, alice, " ".join(f"filler{i:03d}" for i in range(60)), "b.txt")
    padding = " ".join(f"aaa{i:03d}" for i in range(80))  # sorts before the identifier

    found = lexical_candidates(auth_client, user, f"{padding} zx9981")

    assert str(found[0].document_id) == wanted


def test_a_quoted_phrase_survives_a_query_with_very_many_words(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    user = user_id_of(auth_client, alice)
    wanted = ready_doc(auth_client, alice, "restart the worker quickly", "a.txt")
    padding = " ".join(f"aaa{i:03d}" for i in range(80))

    found = lexical_candidates(auth_client, user, f'{padding} "restart the worker"')

    assert [str(c.document_id) for c in found] == [wanted]


def test_frequencies_count_only_ready_documents(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    user = user_id_of(auth_client, alice)
    for i in range(3):
        ready_doc(auth_client, alice, f"common token alpha{i}", f"c{i}.txt")
    other = ready_doc(auth_client, alice, "common token omega", "o.txt")
    before = {str(c.document_id): c.score for c in lexical_candidates(auth_client, user, "common")}

    db_execute(
        migrated_database_url, "UPDATE documents SET status = 'failed' WHERE id = :d", d=other
    )
    after = {str(c.document_id): c.score for c in lexical_candidates(auth_client, user, "common")}

    assert other not in after and len(after) == 3
    assert before and all(v > 0 for v in after.values())
