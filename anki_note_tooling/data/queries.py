import datetime
import itertools
from typing import Any, Iterable, Literal, assert_never

import jsons
import sqlalchemy

import anki_note_tooling.data.tables
import anki_note_tooling.data.types
import anki_note_tooling.lib.media
import anki_note_tooling.lib.types
import anki_note_tooling.notes.note

SQLITE_QUERY_PARAMETER_LIMIT = 999


class RecordNotFoundError(Exception):
    """No row exists for the id asked for. The web layer turns this into a 404."""


def get_or_create_source_type(conn: sqlalchemy.Connection, source_type: str) -> int:
    """
    Returns the `fl_source_type` id for `source_type`, inserting the row if it is new.

    Source names come from user-written config rather than a closed enum, so a name anki_note_tooling
    has never seen is a normal state: the table is a registry handing out stable ids for
    `fl_line.source_type_id` and `image_pool_entry.source_type_id` to reference, not a
    list of the sources anki_note_tooling knows how to handle.
    """
    select_id = (
        sqlalchemy.select(anki_note_tooling.data.tables.fl_source_type.c["id"])
        .select_from(anki_note_tooling.data.tables.fl_source_type)
        .where(anki_note_tooling.data.tables.fl_source_type.c["name"] == source_type)
    )
    result = conn.execute(select_id).one_or_none()
    if result is not None:
        return result[0]

    inserted = conn.execute(
        sqlalchemy.insert(anki_note_tooling.data.tables.fl_source_type)
        .values(name=source_type)
        .returning(anki_note_tooling.data.tables.fl_source_type.c["id"])
    ).one()
    return inserted[0]


def get_is_starred(conn: sqlalchemy.Connection, source_id: int) -> bool:
    result = conn.execute(
        sqlalchemy.select(anki_note_tooling.data.tables.fl_starred.c["id"])
        .select_from(anki_note_tooling.data.tables.fl_starred)
        .where(anki_note_tooling.data.tables.fl_starred.c["source_id"] == source_id)
    ).one_or_none()
    if result is None:
        return False
    else:
        return True


def toggle_starred(conn: sqlalchemy.Connection, source_id: int) -> bool:
    is_starred = get_is_starred(conn, source_id)

    if is_starred:
        conn.execute(
            sqlalchemy.delete(anki_note_tooling.data.tables.fl_starred).where(
                anki_note_tooling.data.tables.fl_starred.c["source_id"] == source_id
            )
        )
        return False
    else:
        conn.execute(
            sqlalchemy.insert(anki_note_tooling.data.tables.fl_starred).values(source_id=source_id)
        )
        return True


def mark_up_to_date_anki_notes(conn: sqlalchemy.Connection, source_line_ids: Iterable[int]):
    for batch in itertools.batched(source_line_ids, SQLITE_QUERY_PARAMETER_LIMIT):
        conn.execute(
            sqlalchemy.update(anki_note_tooling.data.tables.fl_line)
            .where(anki_note_tooling.data.tables.fl_line.c["id"].in_(batch))
            .values(up_to_date_anki_notes=True)
        )


def mark_not_up_to_date_anki_notes(conn: sqlalchemy.Connection, source_line_ids: Iterable[int]):
    for batch in itertools.batched(source_line_ids, SQLITE_QUERY_PARAMETER_LIMIT):
        conn.execute(
            sqlalchemy.update(anki_note_tooling.data.tables.fl_line)
            .where(anki_note_tooling.data.tables.fl_line.c["id"].in_(batch))
            .values(up_to_date_anki_notes=False)
        )


def get_anki_note_texts(conn: sqlalchemy.Connection) -> set[str]:
    """
    The rendered line of every note anki_note_tooling has recorded.

    `note.text` is what identifies a note to a human, and the only thing a note and the
    line it came from share once the fields are written, so it is what a caller asking
    "is this already in Anki?" compares against.
    """
    return {
        text
        for (text,) in conn.execute(
            sqlalchemy.select(
                anki_note_tooling.data.tables.managed_anki_note.c["note"]["text"].as_string()
            ).distinct()
        )
        if text
    }


