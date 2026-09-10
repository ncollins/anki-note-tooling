"""
The worked examples from the grouping design, plus the renderer's universal invariants.

Lines A-E come straight from the design document
(`planning/20260901_configurable_input/CONFIGURABLE_INPUT_REVISED.md`): between them they cover
three group-key resolution branches, a tag that merges what the composite key would split,
a tag that splits what a `romanji` grouping would merge, and a line mixing tagged and
untagged substitutions.
"""

import re

import hypothesis
import hypothesis.strategies as st
import pytest

import anki_note_tooling.notes.annotation
import anki_note_tooling.notes.config
import anki_note_tooling.notes.renderer

SLOTS = ("kanji", "kana", "romanji")

CLOZE = anki_note_tooling.notes.config.Recipe(
    name="kanji_cloze",
    deck="Example Cloze Deck",
    model="Example Cloze",
    for_each="line",
    active="{{c$n::$kanji::$kana}}",
    fields={"Expression": "$note_text"},
)

PHRASE = anki_note_tooling.notes.config.Recipe(
    name="phrase",
    deck="Example Phrase Deck",
    model="Example Phrase",
    for_each="substitution",
    active='<font color="#0000ff">$kanji</font>',
    inactive="$kanji",
    fields={"Text": "$note_text", "Romanji": "$romanji"},
)

SOURCE = anki_note_tooling.notes.config.Source(
    name="podcast", language="ja", slots=SLOTS, notes=("kanji_cloze", "phrase")
)

# The worked examples, keyed as in the design document.
LINES = {
    "A": "[[本当::ほんとう::hontou]]はね、あなたが[[好::す::su]]き",
    "B": "[[帰::かえ::kae]]ろう、また[[帰::かえ::kae]]ろう",
    "C": "[[食::た::ta#eat]]べても[[食::く::ku#eat]]っても",
    "D": "[[帰::かえ::kae#a]]って[[返::かえ::kae#b]]す",
    "E": "[[水::みず::mizu#w]]と[[火::ひ::hi]]と[[水::みず::mizu#w]]",
}


def _render(annotated_text, recipe, source=SOURCE):
    return anki_note_tooling.notes.renderer.render(
        anki_note_tooling.notes.renderer.LineToRender(
            source_name=source.name,
            annotated_text=annotated_text,
            text="",
            language=source.language,
        ),
        recipe=recipe,
        source=source,
    )


def test_the_three_texts_are_distinct():
    """
    `$line_text`, `$annotated_text` and `$note_text` name three different things.

    They were one confusable pair before: the plain line and the rendered note both went by
    a variable called "text". Pinning all three together is what keeps a recipe author — and
    the next rename — from mistaking one for another.
    """
    recipe = CLOZE.model_copy(
        update={
            "fields": {
                "Expression": "$note_text",
                "Raw_expression": "$line_text",
                "Annotation": "$annotated_text",
            }
        }
    )
    line = anki_note_tooling.notes.renderer.LineToRender(
        source_name=SOURCE.name,
        annotated_text=LINES["A"],
        text="本当はね、あなたが好き",
        language=SOURCE.language,
    )

    (note,) = anki_note_tooling.notes.renderer.render(line, recipe=recipe, source=SOURCE)

    assert note.fields == {
        "Expression": "{{c1::本当::ほんとう}}はね、あなたが{{c2::好::す}}き",
        "Raw_expression": "本当はね、あなたが好き",
        "Annotation": "[[本当::ほんとう::hontou]]はね、あなたが[[好::す::su]]き",
    }


def test_note_text_cannot_be_used_inside_an_active_template():
    """
    `$note_text` is what an `active` template is building, so it is not available to one.

    The failure is a RenderError naming what *is* available, rather than an empty field
    discovered in Anki weeks later.
    """
    recipe = CLOZE.model_copy(update={"active": "$note_text"})
    with pytest.raises(anki_note_tooling.notes.renderer.RenderError) as excinfo:
        _render(LINES["A"], recipe)
    assert "$note_text" in str(excinfo.value)
    assert "$line_text" in str(excinfo.value)


@pytest.mark.parametrize(
    "line,expected_text",
    [
        ("A", "{{c1::本当::ほんとう}}はね、あなたが{{c2::好::す}}き"),
        ("B", "{{c1::帰::かえ}}ろう、また{{c2::帰::かえ}}ろう"),
        ("C", "{{c1::食::た}}べても{{c1::食::く}}っても"),
        ("D", "{{c1::帰::かえ}}って{{c2::返::かえ}}す"),
        ("E", "{{c1::水::みず}}と{{c2::火::ひ}}と{{c1::水::みず}}"),
    ],
)
def test_cloze_numbering(line, expected_text):
    notes = _render(LINES[line], CLOZE)
    assert [n.ref for n in notes] == ["kanji_cloze"]
    assert notes[0].fields["Expression"] == expected_text


