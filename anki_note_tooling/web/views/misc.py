import urllib.parse
from typing import cast

import anki.notes
import flask
import sqlalchemy
import werkzeug.datastructures

import anki_note_tooling.anki_shared
import anki_note_tooling.config
import anki_note_tooling.data.inserts
import anki_note_tooling.data.queries
import anki_note_tooling.data.tables
import anki_note_tooling.data.types
import anki_note_tooling.lib.types
import anki_note_tooling.lib.utils
import anki_note_tooling.make_anki_cards
import anki_note_tooling.notes.config
import anki_note_tooling.notes.note
import anki_note_tooling.notes.reconcile


def index():
    return flask.redirect("/search")


def audio_file(file_sub_path: str, *, config: anki_note_tooling.config.Config):
    return flask.send_from_directory(config.fs.files_path, file_sub_path)


def _render_or_report(
    line: anki_note_tooling.data.types.CompleteFlLine, config: anki_note_tooling.config.Config
) -> tuple[list[anki_note_tooling.notes.note.RenderedNote], str | None]:
    """
    The notes a line renders to, or the reason it cannot be rendered.

    A line whose source has no definition, or whose annotation no longer parses, is a
    normal state to surface rather than a crash: someone will always import lines before
    writing the TOML for them.
    """
    try:
        return (
            anki_note_tooling.make_anki_cards.create_notes(
                line, note_config=config.notes, media=config.fs.media
            ),
            None,
        )
    except (anki_note_tooling.notes.config.SourceConfigError, ValueError) as e:
        return [], str(e)


def notes_to_update(*, db: sqlalchemy.Engine):
    with db.connect() as conn:
        inserted_lines = anki_note_tooling.data.queries.get_complete_fl_lines(
            conn, selection="inserted"
        )
        updated_lines = [line for line in inserted_lines if not line.up_to_date_anki_notes]
        return flask.render_template("notes_to_update.html.j2", updated_lines=updated_lines)


def notes_to_insert(*, config: anki_note_tooling.config.Config, db: sqlalchemy.Engine):
    """
    The lines waiting to be inserted, with the fields each would actually write.
    """
    with db.connect() as conn:
        uninserted_lines = anki_note_tooling.data.queries.get_complete_fl_lines(
            conn, selection="uninserted"
        )

    previews = [(line, *_render_or_report(line, config)) for line in uninserted_lines]
    return flask.render_template("notes_to_insert.html.j2", previews=previews)


def update_anki_notes(*, config: anki_note_tooling.config.Config, db: sqlalchemy.Engine):
    """
    Brings every out-of-date line's Anki notes back in line with what its recipes render.

    Matching is a three-way diff on note refs rather than a dict lookup, so a line whose
    annotation gained or lost a substitution updates what it can, inserts what is new, and
    *reports* what it no longer generates instead of silently repointing or deleting an
    existing Anki note.
    """
    with db.connect() as conn:
        inserted_lines = anki_note_tooling.data.queries.get_complete_fl_lines(
            conn, selection="inserted"
        )
        out_of_date = [line for line in inserted_lines if not line.up_to_date_anki_notes]
        stored_by_source = anki_note_tooling.data.queries.get_stored_anki_notes_by_source(
            conn, [line.source_id for line in out_of_date]
        )

    reports: list[str] = []
    for line in out_of_date:
        rendered, problem = _render_or_report(line, config)
        if problem is not None:
            reports.append(f"line {line.source_id}: {problem}")
            continue

        result = anki_note_tooling.notes.reconcile.reconcile(
            rendered, stored_by_source.get(line.source_id, [])
        )
        scale_percentage = anki_note_tooling.make_anki_cards.volume_scale_percentage(
            line, note_config=config.notes
        )
        updates = []
        for stored, note in result.to_update:
            anki_note_tooling.anki_shared.add_note_and_media_to_anki(
                config.anki.profile_dir,
                note,
                anki_note_id=cast(anki.notes.NoteId, stored.anki_note_id),
            )
            updates.append((stored.row_id, note, scale_percentage))

        for note in result.to_insert:
            anki_note_id = anki_note_tooling.anki_shared.add_note_and_media_to_anki(
                config.anki.profile_dir,
                note,
                volume_scale=anki_note_tooling.make_anki_cards.volume_scale(
                    line, note_config=config.notes
                ),
            )
            with db.connect() as conn:
                anki_note_tooling.data.inserts.insert_anki_note(
                    conn,
                    source_id=line.source_id,
                    note=note,
                    anki_note_id=anki_note_id,
                    volume_scale_percentage=scale_percentage,
                )
                conn.commit()

        for stored in result.orphaned:
            reports.append(
                f"line {line.source_id}: note {stored.ref!r} is no longer generated; "
                "it was left in Anki"
            )
        for stored in result.unresolved:
            reports.append(f"line {line.source_id}: note {stored.ref!r} has no usable anki_note_id")

        with db.connect() as conn:
            anki_note_tooling.data.inserts.update_anki_notes(conn, updates=updates)
            anki_note_tooling.data.queries.mark_up_to_date_anki_notes(conn, [line.source_id])
            conn.commit()

    for report in reports:
        print(report)

    return flask.redirect("/notes_to_update")