def _stored_note(row: sqlalchemy.Row) -> anki_note_tooling.notes.note.StoredNote:
    """
    Builds a `StoredNote` from a `managed_anki_note` row.

    The ref lives in the note record itself; `misc.internal_note_ref` is where rows
    written before the record shape changed keep theirs, hence the fallback.
    """
    record = row.note if isinstance(row.note, dict) else {}
    misc = row.misc if isinstance(row.misc, dict) else {}
    return anki_note_tooling.notes.note.StoredNote(
        row_id=row.id,
        anki_note_id=row.anki_note_id,
        ref=record.get("ref") or misc.get("internal_note_ref"),
        record=record,
    )


def get_stored_anki_notes(
    conn: sqlalchemy.Connection, source_id: int
) -> list[anki_note_tooling.notes.note.StoredNote]:
    """Every note anki_note_tooling has recorded for one line, whatever shape it was stored in."""
    return [
        _stored_note(row)
        for row in conn.execute(
            sqlalchemy.select(anki_note_tooling.data.tables.managed_anki_note)
            .where(anki_note_tooling.data.tables.managed_anki_note.c["source_id"] == source_id)
            .order_by(anki_note_tooling.data.tables.managed_anki_note.c["id"])
        )
    ]


def get_stored_anki_notes_by_source(
    conn: sqlalchemy.Connection, source_ids: Iterable[int]
) -> dict[int, list[anki_note_tooling.notes.note.StoredNote]]:
    """The same, for many lines at once — one query rather than one per line."""
    by_source: dict[int, list[anki_note_tooling.notes.note.StoredNote]] = {}
    for batch in itertools.batched(source_ids, SQLITE_QUERY_PARAMETER_LIMIT):
        rows = conn.execute(
            sqlalchemy.select(anki_note_tooling.data.tables.managed_anki_note)
            .where(anki_note_tooling.data.tables.managed_anki_note.c["source_id"].in_(batch))
            .order_by(anki_note_tooling.data.tables.managed_anki_note.c["id"])
        )
        for row in rows:
            by_source.setdefault(row.source_id, []).append(_stored_note(row))
    return by_source


def _select_fl_lines() -> sqlalchemy.Select:
    """
    Base select for building `StoredFlLine` and `CompleteFlLine`.

    fl_line carries everything about a line; joining fl_source_type is the only extra work,
    and supplies the source type's name and its audio volume scaling.
    """
    return sqlalchemy.select(
        anki_note_tooling.data.tables.fl_line.c["id"],
        anki_note_tooling.data.tables.fl_source_type.c["name"],
        anki_note_tooling.data.tables.fl_line.c["source_file"],
        anki_note_tooling.data.tables.fl_line.c["text"],
        anki_note_tooling.data.tables.fl_line.c["annotated_text"],
        anki_note_tooling.data.tables.fl_line.c["audio"],
        anki_note_tooling.data.tables.fl_line.c["image"],
        anki_note_tooling.data.tables.fl_line.c["misc"],
        anki_note_tooling.data.tables.fl_line.c["language"],
        anki_note_tooling.data.tables.fl_line.c["up_to_date_anki_notes"],
    ).select_from(
        anki_note_tooling.data.tables.fl_line.join(
            anki_note_tooling.data.tables.fl_source_type,
            anki_note_tooling.data.tables.fl_source_type.c.id
            == anki_note_tooling.data.tables.fl_line.c.source_type_id,
        )
    )


