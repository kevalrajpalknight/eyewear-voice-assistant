from aiglasses.assistant.chunker import split_clauses


def test_short_sentence_is_one_clause() -> None:
    assert split_clauses("Turn off the kitchen lights.", 6) == ["Turn off the kitchen lights."]


def test_first_clause_is_three_to_six_words_even_without_punctuation() -> None:
    text = "book a table for four people at seven thirty on the twelfth of march"
    clauses = split_clauses(text, 6)
    assert 3 <= len(clauses[0].split()) <= 6
    assert " ".join(clauses) == text


def test_cuts_at_sentence_ends_and_commas() -> None:
    clauses = split_clauses("Honestly, I have no idea what to make for dinner. Do you?", 6)
    assert clauses[0].startswith("Honestly,") and clauses[-1] == "Do you?"
    assert len(clauses) >= 3


def test_a_comma_too_early_does_not_make_a_tiny_first_clause() -> None:
    assert split_clauses("Well, that is fine.", 6) == ["Well, that is fine."]


def test_decimals_times_and_abbreviations_are_not_boundaries() -> None:
    assert split_clauses("Call me at 7 p.m. today.", 6) == ["Call me at 7 p.m. today."]
    assert split_clauses("It is 3.5 now.", 6) == ["It is 3.5 now."]
    assert split_clauses("Dr. Rao will call you.", 6) == ["Dr. Rao will call you."]


def test_breaks_before_a_conjunction_in_a_long_run() -> None:
    text = "Then I walked over to the shop and I waited outside for a long time but nobody came"
    clauses = split_clauses(text, 6)
    assert any(c.startswith("and") or c.startswith("but") for c in clauses)
    assert " ".join(clauses) == text


def test_nothing_is_lost_and_every_clause_is_bounded() -> None:
    text = (
        "The new library opens at nine in the morning and closes at six, so if you want a quiet "
        "seat you should probably arrive early."
    )
    clauses = split_clauses(text, 6)
    assert " ".join(clauses) == text
    assert all(len(c.split()) <= 10 for c in clauses) and len(clauses) >= 3


def test_empty_and_whitespace() -> None:
    assert split_clauses("", 6) == [] and split_clauses("   ", 6) == []
