"""
The worked example in the README, pinned to what the example config actually renders.

Documentation that shows output goes stale silently — a changed template or a changed escaping
rule leaves the README describing something the tool no longer does. These are the exact
strings the README quotes.
"""

import html
from pathlib import Path

import anki_note_tooling.config
import anki_note_tooling.notes.config
import anki_note_tooling.notes.renderer

EXAMPLE_CONFIG_DIR = anki_note_tooling.config.EXAMPLE_CONFIG_DIR

#: Both 犬 carry `#inu`, which is what the worked example exists to demonstrate: they are one
#: word in two places rather than two words that happen to match.
LINE = anki_note_tooling.notes.renderer.LineToRender(
    source_name="podcast",
    annotated_text=(
        "[[大きい::おおきい::ookii]][[犬::いぬ::inu#inu]]と"
        "[[小さい::ちいさい::chiisai]][[犬::いぬ::inu#inu]]"
    ),
    text="大きい犬と小さい犬",
    language="ja",
    english_line="a big dog and a small dog",
    source_tag="episode_12",
    audio_path=Path("/files/media/episode_12_01.mp3"),
    image_path=Path("/files/media/episode_12_01.png"),
)


def _notes():
    config = anki_note_tooling.notes.config.load(EXAMPLE_CONFIG_DIR)
    return anki_note_tooling.notes.renderer.render_all(LINE, config=config)


def test_the_example_line_makes_one_cloze_note_and_one_phrase_note_per_group():
    """Four substitutions, but three phrase notes: the two tagged 犬 are one group."""
    assert [note.recipe for note in _notes()] == ["cloze", "phrase", "phrase", "phrase"]


def test_the_cloze_note_matches_the_readme():
    cloze = next(note for note in _notes() if note.recipe == "cloze")

    assert cloze.deck == "Example Cloze Deck"
    assert cloze.tags == ("episode_12",)
    assert cloze.fields == {
        "Expression": "{{c1::大きい::おおきい}}{{c2::犬::いぬ}}と{{c3::小さい::ちいさい}}{{c2::犬::いぬ}}",
        "Raw_expression": "大きい犬と小さい犬",
        "Translation": "a big dog and a small dog",
        "Audio": "[sound:episode_12_01.mp3]",
        "Images": '<img src="episode_12_01.png">',
    }


def test_the_grouping_tag_gives_both_occurrences_one_cloze_number():
    """
    The point of the example. Untagged, the second 犬 would be `c4` — a second blank to answer
    on its own; tagged, both are `c2`, one blank in two places that is hidden and revealed
    together.
    """
    cloze = next(note for note in _notes() if note.recipe == "cloze")

    assert cloze.fields["Expression"].count("{{c2::犬::いぬ}}") == 2
    assert "c4" not in cloze.fields["Expression"]


def test_the_grouping_tag_names_the_phrase_note_it_produces():
    """A tagged group's ref is the tag, where an untagged one is its slot values joined."""
    refs = [note.ref for note in _notes()]

    assert refs == [
        "cloze",
        "phrase:大きい::おおきい::ookii",
        "phrase:inu",
        "phrase:小さい::ちいさい::chiisai",
    ]


def test_the_phrase_notes_match_the_readme():
    """One note per group, each colouring only the group it tests."""
    phrases = [note for note in _notes() if note.recipe == "phrase"]

    assert [note.fields["Text"] for note in phrases] == [
        '<font color="#0000ff">大きい</font>犬と小さい犬',
        '大きい<font color="#0000ff">犬</font>と小さい犬',
        '大きい犬と<font color="#0000ff">小さい</font>犬',
    ]
    # `$reading` refers to the focused group, not the whole line.
    assert [note.fields["Reading"] for note in phrases] == ["おおきい", "いぬ", "ちいさい"]


def test_only_the_first_occurrence_of_a_group_is_highlighted():
    """`highlight = "first"` is the default; the second 犬 stays plain in its own note."""
    inu = next(note for note in _notes() if note.ref == "phrase:inu")

    assert inu.fields["Text"] == '大きい<font color="#0000ff">犬</font>と小さい犬'
    assert inu.fields["Text"].count("<font") == 1


def test_the_phrase_text_is_markup_rather_than_escaped_text():
    """
    The README shows the stored and escaped forms side by side; this pins both.

    Anki has to receive the tags, not a rendering of them. If the escaped form ever became the
    stored one, cards would show `<font …>` as literal text instead of a coloured word.
    """
    first, second, third = (note.fields["Text"] for note in _notes() if note.recipe == "phrase")

    assert (
        html.escape(first)
        == "&lt;font color=&quot;#0000ff&quot;&gt;大きい&lt;/font&gt;犬と小さい犬"
    )
    assert (
        html.escape(second)
        == "大きい&lt;font color=&quot;#0000ff&quot;&gt;犬&lt;/font&gt;と小さい犬"
    )
    assert (
        html.escape(third)
        == "大きい犬と&lt;font color=&quot;#0000ff&quot;&gt;小さい&lt;/font&gt;犬"
    )
