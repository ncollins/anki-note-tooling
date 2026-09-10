"""
Adoption of notes that are already in Anki, against a real database.

The collection itself is out of reach here, so the two functions that read it are patched;
everything else — the tables, the rendering, the note records — is real, because what these
tests are about is which rows come out and whether a line that cannot be matched stops the
whole run.
"""

from pathlib import Path
from unittest import mock

import anki.collection
import pytest
import sqlalchemy

import anki_note_tooling.anki_lib.queries
import anki_note_tooling.anki_lib.utils
import anki_note_tooling.backfill
import anki_note_tooling.config
import anki_note_tooling.data.queries
import anki_note_tooling.data.tables
import anki_note_tooling.lib.media
import anki_note_tooling.lib.types
import anki_note_tooling.notes.config
import anki_note_tooling.notes.renderer

SOURCE_NAME = "podcast"

CLOZE_RECIPE = anki_note_tooling.notes.config.Recipe(
    name="kanji_cloze",
    deck="Example Cloze Deck",
    model="Example Cloze",
    for_each="line",
    active="{{c$n::$kanji::$kana}}",
    fields={"Expression": "$note_text"},
)

PHRASE_RECIPE = anki_note_tooling.notes.config.Recipe(
    name="jp_phrase",
    deck="Example Phrase Deck",
    model="Example Phrase",
    for_each="substitution",
    active="<b>$kanji</b>",
    inactive="$kanji",
    fields={"Text": "$note_text", "Romanji": "$romanji"},
)

SOURCE = anki_note_tooling.notes.config.Source(
    name=SOURCE_NAME,
    language="ja",
    slots=("kanji", "kana", "romanji"),
    notes=("kanji_cloze", "jp_phrase"),
)

NOTE_CONFIG = anki_note_tooling.notes.config.NoteConfig(
    sources={SOURCE.name: SOURCE},
    recipes={CLOZE_RECIPE.name: CLOZE_RECIPE, PHRASE_RECIPE.name: PHRASE_RECIPE},
)

ANNOTATED = "[[本::ほん::hon]] を [[読む::よむ::yomu]]"


def _config(tmp_path: Path) -> anki_note_tooling.config.Config:
    return anki_note_tooling.config.Config(
        anki=anki_note_tooling.config.AnkiConfig(profile_dir=tmp_path),
        database=anki_note_tooling.config.DatabaseConfig(
            sqlite_file=tmp_path / "db.db", echo_queries_in_log=False
        ),
        fs=anki_note_tooling.config.FsConfig(
            files_path=tmp_path,
            managed_language_learning_files=tmp_path,
            tmp_files=tmp_path,
        ),
        web=anki_note_tooling.config.WebConfig(debug_server=False),
        notes=NOTE_CONFIG,
    )


def _prepared() -> anki_note_tooling.backfill.PreparedLine:
    """One line with the notes its recipes actually render."""
    to_render = anki_note_tooling.notes.renderer.LineToRender(
        source_name=SOURCE_NAME,
        annotated_text=ANNOTATED,
        text="本 を 読む",
        language="ja",
        source_tag="lesson_1",
        audio_path=Path("/files/audio/line.mp3"),
        image_path=Path("/files/images/line.png"),
    )
    return anki_note_tooling.backfill.PreparedLine(
        line=anki_note_tooling.lib.types.FlLine(
            source_type=SOURCE_NAME,
            source_file="lesson_1.csv",
            language="ja",
            text="本 を 読む",
            annotated_text=ANNOTATED,
            audio=anki_note_tooling.lib.types.AudioFile(
                path=anki_note_tooling.lib.media.MediaRef.parse("audio/l.mp3")
            ),
            image_path=anki_note_tooling.lib.media.MediaRef.parse("images/l.png"),
            misc=anki_note_tooling.lib.types.FlMiscData(
                source_tag="lesson_1", filename="lesson_1.csv"
            ),
        ),
        notes=tuple(anki_note_tooling.notes.renderer.render_all(to_render, config=NOTE_CONFIG)),
    )


@pytest.fixture
def conn(tmp_path):
    config = _config(tmp_path)
    engine = sqlalchemy.create_engine(f"sqlite+pysqlite:///{config.database.sqlite_file}")
    with engine.connect() as connection:
        anki_note_tooling.data.tables.fl_source_type.create(connection)
        anki_note_tooling.data.tables.fl_line.create(connection)
        anki_note_tooling.data.tables.managed_anki_note.create(connection)
        yield connection


@pytest.fixture
def col():
    return mock.create_autospec(anki.collection.Collection, instance=True)


def _anki_note_ids_by_text_by_deck(notes, *, decks=None, first_id=900) -> dict[str, dict[str, int]]:
    """Every note keyed by deck and rendered text, as a collection would answer."""
    by_deck: dict[str, dict[str, int]] = {}
    for index, note in enumerate(notes):
        if decks is not None and note.deck not in decks:
            continue
        by_deck.setdefault(note.deck, {})[note.text] = first_id + index
    return by_deck


def _patched_anki(anki_note_ids_by_text_by_deck: dict[str, dict[str, int]]):
    """
    The two collection reads `match_and_insert_existing_lines_and_notes` makes.

    Standing in for a real Anki collection, which the tests cannot reach.
    """
    return (
        mock.patch.object(
            anki_note_tooling.anki_lib.queries,
            "get_note_ids_by_field",
            autospec=True,
            side_effect=lambda col, *, deck, field: anki_note_ids_by_text_by_deck.get(deck, {}),
        ),
        mock.patch.object(
            anki_note_tooling.anki_lib.utils,
            "get_note_added",
            autospec=True,
            return_value=1_700_000_000,
        ),
    )


