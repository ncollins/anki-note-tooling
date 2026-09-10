"""
The three-way diff, on the shapes of change positional refs cannot survive: adding a
substitution to a line, and removing one.
"""

from pathlib import Path

import anki_note_tooling.notes.config
import anki_note_tooling.notes.note
import anki_note_tooling.notes.reconcile
import anki_note_tooling.notes.renderer

SOURCE = anki_note_tooling.notes.config.Source(
    name="podcast", language="ja", slots=("kanji", "kana", "romanji"), notes=("phrase",)
)
PHRASE = anki_note_tooling.notes.config.Recipe(
    name="phrase",
    deck="d",
    model="m",
    for_each="substitution",
    active="$kanji",
    fields={"Text": "$note_text", "Romanji": "$romanji"},
)

ORIGINAL = "[[水::みず::mizu]]と[[火::ひ::hi]]"
EDITED = "[[水::みず::mizu]]と[[土::つち::tsuchi]]"


def _render(annotated_text):
    return anki_note_tooling.notes.renderer.render(
        anki_note_tooling.notes.renderer.LineToRender(
            source_name="podcast", annotated_text=annotated_text, text="", language="ja"
        ),
        recipe=PHRASE,
        source=SOURCE,
    )


def _as_stored(notes):
    return [
        anki_note_tooling.notes.note.StoredNote(
            row_id=i, anki_note_id=1000 + i, ref=note.ref, record=note.to_record()
        )
        for i, note in enumerate(notes)
    ]


def test_editing_a_line_updates_what_survives_and_never_repoints_what_does_not():
    stored = _as_stored(_render(ORIGINAL))
    result = anki_note_tooling.notes.reconcile.reconcile(_render(EDITED), stored)

    assert [n.ref for n in result.to_insert] == ["phrase:土::つち::tsuchi"]
    assert [(s.anki_note_id, n.ref) for s, n in result.to_update] == [
        (1000, "phrase:水::みず::mizu")
    ]
    # The dropped substitution's note is reported, not silently repointed or deleted.
    assert [s.ref for s in result.orphaned] == ["phrase:火::ひ::hi"]


def test_an_unchanged_line_is_all_updates():
    rendered = _render(ORIGINAL)
    result = anki_note_tooling.notes.reconcile.reconcile(rendered, _as_stored(rendered))
    assert (result.to_insert, result.orphaned, result.unresolved) == ([], [], [])
    assert len(result.to_update) == len(rendered)


def test_a_line_with_nothing_stored_is_all_inserts():
    rendered = _render(ORIGINAL)
    result = anki_note_tooling.notes.reconcile.reconcile(rendered, [])
    assert result.to_insert == rendered
    assert result.summary() == "2 to insert, 0 to update, 0 orphaned, 0 unresolved"


def test_a_row_whose_anki_note_is_gone_is_surfaced_not_updated():
    """Two production rows point at Anki notes that no longer exist."""
    rendered = _render(ORIGINAL)
    stored = _as_stored(rendered)
    result = anki_note_tooling.notes.reconcile.reconcile(
        rendered, stored, resolvable=frozenset({1000})
    )
    assert [s.row_id for s in result.unresolved] == [1]
    assert [s.row_id for s, _ in result.to_update] == [0]


def test_a_row_written_before_refs_existed_is_matched_by_its_legacy_misc_ref():
    """232 production rows have no `internal_note_ref` at all; those stay unresolved."""
    rendered = _render(ORIGINAL)
    stored = [
        anki_note_tooling.notes.note.StoredNote(row_id=7, anki_note_id=99, ref=None, record={}),
    ]
    result = anki_note_tooling.notes.reconcile.reconcile(rendered, stored)
    assert [s.row_id for s in result.unresolved] == [7]
    assert len(result.to_insert) == len(rendered)


def test_media_is_not_stored_in_the_record():
    """It is derived from the line, so storing it would be a second copy to keep in sync."""
    note = anki_note_tooling.notes.renderer.render(
        anki_note_tooling.notes.renderer.LineToRender(
            source_name="podcast",
            annotated_text=ORIGINAL,
            text="",
            language="ja",
            audio_path=Path("a.wav"),
        ),
        recipe=PHRASE,
        source=SOURCE,
    )[0]
    assert note.media
    assert "media" not in note.to_record()
