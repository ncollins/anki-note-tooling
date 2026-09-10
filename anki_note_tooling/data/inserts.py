from typing import Any, Iterable, cast

import jsons
import sqlalchemy
import sqlalchemy.dialects.sqlite

import anki_note_tooling.config
import anki_note_tooling.data.tables
import anki_note_tooling.data.types
import anki_note_tooling.lib.media
import anki_note_tooling.lib.types
import anki_note_tooling.notes.note


# The three helpers below return fragments of an fl_line row rather than whole records, so
# that they serve both as pieces of an insert and as `.values()` for a partial update. The
# web layer builds an update out of only the fields a form actually changed, which is why
# they stay separate rather than being folded into _source_line_record.
def _audio_record(audio: anki_note_tooling.lib.types.AudioFile) -> dict[str, Any]:
    """
    The `fl_line.audio` column value for `audio`.

    A plain `{"path": ...}` object: the column holds one location, so writing it out directly
    means the value is parsed back at the boundary in `anki_note_tooling.data.queries` rather than
    reconstructed from serialized type metadata.
    """
    return {
        "audio": {"path": str(audio.path)},
    }


def _image_record(image: anki_note_tooling.lib.media.MediaRef) -> dict[str, Any]:
    return {
        "image": str(image),
    }


def _annotation_record(annotated_text: str) -> dict[str, Any]:
    return {
        "annotated_text": annotated_text,
    }


def _source_line_record(
    source_line: anki_note_tooling.lib.types.FlLine,
    *,
    source_type_id: int,
) -> dict[str, Any]:
    """
    A complete fl_line row. Every column is present even when empty: executemany binds a
    single statement for all the records, so a key missing from some of them would be dropped.
    """
    record: dict[str, Any] = {
        "source_type_id": source_type_id,
        "source_file": source_line.source_file,
        "language": source_line.language,
        "text": source_line.text,
        "misc": jsons.dump(source_line.misc),
        "annotated_text": source_line.annotated_text,
        "image": None,
        "audio": None,
    }

    if source_line.image_path is not None:
        record |= _image_record(source_line.image_path)
    if source_line.audio is not None:
        record |= _audio_record(source_line.audio)

    return record


def insert_lines(
    conn: sqlalchemy.Connection,
    source_lines: list[anki_note_tooling.lib.types.FlLine],
    *,
    source_type_id: int,
) -> list[anki_note_tooling.data.types.StoredFlLine]:
    """
    Creates the `fl_line` rows for `source_lines` and returns them as stored, in the order
    they were passed.

    Returning the lines rather than bare ids means a caller that goes on to render notes is
    working from what the rows now hold, without having to rebuild a line field by field.
    """
    if len(source_lines) == 0:
        return []

    records = [_source_line_record(line, source_type_id=source_type_id) for line in source_lines]

    # sort_by_parameter_order is what pairs each returned id with the line it came from:
    # RETURNING on an executemany is otherwise free to come back in any order.
    source_insert = anki_note_tooling.data.tables.fl_line.insert().returning(
        anki_note_tooling.data.tables.fl_line.c.id, sort_by_parameter_order=True
    )
    results = conn.execute(source_insert, records)
    source_line_ids = [cast(int, row[0]) for row in results]
    assert len(source_lines) == len(source_line_ids)

    return [
        anki_note_tooling.data.types.StoredFlLine(
            source_id=source_id, up_to_date_anki_notes=False, **dict(line)
        )
        for line, source_id in zip(source_lines, source_line_ids, strict=True)
    ]


def insert_image_pool_entries(
    conn: sqlalchemy.Connection,
    *,
    source_type_id: int,
    entries: Iterable[anki_note_tooling.data.types.ImagePoolEntry],
) -> None:
    """
    Adds entries to a source's image pool, skipping ones already present.

    Idempotent by way of the two unique indexes on the table — one over
    (source_type_id, key, image) and a partial one covering keyless entries, which the
    first cannot because SQLite treats NULLs as distinct. Re-running an importer over the
    same directory is therefore a no-op rather than a duplicate of every row.
    """
    records = [
        {
            "source_type_id": source_type_id,
            "key": entry.key,
            "image": str(entry.image),
            "label": entry.label,
            "metadata": entry.metadata,
        }
        for entry in entries
    ]
    if not records:
        return
    conn.execute(
        # No conflict target: the insert has to yield to either unique index, and naming
        # one of them makes SQLite raise on the other.
        sqlalchemy.dialects.sqlite.insert(
            anki_note_tooling.data.tables.image_pool_entry
        ).on_conflict_do_nothing(),
        records,
    )


def insert_anki_note(
    conn: sqlalchemy.Connection,
    *,
    source_id: int,
    note: anki_note_tooling.notes.note.RenderedNote,
    anki_note_id: int,
    volume_scale_percentage: int | None = None,
) -> None:
    insert = anki_note_tooling.data.tables.managed_anki_note.insert().values(
        source_id=source_id,
        deck_name=note.deck,
        note=note.to_record(),
        misc={
            "internal_note_ref": note.ref,
            "volume_scale_percentage": volume_scale_percentage,
        },
        inserted_at=sqlalchemy.func.now(),
        anki_note_id=anki_note_id,
    )
    conn.execute(insert)


def update_anki_notes(
    conn: sqlalchemy.Connection,
    *,
    updates: Iterable[tuple[int, anki_note_tooling.notes.note.RenderedNote, int | None]],
) -> None:
    """Rewrites the stored record of notes already in Anki, keyed by `managed_anki_note.id`."""
    records = [
        {
            "row_id": row_id,
            "deck_name": note.deck,
            "note_record": note.to_record(),
            "misc_record": {
                "internal_note_ref": note.ref,
                "volume_scale_percentage": volume_scale_percentage,
            },
        }
        for row_id, note, volume_scale_percentage in updates
    ]
    if not records:
        return
    conn.execute(
        anki_note_tooling.data.tables.managed_anki_note.update()
        .where(
            anki_note_tooling.data.tables.managed_anki_note.c["id"]
            == sqlalchemy.bindparam("row_id")
        )
        .values(
            deck_name=sqlalchemy.bindparam("deck_name"),
            note=sqlalchemy.bindparam("note_record"),
            misc=sqlalchemy.bindparam("misc_record"),
        ),
        records,
    )


def insert_anki_notes(
    conn: sqlalchemy.Connection,
    *,
    records: list[dict[str, Any]],
) -> None:
    if len(records) == 0:
        return
    managed_anki_note_insert = anki_note_tooling.data.tables.managed_anki_note.insert()
    conn.execute(managed_anki_note_insert, records)


def update_fl_line(
    conn: sqlalchemy.Connection,
    source_id: int,
    *,
    changes: dict[str, Any],
) -> None:
    if not changes:
        return
    conn.execute(
        sqlalchemy.update(anki_note_tooling.data.tables.fl_line)
        .where(anki_note_tooling.data.tables.fl_line.c["id"] == source_id)
        .values(**changes)
    )
