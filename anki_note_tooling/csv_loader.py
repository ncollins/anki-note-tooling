"""
Loading flashcard lines from a CSV, for a source with no importer of its own.

The point of the module is that most sources do not need one. What an importer earns its
keep doing is untangling a format nobody chose — a game's bdat dump, an apkg's media
mapping — and a CSV whose columns are already the fields of a line has nothing left to
untangle. So the headers *are* the field names, and adding a source becomes a `sources/`
TOML plus a spreadsheet.

Nothing here touches Anki. A loaded line is raw material: `anki_note_tooling sources render` and
`anki_note_tooling make-anki-cards` are what turn it into notes, exactly as they would a line any
importer produced.
"""

import csv
import dataclasses
from pathlib import Path
from typing import Iterable, Mapping

import typer

import anki_note_tooling.backfill
import anki_note_tooling.config
import anki_note_tooling.data.inserts
import anki_note_tooling.data.queries
import anki_note_tooling.data.tables
import anki_note_tooling.lib.media
import anki_note_tooling.lib.types
import anki_note_tooling.notes.annotation
import anki_note_tooling.notes.config

app = typer.Typer()

#: Columns that become `fl_line` fields directly.
TEXT_COLUMN = "text"
ANNOTATED_TEXT_COLUMN = "annotated_text"
LANGUAGE_COLUMN = "language"
SOURCE_FILE_COLUMN = "source_file"

#: Columns naming a file in `--media-dir`.
AUDIO_COLUMN = "audio"
IMAGE_COLUMN = "image"

#: Columns that become `FlMiscData` fields. Taken from the dataclass rather than listed, so
#: a field added there is loadable without an edit here.
MISC_COLUMNS = tuple(
    field.name for field in dataclasses.fields(anki_note_tooling.lib.types.FlMiscData)
)

KNOWN_COLUMNS = frozenset(
    {
        TEXT_COLUMN,
        ANNOTATED_TEXT_COLUMN,
        LANGUAGE_COLUMN,
        SOURCE_FILE_COLUMN,
        AUDIO_COLUMN,
        IMAGE_COLUMN,
        *MISC_COLUMNS,
    }
)


class CsvLoadError(Exception):
    """The file cannot be read as lines, so nothing should be written."""


def check_headers(headers: Iterable[str]) -> None:
    """
    Raises unless every column is one a line has a place for.

    An unrecognised header is refused rather than ignored, because the failure it usually
    means — a misspelled column — is otherwise silent: the load succeeds and the data is
    simply not there.

    >>> check_headers(["text", "english_line", "audio"])
    >>> check_headers(["text", "englsh_line", "audioo"])
    Traceback (most recent call last):
        ...
    anki_note_tooling.csv_loader.CsvLoadError: unknown column(s): audioo, englsh_line. Known columns are: ...
    >>> check_headers(["english_line"])
    Traceback (most recent call last):
        ...
    anki_note_tooling.csv_loader.CsvLoadError: no 'text' column, so there is no line to load
    """
    unknown = sorted(set(headers) - KNOWN_COLUMNS)
    if unknown:
        raise CsvLoadError(
            f"unknown column(s): {', '.join(unknown)}. "
            f"Known columns are: {', '.join(sorted(KNOWN_COLUMNS))}"
        )
    if TEXT_COLUMN not in set(headers):
        raise CsvLoadError(f"no {TEXT_COLUMN!r} column, so there is no line to load")