def test_every_rendered_note_gets_a_record(conn, col, tmp_path):
    prepared = _prepared()
    assert {note.recipe for note in prepared.notes} == {"kanji_cloze", "jp_phrase"}

    by_field, added = _patched_anki(_anki_note_ids_by_text_by_deck(prepared.notes))
    with by_field, added:
        result = anki_note_tooling.backfill.match_and_insert_existing_lines_and_notes(
            conn, [prepared], col=col, config=_config(tmp_path), source_name=SOURCE_NAME
        )

    records = result.records
    assert result.absent == ()
    assert len(records) == len(prepared.notes)
    assert {record["anki_note_id"] for record in records} == set(range(900, 900 + len(records)))
    assert {record["source_id"] for record in records} == {1}
    assert [record["note"]["ref"] for record in records] == [n.ref for n in prepared.notes]
    assert all(record["misc"]["volume_scale_percentage"] is None for record in records)


def test_records_carry_the_volume_scale_the_notes_were_added_with(conn, col, tmp_path):
    prepared = _prepared()
    by_field, added = _patched_anki(_anki_note_ids_by_text_by_deck(prepared.notes))
    with by_field, added:
        result = anki_note_tooling.backfill.match_and_insert_existing_lines_and_notes(
            conn,
            [prepared],
            col=col,
            config=_config(tmp_path),
            source_name=SOURCE_NAME,
            volume_scale_percentage=200,
        )

    assert all(record["misc"]["volume_scale_percentage"] == 200 for record in result.records)


def test_a_recipe_wholly_absent_from_anki_is_reported_rather_than_fatal(conn, col, tmp_path):
    """
    A recipe with none of its notes in Anki was never run for this line.

    That is a state a previous run could have left — the source's recipes changed, or the
    line was inserted before one of them existed — so the line is still recorded against
    the recipe that is complete, and the gap is reported rather than guessed at.
    """
    prepared = _prepared()
    by_field, added = _patched_anki(
        _anki_note_ids_by_text_by_deck(prepared.notes, decks={CLOZE_RECIPE.deck})
    )
    with by_field, added:
        result = anki_note_tooling.backfill.match_and_insert_existing_lines_and_notes(
            conn, [prepared], col=col, config=_config(tmp_path), source_name=SOURCE_NAME
        )

    assert [record["note"]["recipe"] for record in result.records] == ["kanji_cloze"]
    assert [(a.recipe, a.note_count) for a in result.absent] == [("jp_phrase", 2)]
    assert "no notes in Anki" in str(result.absent[0])


def test_a_recipe_only_partly_in_anki_stops_the_run(conn, col, tmp_path):
    """
    Half a recipe's notes is a state no previous run could have left.

    It means the notes drifted after they were added, and matching on rendered text cannot
    say which Anki note a changed line belongs to — so nothing is recorded rather than
    recording the line against some of its notes and orphaning the rest.
    """
    prepared = _prepared()
    phrase_notes = [note for note in prepared.notes if note.recipe == "jp_phrase"]
    assert len(phrase_notes) == 2
    anki_note_ids_by_text_by_deck = _anki_note_ids_by_text_by_deck(prepared.notes)
    # One phrase note has drifted since it was added, so it is no longer found.
    del anki_note_ids_by_text_by_deck[PHRASE_RECIPE.deck][phrase_notes[0].text]

    by_field, added = _patched_anki(anki_note_ids_by_text_by_deck)
    with by_field, added:
        with pytest.raises(anki_note_tooling.backfill.BackfillError, match="partly in Anki"):
            anki_note_tooling.backfill.match_and_insert_existing_lines_and_notes(
                conn, [prepared], col=col, config=_config(tmp_path), source_name=SOURCE_NAME
            )

    conn.rollback()
    assert conn.execute(sqlalchemy.select(anki_note_tooling.data.tables.fl_line)).all() == []
    assert (
        conn.execute(sqlalchemy.select(anki_note_tooling.data.tables.managed_anki_note)).all() == []
    )


def test_a_line_with_nothing_in_anki_stops_the_run(conn, col, tmp_path):
    """With no complete recipe there is no evidence the line was ever inserted."""
    prepared = _prepared()
    by_field, added = _patched_anki({})
    with by_field, added:
        with pytest.raises(anki_note_tooling.backfill.BackfillError, match="no recipe whose notes"):
            anki_note_tooling.backfill.match_and_insert_existing_lines_and_notes(
                conn, [prepared], col=col, config=_config(tmp_path), source_name=SOURCE_NAME
            )

    conn.rollback()
    assert conn.execute(sqlalchemy.select(anki_note_tooling.data.tables.fl_line)).all() == []


def test_note_text_field_rejects_a_recipe_it_cannot_identify_notes_by():
    recipe = anki_note_tooling.notes.config.Recipe(
        name="phrase",
        deck="d",
        model="m",
        for_each="line",
        active="$kanji",
        fields={"Romanji": "$romanji"},
    )
    with pytest.raises(anki_note_tooling.backfill.BackfillError, match="exactly one field"):
        anki_note_tooling.backfill.note_text_field(recipe)