def insert_anki_notes(*, config: anki_note_tooling.config.Config, db: sqlalchemy.Engine):
    with db.connect() as conn:
        uninserted_lines = anki_note_tooling.data.queries.get_complete_fl_lines(
            conn, selection="uninserted"
        )

    for line in uninserted_lines:
        notes, problem = _render_or_report(line, config)
        if problem is not None:
            print(f"Skipping line {line.source_id}: {problem}")
            continue

        for note in notes:
            anki_note_id = anki_note_tooling.anki_shared.add_note_and_media_to_anki(
                config.anki.profile_dir,
                note,
                volume_scale=anki_note_tooling.make_anki_cards.volume_scale(
                    line, note_config=config.notes
                ),
            )
            with db.connect() as conn:
                anki_note_tooling.data.inserts.insert_anki_note(
                    conn,
                    source_id=line.source_id,
                    note=note,
                    anki_note_id=anki_note_id,
                    volume_scale_percentage=anki_note_tooling.make_anki_cards.volume_scale_percentage(
                        line, note_config=config.notes
                    ),
                )
                anki_note_tooling.data.queries.mark_up_to_date_anki_notes(conn, [line.source_id])
                conn.commit()

    return flask.redirect("/notes_to_insert")


SEARCH_PAGE_SIZE = 100

# Labels for the filter summary shown above the results, in the order they read best.
_FILTER_SUMMARY_LABELS = {
    anki_note_tooling.data.types.MiscSearchField.CHARACTER_NAME: "character",
    anki_note_tooling.data.types.MiscSearchField.FILENAME: "filename",
    anki_note_tooling.data.types.MiscSearchField.SOURCE_TAG: "source tag",
}