def _cell(row: Mapping[str, str | None], column: str) -> str | None:
    """
    A column's value, with blank read as absent.

    `csv.DictReader` gives a short row `None` and an empty cell `""`; a line has one way of
    lacking a field, so both arrive here as None.

    >>> _cell({"english_line": " to go "}, "english_line")
    'to go'
    >>> _cell({"english_line": "  "}, "english_line") is None
    True
    >>> _cell({}, "english_line") is None
    True
    """
    value = row.get(column)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def misc_data(
    row: Mapping[str, str | None], *, default_source_tag: str
) -> anki_note_tooling.lib.types.FlMiscData:
    """
    The metadata columns of one row.

    `source_tag` falls back to the file's own name because recipes routinely tag notes with
    `$source_tag`; leaving it empty would tag every note with the empty string rather than
    with nothing.

    >>> misc_data({"english_line": "to go"}, default_source_tag="verbs.csv")
    FlMiscData(character_name=None, raw_text=None, english_line='to go', source_tag='verbs.csv', filename=None, line_id=None)
    >>> misc_data({"source_tag": "chapter1"}, default_source_tag="verbs.csv").source_tag
    'chapter1'
    """
    values = {column: _cell(row, column) for column in MISC_COLUMNS}
    if values["source_tag"] is None:
        values["source_tag"] = default_source_tag
    return anki_note_tooling.lib.types.FlMiscData(**values)  # type: ignore[arg-type]


@dataclasses.dataclass(kw_only=True, frozen=True)
class ParsedRow:
    """
    One row, with the files it names still to be found.

    The media stays as a bare file name until every row has been read: a load that is going
    to fail for a missing file should say so about all of them at once, rather than one run
    per typo.
    """

    line: anki_note_tooling.lib.types.FlLine
    audio_filename: str | None
    image_filename: str | None


def parse_row(
    row: Mapping[str, str | None],
    *,
    source: anki_note_tooling.notes.config.Source,
    source_file: str,
    row_number: int,
) -> ParsedRow:
    """
    One CSV row as a line, or `CsvLoadError` naming the row that cannot be one.

    The annotation is parsed here rather than left for render time, because the slots it
    must fill are the source's and this is the last moment they are both to hand. It is the
    same check `anki_note_tooling.web.views.fl_line` makes when someone edits an annotation, so a line
    loaded from a file and a line typed into the browser are held to one standard.
    """
    text = _cell(row, TEXT_COLUMN)
    if text is None:
        raise CsvLoadError(f"row {row_number}: {TEXT_COLUMN!r} is empty")

    annotated_text = _cell(row, ANNOTATED_TEXT_COLUMN)
    if annotated_text is not None:
        try:
            anki_note_tooling.notes.annotation.parse(annotated_text, slots=source.slots)
        except ValueError as e:
            raise CsvLoadError(f"row {row_number}: {e}") from e

    # No closed set to check against: which languages exist is the source's business, and
    # the source definition is where a line's default comes from.
    language = _cell(row, LANGUAGE_COLUMN) or source.language

    return ParsedRow(
        line=anki_note_tooling.lib.types.FlLine(
            source_type=source.name,
            source_file=_cell(row, SOURCE_FILE_COLUMN) or source_file,
            language=language,
            text=text,
            annotated_text=annotated_text,
            audio=None,
            image_path=None,
            misc=misc_data(row, default_source_tag=source_file),
        ),
        audio_filename=_cell(row, AUDIO_COLUMN),
        image_filename=_cell(row, IMAGE_COLUMN),
    )


def read_rows(input_csv: Path, *, source: anki_note_tooling.notes.config.Source) -> list[ParsedRow]:
    """Every row of `input_csv`, as lines, or `CsvLoadError` for the first one that is not."""
    with input_csv.open("r", newline="") as f:
        reader = csv.DictReader(f)
        check_headers(reader.fieldnames or [])
        # start=2 so a reported row number is the one a spreadsheet shows, header included.
        return [
            parse_row(row, source=source, source_file=input_csv.name, row_number=number)
            for number, row in enumerate(reader, start=2)
        ]


@dataclasses.dataclass(kw_only=True, frozen=True)
class ResolvedRows:
    """Lines with their media located, and the copies that will put it in the store."""

    lines: list[anki_note_tooling.lib.types.FlLine]
    media: list[anki_note_tooling.backfill.MediaCopy]


