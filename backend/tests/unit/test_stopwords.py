import re

from app.retrieval.stopwords import BANGLA_STOPWORDS, ENGLISH_STOPWORDS, STOPWORDS


def test_the_lists_are_lowercase_single_words() -> None:
    for word in STOPWORDS:
        assert word == word.casefold() and " " not in word and word.strip() == word


def test_each_language_list_is_not_empty_and_they_do_not_share_words() -> None:
    assert len(ENGLISH_STOPWORDS) > 80 and len(BANGLA_STOPWORDS) > 50
    assert not ENGLISH_STOPWORDS & BANGLA_STOPWORDS
    assert STOPWORDS == ENGLISH_STOPWORDS | BANGLA_STOPWORDS


def test_english_words_are_ascii_and_bangla_words_are_bengali_script() -> None:
    assert all(re.fullmatch(r"[a-z]+", w) for w in ENGLISH_STOPWORDS)
    assert all(re.fullmatch(r"[\u0980-\u09ff]+", w) for w in BANGLA_STOPWORDS)


def test_content_words_and_identifiers_are_never_stopwords() -> None:
    for word in ("error", "database", "err", "4721", "ইলিশ", "ঢাকা", "ধান", "kx"):
        assert word not in STOPWORDS