def _fl_line(record: sqlalchemy.Row) -> anki_note_tooling.data.types.StoredFlLine:
    """
    One `_select_fl_lines` row as a `StoredFlLine`.

    Column names and field names differ on purpose — `fl_line.text` is the model's `text`,
    `fl_source_type.name` is its `source_type` — so the mapping lives here rather than being
    written out again at each caller.
    """
    misc: anki_note_tooling.lib.types.FlMiscData = jsons.load(
        record.misc, cls=anki_note_tooling.lib.types.FlMiscData
    )
    audio = (
        anki_note_tooling.lib.types.AudioFile(
            path=anki_note_tooling.lib.media.MediaRef.parse(record.audio["path"])
        )
        if record.audio is not None
        else None
    )
    return anki_note_tooling.data.types.StoredFlLine(
        source_id=record.id,
        source_type=record.name,
        # TODO: this shouldn't really be stored in two places
        source_file=record.source_file or misc.filename,
        language=record.language,
        text=record.text,
        annotated_text=record.annotated_text,
        audio=audio,
        image_path=(
            anki_note_tooling.lib.media.MediaRef.parse(record.image)
            if record.image is not None
            else None
        ),
        misc=misc,
        up_to_date_anki_notes=record.up_to_date_anki_notes,
    )


def get_complete_fl_lines(
    conn: sqlalchemy.Connection,
    *,
    selection: Literal["all", "uninserted", "inserted"] = "all",
    max_records: int | None = None,
) -> list[anki_note_tooling.data.types.CompleteFlLine]:
    # Completeness is an explicit check that each of the three columns is populated. This
    # relies on fl_line.audio using JSON(none_as_null=True): the default would store a missing
    # audio as the JSON text 'null', which is not SQL NULL, and every line would look complete.
    query = _select_fl_lines().where(
        anki_note_tooling.data.tables.fl_line.c["annotated_text"].is_not(None),
        anki_note_tooling.data.tables.fl_line.c["image"].is_not(None),
        anki_note_tooling.data.tables.fl_line.c["audio"].is_not(None),
    )

    match selection:
        case "uninserted":
            query = query.where(
                anki_note_tooling.data.tables.fl_line.c["id"].not_in(
                    sqlalchemy.select(anki_note_tooling.data.tables.managed_anki_note.c.source_id)
                )
            )
        case "inserted":
            query = query.where(
                anki_note_tooling.data.tables.fl_line.c["id"].in_(
                    sqlalchemy.select(anki_note_tooling.data.tables.managed_anki_note.c.source_id)
                )
            )
        case "all":
            pass
        case _ as unreachable:
            assert_never(unreachable)

    if max_records is not None:
        query = query.limit(max_records)

    return [
        complete
        for row in conn.execute(query)
        # The WHERE clauses above already guarantee this; `completed()` is what turns that
        # guarantee into a type the caller can rely on.
        if (complete := _fl_line(row).completed()) is not None
    ]


def get_single_complete_line(
    conn: sqlalchemy.Connection, fl_line_id: int
) -> anki_note_tooling.data.types.CompleteFlLine | None:
    """
    One complete line by id, or None if it is missing or not complete yet.

    Unlike `get_single_fl_line`, a missing row is not an error: callers use this to
    ask whether a line is ready to render, and "no such line" and "not complete yet" are
    the same answer to that question.
    """
    record = conn.execute(
        _select_fl_lines().where(
            anki_note_tooling.data.tables.fl_line.c["id"] == fl_line_id,
            anki_note_tooling.data.tables.fl_line.c["annotated_text"].is_not(None),
            anki_note_tooling.data.tables.fl_line.c["image"].is_not(None),
            anki_note_tooling.data.tables.fl_line.c["audio"].is_not(None),
        )
    ).one_or_none()

    if record is None:
        return None

    return _fl_line(record).completed()


def get_single_fl_line(
    conn: sqlalchemy.Connection, fl_line_id: int
) -> anki_note_tooling.data.types.StoredFlLine:
    """One line by id, whatever state of completeness it is in."""
    record = conn.execute(
        _select_fl_lines().where(anki_note_tooling.data.tables.fl_line.c["id"] == fl_line_id)
    ).one_or_none()

    if record is None:
        raise RecordNotFoundError(f"no fl_line with id {fl_line_id}")

    return _fl_line(record)


