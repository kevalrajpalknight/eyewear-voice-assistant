from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from aiglasses.assistant.glossary import Glossary

SYNTHETIC = [
    "Zorvex Quill",
    "Blenmark | blenmark, blen mark",
    "# a comment",
    "",
    "Tamsin Fernhollow",
]


@pytest.fixture
def glossary() -> "Glossary":
    pytest.importorskip("rapidfuzz")
    pytest.importorskip("jellyfish")
    from aiglasses.assistant.glossary import Glossary, parse_glossary

    return Glossary(parse_glossary(SYNTHETIC))


def test_parse_skips_comments_and_blank_lines_and_reads_spoken_forms() -> None:
    pytest.importorskip("rapidfuzz")
    from aiglasses.assistant.glossary import parse_glossary

    terms = parse_glossary(SYNTHETIC)
    assert [t.canonical for t in terms] == ["Zorvex Quill", "Blenmark", "Tamsin Fernhollow"]
    assert terms[1].forms == ("Blenmark", "blenmark", "blen mark")


def test_a_split_or_misspelled_term_is_corrected_and_the_correction_is_reported(
    glossary: "Glossary",
) -> None:
    text, corrections = glossary.correct("Please ask zorvexquill about the plan.")
    assert text == "Please ask Zorvex Quill about the plan."
    assert [(c.original, c.corrected) for c in corrections] == [("zorvexquill", "Zorvex Quill")]
    assert corrections[0].score == 100.0


def test_a_near_miss_is_corrected_by_fuzzy_match(glossary: "Glossary") -> None:
    text, corrections = glossary.correct("Tell Tamsin Fernhollo I said hi")
    assert text == "Tell Tamsin Fernhollow I said hi"
    assert corrections[0].reason == "fuzzy" and corrections[0].score >= 88


def test_ordinary_sentences_are_left_untouched(glossary: "Glossary") -> None:
    sentence = "Set a timer for ten minutes and remind me to take the bread out."
    assert glossary.correct(sentence) == (sentence, [])


def test_a_term_that_is_already_right_is_not_reported(glossary: "Glossary") -> None:
    assert glossary.correct("Call Blenmark today.") == ("Call Blenmark today.", [])


def test_sentence_punctuation_blocks_a_multi_word_match(glossary: "Glossary") -> None:
    text, corrections = glossary.correct("It is a zorvex. Quill is here.")
    assert corrections == [] and text == "It is a zorvex. Quill is here."


def test_short_and_common_words_are_never_replaced() -> None:
    pytest.importorskip("rapidfuzz")
    from aiglasses.assistant.glossary import Glossary, parse_glossary

    glossary = Glossary(parse_glossary(["Anu", "Will Smith"]))
    assert glossary.correct("And will you come?") == ("And will you come?", [])


def test_empty_glossary_changes_nothing() -> None:
    pytest.importorskip("rapidfuzz")
    from aiglasses.assistant.glossary import Glossary

    assert Glossary([]).correct("anything at all") == ("anything at all", [])
    assert Glossary([]).prompt_note() == ""


def test_prompt_note_lists_the_terms_and_warns_about_recognition_errors(
    glossary: "Glossary",
) -> None:
    note = glossary.prompt_note()
    assert "speech recognition" in note and "Zorvex Quill" in note and "Blenmark" in note
