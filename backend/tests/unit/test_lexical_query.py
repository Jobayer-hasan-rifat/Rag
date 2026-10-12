import pytest

from app.retrieval.lexical_query import MAX_PHRASE_CHARS, MAX_PHRASES, parse_lexical_query


def test_plain_words_pass_through_untouched() -> None:
    query = parse_lexical_query("ইলিশ ধরা কবে নিষিদ্ধ থাকে?")

    assert query.words == "ইলিশ ধরা কবে নিষিদ্ধ থাকে?"  # no rewriting: PostgreSQL tokenises
    assert query.phrases == ()
    assert query.searchable


def test_quoted_segments_become_phrases_and_the_rest_stays_words() -> None:
    query = parse_lexical_query('ইলিশ "ধরা নিষিদ্ধ" কবে')

    assert query.phrases == ("ধরা নিষিদ্ধ",)
    assert query.words == "ইলিশ কবে"


@pytest.mark.parametrize(
    "text",
    ['find "national fish" now', "find “national fish” now", "find «national fish» now"],
    ids=["ascii", "curly", "guillemets"],
)
def test_common_quote_styles_are_recognised(text: str) -> None:
    query = parse_lexical_query(text)

    assert query.phrases == ("national fish",) and query.words == "find now"


def test_several_phrases_and_unmatched_quotes() -> None:
    assert parse_lexical_query('"a b" and "c d"').phrases == ("a b", "c d")
    unmatched = parse_lexical_query('look for "open phrase')
    assert unmatched.phrases == () and "open phrase" in unmatched.words


def test_empty_phrases_are_dropped() -> None:
    query = parse_lexical_query('word "" other "   "')

    assert query.phrases == () and query.words == "word other"


def test_phrase_count_and_size_are_bounded() -> None:
    many = " ".join(f'"phrase {i}"' for i in range(MAX_PHRASES + 3))
    long = f'"{"w " * (MAX_PHRASE_CHARS // 2 + 5)}"'

    assert len(parse_lexical_query(many).phrases) == MAX_PHRASES
    assert parse_lexical_query(long).phrases == ()  # too long: falls back to ordinary words
    assert parse_lexical_query(long).words.strip() != ""


@pytest.mark.parametrize("text", ["", "   ", "?!...", "--- *** ---", '""', "‍‌", "( ) [ ] { }"])
def test_queries_with_nothing_to_match_are_not_searchable(text: str) -> None:
    assert not parse_lexical_query(text).searchable


@pytest.mark.parametrize("text", ["a", "7", "ক", "ERR-4721", "😀 café", "ক্ষ"])
def test_anything_with_letters_digits_or_bengali_is_searchable(text: str) -> None:
    assert parse_lexical_query(text).searchable


def test_a_phrase_alone_makes_a_query_searchable() -> None:
    query = parse_lexical_query('"রাজধানী ঢাকা"')

    assert query.words == "" and query.phrases == ("রাজধানী ঢাকা",) and query.searchable


def test_sql_looking_text_is_just_text() -> None:
    query = parse_lexical_query("'; DROP TABLE document_chunks; --")

    assert query.words == "'; DROP TABLE document_chunks; --" and query.searchable