def _misc_value(
    field: anki_note_tooling.data.types.MiscSearchField,
) -> sqlalchemy.ColumnElement[Any]:
    """
    The `fl_line.misc` JSON value for `field`, as a string SQL can compare.

    SQLAlchemy renders this as `JSON_EXTRACT(fl_line.misc, '$."<field>"')` on SQLite. The
    path is built from an enum member rather than a bare string so a request parameter can
    never name an arbitrary key.

    `Any` matches what `as_string()` resolves to, since SQLAlchemy leaves it unannotated.
    """
    return anki_note_tooling.data.tables.fl_line.c["misc"][field.value].as_string()


def _search_clauses(
    filters: anki_note_tooling.data.types.FlLineSearchFilters,
) -> list[sqlalchemy.ColumnElement[bool]]:
    """
    The WHERE clauses for `filters`, omitting any criterion that is unset.

    Every criterion is expressed in SQL rather than filtered out afterwards, which is what
    lets LIMIT/OFFSET paging stay honest about how many rows a page holds.
    """
    clauses: list[sqlalchemy.ColumnElement[bool]] = []

    if filters.text:
        clauses.append(anki_note_tooling.data.tables.fl_line.c["text"].ilike(f"%{filters.text}%"))

    if filters.source_types:
        clauses.append(
            anki_note_tooling.data.tables.fl_source_type.c["name"].in_(filters.source_types)
        )

    if filters.language is not None:
        clauses.append(anki_note_tooling.data.tables.fl_line.c["language"] == filters.language)

    clauses.extend(_misc_value(field).ilike(f"%{value}%") for field, value in filters.misc_criteria)

    if filters.exclude_inserted:
        clauses.append(
            anki_note_tooling.data.tables.fl_line.c["id"].not_in(
                sqlalchemy.select(anki_note_tooling.data.tables.managed_anki_note.c["source_id"])
            )
        )

    return clauses


def search_fl_lines(
    conn: sqlalchemy.Connection,
    filters: anki_note_tooling.data.types.FlLineSearchFilters,
    *,
    page: int | None = None,
    page_size: int | None = None,
) -> tuple[list[anki_note_tooling.data.types.StoredFlLine], bool]:
    select_lines = (
        _select_fl_lines()
        .where(*_search_clauses(filters))
        .order_by(anki_note_tooling.data.tables.fl_line.c["id"])
    )

    if page_size is not None:
        select_lines = select_lines.limit(limit=page_size + 1)

        if page is not None:
            select_lines = select_lines.offset((page - 1) * page_size)

    results_list = list(conn.execute(select_lines))

    has_next = False
    if page_size is not None and len(results_list) > page_size:
        has_next = True
        results_list = results_list[:-1]

    return [_fl_line(row) for row in results_list], has_next


def get_source_type_names(conn: sqlalchemy.Connection) -> list[str]:
    """Every name in the `fl_source_type` registry, whether or not it has any lines."""
    return [
        row.name
        for row in conn.execute(
            sqlalchemy.select(anki_note_tooling.data.tables.fl_source_type.c["name"]).order_by(
                anki_note_tooling.data.tables.fl_source_type.c["name"]
            )
        )
    ]


def get_source_type_line_counts(conn: sqlalchemy.Connection) -> list[tuple[str, int]]:
    """
    Every registered source name paired with how many `fl_line` rows it has.

    The outer join keeps sources that are registered but not yet imported, so the search page
    can offer them as filters showing a count of zero rather than hiding them until the first
    import makes them appear.
    """
    query = (
        sqlalchemy.select(
            anki_note_tooling.data.tables.fl_source_type.c["name"],
            sqlalchemy.func.count(anki_note_tooling.data.tables.fl_line.c["id"]),
        )
        .select_from(
            anki_note_tooling.data.tables.fl_source_type.outerjoin(
                anki_note_tooling.data.tables.fl_line,
                anki_note_tooling.data.tables.fl_source_type.c["id"]
                == anki_note_tooling.data.tables.fl_line.c["source_type_id"],
            )
        )
        .group_by(anki_note_tooling.data.tables.fl_source_type.c["name"])
        .order_by(anki_note_tooling.data.tables.fl_source_type.c["name"])
    )
    return [(row[0], row[1]) for row in conn.execute(query)]