@pytest.mark.parametrize(
    "line,expected_refs",
    [
        ("A", ["phrase:本当::ほんとう::hontou", "phrase:好::す::su"]),
        # identical in every slot, so they merge into one card
        ("B", ["phrase:帰::かえ::kae"]),
        # the tag merges what the composite key would split
        ("C", ["phrase:eat"]),
        # the tag splits two words that share a reading
        ("D", ["phrase:a", "phrase:b"]),
        ("E", ["phrase:w", "phrase:火::ひ::hi"]),
    ],
)
def test_phrase_fan_out(line, expected_refs):
    assert [n.ref for n in _render(LINES[line], PHRASE)] == expected_refs


def test_the_same_line_groups_differently_under_the_two_cardinalities():
    """
    Line B, with neither recipe setting `default_group_by`: cloze wants each blank tested
    separately, `phrase` wants one card per distinct word. Grouping is a recipe-level
    concern, not a source-level one.
    """
    assert len(_render(LINES["B"], CLOZE)) == 1
    assert _render(LINES["B"], CLOZE)[0].fields["Expression"].count("{{c") == 2
    assert len(_render(LINES["B"], PHRASE)) == 1


def test_only_the_first_member_of_a_group_is_highlighted():
    text = _render(LINES["E"], PHRASE)[0].fields["Text"]
    assert text.count('<font color="#0000ff">') == 1
    assert text == '<font color="#0000ff">水</font>と火と水'


def test_highlight_all_colours_every_member_of_the_group():
    recipe = PHRASE.model_copy(update={"highlight": "all"})
    text = _render(LINES["E"], recipe)[0].fields["Text"]
    assert text == '<font color="#0000ff">水</font>と火と<font color="#0000ff">水</font>'


def test_a_one_slot_source_numbers_cloze_blanks_the_same_way():
    """
    The commented-out `test_cloze_2` case, which the fixed-arity parser could not accept.
    Slot count is per source, so a one-slot source and a three-slot one coexist.
    """
    source = anki_note_tooling.notes.config.Source(
        name="cantonese_drama", language="zh-yue", slots=("hanzi",), notes=("cloze",)
    )
    recipe = anki_note_tooling.notes.config.Recipe(
        name="cloze",
        deck="d",
        model="m",
        for_each="line",
        active="{{c$n::$hanzi}}",
        fields={"Expression": "$note_text"},
    )
    notes = _render("[[好好#1]][[小姐]]，[[真係]][[好好#1]]", recipe, source)
    assert notes[0].fields["Expression"] == "{{c1::好好}}{{c2::小姐}}，{{c3::真係}}{{c1::好好}}"


def test_a_mistyped_variable_raises_rather_than_emptying_a_field():
    recipe = PHRASE.model_copy(update={"fields": {"Romanji": "$romaji"}})
    with pytest.raises(anki_note_tooling.notes.renderer.RenderError, match=r"\$romaji"):
        _render(LINES["A"], recipe)


def test_media_variables_expand_to_whole_fragments_or_nothing():
    from pathlib import Path

    recipe = PHRASE.model_copy(update={"fields": {"Image": "$image", "Audio": "$audio"}})
    with_media = anki_note_tooling.notes.renderer.render(
        anki_note_tooling.notes.renderer.LineToRender(
            source_name="podcast",
            annotated_text=LINES["A"],
            text="",
            language="ja",
            audio_path=Path("a/b [x].wav"),
            image_path=Path("a/c.png"),
        ),
        recipe=recipe,
        source=SOURCE,
    )
    assert with_media[0].fields == {"Image": '<img src="c.png">', "Audio": "[sound:bx.mp3]"}

    without_media = _render(LINES["A"], recipe)
    assert without_media[0].fields == {"Image": "", "Audio": ""}


# --- Universally quantified invariants -------------------------------------------------

# `<`, `>` and `&` are deliberately allowed: a slot value carrying markup is exactly what
# the escaping invariant below is about.
_slot_value = st.text(
    alphabet=st.characters(blacklist_characters="[]:#$\r\n"), min_size=1, max_size=4
)
# The text *between* annotations is line content, passed through verbatim exactly like
# `$english_line`, so markup is excluded here to keep the escaping property about slots.
_literal = st.text(alphabet=st.characters(blacklist_characters="[]$<>&"), max_size=6)
_tag = st.text(alphabet="abc", min_size=1, max_size=2)