def _clean(value: str | None) -> str | None:
    """Strips a form value, treating an all-whitespace one as absent."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def search_filters_from_args(
    args: werkzeug.datastructures.MultiDict,
) -> anki_note_tooling.data.types.FlLineSearchFilters:
    """
    Parses the search form's query string into filters, dropping anything unusable.

    A blank input is dropped rather than becoming an empty substring that matches everything.
    A language nothing has been imported in simply matches no rows: this query string gets
    bookmarked and hand-edited, so a stale value should return nothing rather than break the
    page.

    >>> from werkzeug.datastructures import MultiDict
    >>> search_filters_from_args(MultiDict([("query", "  hello  ")])).text
    'hello'
    >>> search_filters_from_args(MultiDict([("query", "   ")])).text is None
    True
    >>> search_filters_from_args(
    ...     MultiDict([("source", "podcast"), ("source", "drama")])
    ... ).source_types
    ('podcast', 'drama')
    >>> search_filters_from_args(MultiDict([("language", "ja")])).language
    'ja'
    >>> search_filters_from_args(MultiDict([("language", "  ")])).language is None
    True
    >>> search_filters_from_args(MultiDict([("character_name", "Hanako")])).misc_criteria
    ((<MiscSearchField.CHARACTER_NAME: 'character_name'>, 'Hanako'),)
    >>> search_filters_from_args(MultiDict([("exclude_inserted", "on")])).exclude_inserted
    True
    >>> search_filters_from_args(MultiDict()).is_empty
    True
    """
    return anki_note_tooling.data.types.FlLineSearchFilters(
        text=_clean(args.get("query")),
        source_types=tuple(source for source in args.getlist("source") if source),
        language=_clean(args.get("language")),
        character_name=_clean(args.get("character_name")),
        filename=_clean(args.get("filename")),
        source_tag=_clean(args.get("source_tag")),
        exclude_inserted="exclude_inserted" in args,
    )


def search_filter_summary(filters: anki_note_tooling.data.types.FlLineSearchFilters) -> list[str]:
    """
    The active filters as short "label: value" strings, for display above the results.

    >>> search_filter_summary(anki_note_tooling.data.types.FlLineSearchFilters())
    []
    >>> search_filter_summary(
    ...     anki_note_tooling.data.types.FlLineSearchFilters(
    ...         source_types=("podcast", "drama"), filename="episode_03"
    ...     )
    ... )
    ['sources: podcast, drama', 'filename: episode_03']
    """
    summary = []

    if filters.source_types:
        summary.append(f"sources: {', '.join(filters.source_types)}")
    if filters.language is not None:
        summary.append(f"language: {filters.language}")
    summary.extend(
        f"{_FILTER_SUMMARY_LABELS[field]}: {value}" for field, value in filters.misc_criteria
    )
    if filters.exclude_inserted:
        summary.append("excluding lines already in Anki")

    return summary


def _page_from_args(args: werkzeug.datastructures.MultiDict) -> int:
    """
    The requested page number, clamped to 1 for anything unparseable.

    >>> from werkzeug.datastructures import MultiDict
    >>> _page_from_args(MultiDict([("page", "3")]))
    3
    >>> _page_from_args(MultiDict([("page", "-2")]))
    1
    >>> _page_from_args(MultiDict([("page", "banana")]))
    1
    >>> _page_from_args(MultiDict())
    1
    """
    try:
        return max(1, int(args.get("page", "1")))
    except ValueError:
        return 1


def _search_page_url(page: int) -> str:
    """
    The current search URL with `page` replaced.

    Rebuilding from the whole query string rather than naming each filter means a filter
    added later is carried across pages without touching this. The query string is encoded
    directly rather than through `url_for`, which reserves its own keyword arguments.
    """
    args = flask.request.args.to_dict(flat=False)
    args["page"] = [str(page)]
    return f"{flask.request.path}?{urllib.parse.urlencode(args, doseq=True)}"


def search(*, config: anki_note_tooling.config.Config, db: sqlalchemy.Engine):
    filters = search_filters_from_args(flask.request.args)
    page = _page_from_args(flask.request.args)

    results = []
    has_next = False

    with db.connect() as conn:
        source_type_counts = anki_note_tooling.data.queries.get_source_type_line_counts(conn)
        language_counts = anki_note_tooling.data.queries.get_language_counts(conn)

        if not filters.is_empty:
            results_rows, has_next = anki_note_tooling.data.queries.search_fl_lines(
                conn, filters, page=page, page_size=SEARCH_PAGE_SIZE
            )
            inserted = set(
                source_id
                for source_id, _ in anki_note_tooling.data.queries.get_insertions_with_dates(conn)
            )

            for line in results_rows:
                audio_file_sub_path = str(line.audio.path) if line.audio is not None else None
                results.append(
                    [
                        line.source_id,
                        line.source_type,
                        line.source_file,
                        line.text,
                        audio_file_sub_path,
                        line.source_id in inserted,
                    ]
                )

    return flask.render_template(
        "search.html.j2",
        query=filters.text,
        filters=filters,
        filter_summary=search_filter_summary(filters),
        searched=not filters.is_empty,
        results=results,
        source_type_counts=source_type_counts,
        languages=language_counts,
        misc_fields=list(anki_note_tooling.data.types.MiscSearchField),
        prev_url=_search_page_url(page - 1) if page > 1 else None,
        next_url=_search_page_url(page + 1) if has_next else None,
        page=page,
    )


def search_suggest(*, db: sqlalchemy.Engine):
    """
    A `<datalist>` of values for one metadata field, for the search form's autocomplete.

    The field is parsed into `MiscSearchField` before it reaches the query, so an unknown
    field name is a 404 rather than an arbitrary JSON path.
    """
    try:
        field = anki_note_tooling.data.types.MiscSearchField(flask.request.args.get("field", ""))
    except ValueError:
        flask.abort(404)

    with db.connect() as conn:
        suggestions = anki_note_tooling.data.queries.get_misc_value_suggestions(
            conn,
            field=field,
            # The input sends its own value under its own name, which is the field name.
            prefix=_clean(flask.request.args.get(field.value)),
            source_types=[source for source in flask.request.args.getlist("source") if source],
        )

    return flask.render_template("search_suggestions.html.j2", field=field, suggestions=suggestions)


def inserted(*, db: sqlalchemy.Engine):
    with db.connect() as conn:
        results = list(anki_note_tooling.data.queries.get_insertions_with_dates(conn))
    return flask.render_template("inserted.html.j2", results=results)