def get_language_counts(conn: sqlalchemy.Connection) -> list[tuple[str, int]]:
    """
    Every language present in `fl_line`, paired with how many rows carry it.

    The search page's language filter is built from this rather than from a fixed list, so it
    offers the languages the data actually holds. Which languages exist is a property of what
    has been imported, not something this package decides.
    """
    query = (
        sqlalchemy.select(
            anki_note_tooling.data.tables.fl_line.c["language"],
            sqlalchemy.func.count(anki_note_tooling.data.tables.fl_line.c["id"]),
        )
        .where(anki_note_tooling.data.tables.fl_line.c["language"].is_not(None))
        .group_by(anki_note_tooling.data.tables.fl_line.c["language"])
        .order_by(anki_note_tooling.data.tables.fl_line.c["language"])
    )
    return [(row[0], row[1]) for row in conn.execute(query)]


def get_misc_value_suggestions(
    conn: sqlalchemy.Connection,
    *,
    field: anki_note_tooling.data.types.MiscSearchField,
    prefix: str | None = None,
    source_types: Iterable[str] = (),
    limit: int = 20,
) -> list[str]:
    """
    Distinct `misc` values for `field`, for autocompleting a search input.

    Matching is the same substring match the search itself uses, so a suggestion offered here
    always finds the rows it came from. Narrowing by `source_types` keeps the list to sources
    the user is already filtering on, which is what makes it short enough to be useful.
    """
    value = _misc_value(field)
    query = (
        sqlalchemy.select(value)
        .select_from(
            anki_note_tooling.data.tables.fl_line.join(
                anki_note_tooling.data.tables.fl_source_type,
                anki_note_tooling.data.tables.fl_source_type.c["id"]
                == anki_note_tooling.data.tables.fl_line.c["source_type_id"],
            )
        )
        .where(value.is_not(None))
        .distinct()
        .order_by(value)
        .limit(limit)
    )

    if prefix:
        query = query.where(value.ilike(f"%{prefix}%"))

    source_types = tuple(source_types)
    if source_types:
        query = query.where(
            anki_note_tooling.data.tables.fl_source_type.c["name"].in_(source_types)
        )

    return [row[0] for row in conn.execute(query)]


def get_image_pool_entries(
    conn: sqlalchemy.Connection, *, source_type_id: int, key: str | None = None
) -> list[anki_note_tooling.data.types.ImagePoolEntry]:
    """
    The image pool for a source, optionally narrowed to one `key`.

    Passing no `key` returns the whole pool, which is what a source with no
    `[source.images].match` gets. Narrowing to a key that matches nothing correctly
    returns nothing: a line whose character has no images should show an empty picker,
    not the entire pool.
    """
    query = (
        sqlalchemy.select(anki_note_tooling.data.tables.image_pool_entry)
        .where(anki_note_tooling.data.tables.image_pool_entry.c["source_type_id"] == source_type_id)
        .order_by(anki_note_tooling.data.tables.image_pool_entry.c["id"])
    )
    if key is not None:
        query = query.where(anki_note_tooling.data.tables.image_pool_entry.c["key"] == key)

    return [
        anki_note_tooling.data.types.ImagePoolEntry(
            image=anki_note_tooling.lib.media.MediaRef.parse(row.image),
            key=row.key,
            label=row.label,
            metadata=row.metadata if isinstance(row.metadata, dict) else {},
        )
        for row in conn.execute(query)
    ]


def get_insertions_with_dates(
    conn: sqlalchemy.Connection,
) -> list[tuple[int, datetime.datetime]]:
    insertion_lookup = (
        sqlalchemy.select(
            anki_note_tooling.data.tables.managed_anki_note.c["source_id"],
            sqlalchemy.func.min(anki_note_tooling.data.tables.managed_anki_note.c.created_at),
        )
        .select_from(anki_note_tooling.data.tables.managed_anki_note)
        .group_by(anki_note_tooling.data.tables.managed_anki_note.c["source_id"])
    )
    return [(row[0], row[1]) for row in conn.execute(insertion_lookup)]