def _annotation(arity: int) -> st.SearchStrategy[str]:
    """One `[[a::b::c]]`, optionally tagged. Slot values are distinct, since a surface
    form repeating its own first hint is rejected as a mis-typed annotation."""
    return st.tuples(
        st.lists(_slot_value, min_size=arity, max_size=arity, unique=True),
        st.one_of(st.none(), _tag),
    ).map(lambda v: f"[[{'::'.join(v[0])}{'' if v[1] is None else '#' + v[1]}]]")


def annotated_lines(arity: int = 3) -> st.SearchStrategy[str]:
    """Annotated lines of any shape the parser is meant to accept."""
    segment = st.tuples(_literal, _annotation(arity)).map("".join)
    return st.tuples(st.lists(segment, min_size=1, max_size=5).map("".join), _literal).map("".join)


@hypothesis.given(annotated_lines())
def test_parsing_round_trips_to_the_surface_text(annotated_text):
    parsed = anki_note_tooling.notes.annotation.parse(annotated_text, slots=SLOTS)
    expected = re.sub(r"\[\[([^:\]]*)(?:::[^\]]*)?\]\]", lambda m: m.group(1), annotated_text)
    assert parsed.surface_text() == expected


@hypothesis.given(annotated_lines())
def test_refs_are_distinct_within_a_line(annotated_text):
    refs = [n.ref for n in _render(annotated_text, PHRASE)]
    assert len(set(refs)) == len(refs)


@hypothesis.given(annotated_lines())
def test_cloze_numbers_are_contiguous_from_one_and_shared_within_a_group(annotated_text):
    parsed = anki_note_tooling.notes.annotation.parse(annotated_text, slots=SLOTS)
    text = _render(annotated_text, CLOZE)[0].fields["Expression"]
    numbers = [int(n) for n in re.findall(r"\{\{c(\d+)::", text)]

    assert len(numbers) == len(parsed.substitutions())
    assert set(numbers) == set(range(1, max(numbers) + 1))

    by_tag: dict[str, set[int]] = {}
    for substitution, number in zip(parsed.substitutions(), numbers):
        if substitution.tag is not None:
            by_tag.setdefault(substitution.tag, set()).add(number)
    assert all(len(shared) == 1 for shared in by_tag.values())


#: An escape sequence, or a character that has to be part of one.
_UNESCAPED_MARKUP = re.compile(r"[<>]|&(?!amp;|lt;|gt;|quot;|#x27;)")


@hypothesis.given(annotated_lines())
def test_slot_values_never_contribute_unescaped_markup(annotated_text):
    """
    Only the recipe's own templates may contribute `<`, `>` or `&` to a rendered field.
    Line variables are exempt — they routinely carry deliberate markup — but slot values
    are line fragments anki_note_tooling split out itself, so they are always escaped.
    """
    recipe = PHRASE.model_copy(update={"fields": {"Kanji": "$kanji", "Text": "$note_text"}})
    for note in _render(annotated_text, recipe):
        assert not _UNESCAPED_MARKUP.search(note.fields["Kanji"])
        # `$note_text` additionally carries the recipe's own `<font ...>` markup, so strip what
        # the template contributed before looking for anything the data smuggled in.
        contributed_by_recipe = (
            note.fields["Text"].replace('<font color="#0000ff">', "").replace("</font>", "")
        )
        assert not _UNESCAPED_MARKUP.search(contributed_by_recipe)


@pytest.mark.parametrize(
    "annotation",
    [
        "[[今日::きょう::kyou#kyou]]]",  # a mistyped third `]` swallowed into the tag
        "[[好::す::su#]]",  # a `#` with no tag after it
        "[[好::す::su#a b]]",
        "[[好::す::su#kyō]]",
        "[[好::す::su#今日]]",
        "[[好::す::su#a-b]]",
    ],
)
def test_a_grouping_tag_outside_ascii_alphanumerics_is_rejected(annotation):
    """
    A tag is note identity — it becomes a group key and from there a ref — so a typo has
    to be an error rather than a group of its own. `[[今日::きょう::kyou#kyou]]]` is the
    real case: the annotation regex swallows the stray `]`, leaving `kyou]` looking like
    a deliberate tag.
    """
    with pytest.raises(ValueError, match="grouping tag"):
        anki_note_tooling.notes.annotation.parse(annotation, slots=["kanji", "kana", "romanji"])


@pytest.mark.parametrize("tag", ["kyou", "A", "1", "supports2", "aB9"])
def test_ascii_alphanumeric_tags_are_accepted(tag):
    (substitution,) = anki_note_tooling.notes.annotation.parse(
        f"[[好::す::su#{tag}]]", slots=["kanji", "kana", "romanji"]
    ).substitutions()
    assert substitution.tag == tag