def resolve_media(
    rows: list[ParsedRow], *, media_dir: Path | None, fs: anki_note_tooling.config.FsConfig
) -> ResolvedRows:
    """
    Finds every file the rows name, or `CsvLoadError` listing the ones that are not there.

    A named file that is missing stops the load, which is where this parts company with
    `anki_note_tooling.backfill`: a backfill records notes Anki already holds, so it is better off
    recording a line without its image than not at all, while nothing here is recovering
    anything. A blank cell is not a gap — it says the line has no such file.
    """
    missing: list[str] = []
    lines: list[anki_note_tooling.lib.types.FlLine] = []
    copies: list[anki_note_tooling.backfill.MediaCopy] = []

    for row in rows:
        planned: dict[str, anki_note_tooling.lib.media.MediaRef | None] = {
            "audio": None,
            "image": None,
        }
        for column, filename in (
            (AUDIO_COLUMN, row.audio_filename),
            (IMAGE_COLUMN, row.image_filename),
        ):
            if filename is None:
                continue
            if media_dir is None:
                raise CsvLoadError(
                    f"{row.line.text!r} names {column} {filename!r}, but no --media-dir was given"
                )
            path = media_dir / filename
            if not path.is_file():
                missing.append(f"{column} for {row.line.text!r} is missing: {path}")
                continue
            copy = anki_note_tooling.backfill.planned_copy(path, fs=fs)
            copies.append(copy)
            planned[column] = copy.ref

        audio_ref = planned["audio"]
        lines.append(
            row.line.model_copy(
                update={
                    "audio": None
                    if audio_ref is None
                    else anki_note_tooling.lib.types.AudioFile(path=audio_ref),
                    "image_path": planned["image"],
                }
            )
        )

    if missing:
        raise CsvLoadError("\n".join(missing))
    return ResolvedRows(lines=lines, media=copies)


@app.command()
def load(
    input_csv: Path,
    source: str = typer.Option(..., help="The `sources/<name>.toml` these lines belong to."),
    media_dir: Path = typer.Option(None, help="Where the `audio` and `image` columns name files."),
    config_dir: Path = anki_note_tooling.config.cli_option,
    live_run: bool = typer.Option(False),
):
    """
    Creates `fl_line` rows from a CSV whose headers are the fields of a line.

    An unrecognised header is refused rather than ignored, so a misspelled column stops the
    load instead of silently dropping its data.

    Compulsory column:

    \b
      text              the line itself

    Optional columns:

    \b
      annotated_text    the annotated form, checked against the source's slots
      language          defaults to the source definition's `language`
      source_file       defaults to this file's own name
      audio             a file name, found in `--media-dir` and copied into the media store
      image             a file name, found in `--media-dir` and copied into the media store

    Optional metadata columns, each one a recipe can refer to as `$<name>`:

    \b
      character_name    who says the line
      english_line      a translation
      filename          the name of the file the line came from
      line_id           the line's id in the material it came from
      raw_text          the pre-normalisation original, where it differs from `text`
      source_tag        defaults to this file's own name

    Note that `source_file` and `filename` are different fields and both are kept: the
    first is the `fl_line` column recording where a line came from, the second is metadata
    a recipe can refer to as `$filename`.

    The whole file is read and every file it names located before anything is written, so a
    bad row or a missing image stops the load with nothing inserted rather than half of it.
    """
    config = anki_note_tooling.config.load(config_dir)
    source_definition = config.notes.source_for(source)

    rows = read_rows(input_csv, source=source_definition)
    resolved = resolve_media(
        rows,
        media_dir=None if media_dir is None else media_dir.resolve(),
        fs=config.fs,
    )

    engine = anki_note_tooling.data.tables.get_engine(config.database)
    with engine.connect() as conn:
        source_type_id = anki_note_tooling.data.queries.get_or_create_source_type(conn, source)
        anki_note_tooling.data.inserts.insert_lines(
            conn, resolved.lines, source_type_id=source_type_id
        )

        print(f"{len(resolved.lines)} lines read from {input_csv.name} as source {source!r}")
        if live_run:
            copied = anki_note_tooling.backfill.copy_media(resolved.media, fs=config.fs)
            print(f"{copied} of {len(resolved.media)} media files copied into the store")
            conn.commit()
            print("committed")
        else:
            print(f"{len(resolved.media)} media files would be copied into the store")
            print("not committed: pass --live-run to write")
