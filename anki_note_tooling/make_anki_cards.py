"""
Turning stored lines into Anki notes.

The construction itself lives in `anki_note_tooling.notes.renderer`; what this module adds is the
adapter from a database row to something a recipe can be rendered against, and the command that
walks the uninserted lines and puts what they render to into Anki.
"""

from pathlib import Path

import typer
from sqlalchemy import Engine

import anki_note_tooling.anki_shared
import anki_note_tooling.config
import anki_note_tooling.data.inserts
import anki_note_tooling.data.queries
import anki_note_tooling.data.tables
import anki_note_tooling.data.types
import anki_note_tooling.lib.media
import anki_note_tooling.lib.types
import anki_note_tooling.notes.config
import anki_note_tooling.notes.note
import anki_note_tooling.notes.renderer

app = typer.Typer()


def to_render_line(
    complete_fl_line: anki_note_tooling.data.types.CompleteFlLine,
    *,
    media: anki_note_tooling.lib.media.MediaStore,
) -> anki_note_tooling.notes.renderer.LineToRender:
    """
    The subset of a stored line a recipe can refer to.

    Media is resolved to absolute paths here. `LineToRender` is the renderer's port and is
    shared with the ingesters that render straight from a directory of files, so it carries
    paths that can be opened rather than locations relative to the media root.
    """
    assert isinstance(complete_fl_line.audio, anki_note_tooling.lib.types.AudioFile)
    misc = complete_fl_line.misc
    return anki_note_tooling.notes.renderer.LineToRender(
        source_name=complete_fl_line.source_type,
        annotated_text=complete_fl_line.annotated_text,
        text=complete_fl_line.text,
        language=complete_fl_line.language,
        english_line=misc.english_line or "",
        source_tag=misc.source_tag or "",
        character_name=misc.character_name or "",
        audio_path=media.locate(complete_fl_line.audio.path),
        image_path=media.locate(complete_fl_line.image_path),
    )


def create_notes(
    complete_fl_line: anki_note_tooling.data.types.CompleteFlLine,
    *,
    note_config: anki_note_tooling.notes.config.NoteConfig,
    media: anki_note_tooling.lib.media.MediaStore,
) -> list[anki_note_tooling.notes.note.RenderedNote]:
    """
    Every note the line's source is configured to produce.

    A line whose source has no definition raises `SourceConfigError` — a normal state
    worth reporting rather than a crash.
    """
    return anki_note_tooling.notes.renderer.render_all(
        to_render_line(complete_fl_line, media=media), config=note_config
    )


def volume_scale(
    complete_fl_line: anki_note_tooling.data.types.CompleteFlLine,
    *,
    note_config: anki_note_tooling.notes.config.NoteConfig,
) -> float | None:
    """
    The audio volume scaling for a line, as a multiplier.

    Owned by the source definition rather than by an `fl_source_type` column, so there is
    one place it is set, and one place — this function — that the CLI and the web UI both
    read it through.
    """
    source = note_config.sources.get(complete_fl_line.source_type)
    if source is None or source.volume_scale_percentage is None:
        return None
    return source.volume_scale_percentage / 100


def volume_scale_percentage(
    complete_fl_line: anki_note_tooling.data.types.CompleteFlLine,
    *,
    note_config: anki_note_tooling.notes.config.NoteConfig,
) -> int | None:
    source = note_config.sources.get(complete_fl_line.source_type)
    return None if source is None else source.volume_scale_percentage


PreparedLine = tuple[
    anki_note_tooling.data.types.CompleteFlLine, list[anki_note_tooling.notes.note.RenderedNote]
]


def staging_notes(config: anki_note_tooling.config.Config, engine: Engine) -> list[PreparedLine]:
    """
    Every complete line with no notes yet, paired with the notes it renders to.

    Rendering the whole batch before any of it is written is what lets the dry run show exactly
    what a live run would add, and what stops a source with no usable definition being
    discovered halfway through writing to Anki.

    A line whose source has no definition is reported and skipped rather than raised on: it is a
    normal state for a line imported before anyone decided what notes it should make.
    """
    with engine.connect() as conn:
        lines = anki_note_tooling.data.queries.get_complete_fl_lines(conn, selection="uninserted")

    prepared: list[PreparedLine] = []
    for line in lines:
        try:
            notes = create_notes(line, note_config=config.notes, media=config.fs.media)
        except anki_note_tooling.notes.config.SourceConfigError as e:
            print(f"Skipping line {line.source_id}: {e}")
            continue
        prepared.append((line, notes))
    return prepared


@app.command()
def make_staging_notes(
    config_dir: Path = anki_note_tooling.config.cli_option,
    live_run: bool = typer.Option(False),
):
    """
    Adds to Anki the notes for every complete line that has none yet.

    Without `--live-run` every note is rendered and printed and nothing is written, which is the
    only way to see what a run would add before it adds it. This matters more here than for the
    other commands: a note written into the Anki collection cannot be taken back by this tool.
    """
    config = anki_note_tooling.config.load(config_dir)
    engine = anki_note_tooling.data.tables.get_engine(config.database)

    prepared = staging_notes(config, engine)
    for line, notes in prepared:
        print()
        print(line)
        for note in notes:
            print(f"  {note}")

    note_count = sum(len(notes) for _, notes in prepared)
    print()
    if not live_run:
        print(f"{note_count} note(s) from {len(prepared)} line(s) would be added to Anki")
        print("not committed: pass --live-run to write")
        return

    # One connection for the run, but a commit per note: a note added to Anki cannot be rolled
    # back, so its row is recorded before the next note is attempted. Committing the batch once
    # at the end would, on a failure partway, leave notes in Anki that no row accounts for —
    # and the next run would add them a second time.
    with engine.connect() as conn:
        for line, notes in prepared:
            for note in notes:
                anki_note_id = anki_note_tooling.anki_shared.add_note_and_media_to_anki(
                    config.anki.profile_dir,
                    note,
                    volume_scale=volume_scale(line, note_config=config.notes),
                )
                anki_note_tooling.data.inserts.insert_anki_note(
                    conn,
                    source_id=line.source_id,
                    note=note,
                    anki_note_id=anki_note_id,
                    volume_scale_percentage=volume_scale_percentage(line, note_config=config.notes),
                )
                conn.commit()
                print(f"Added: {note}")
            anki_note_tooling.data.queries.mark_up_to_date_anki_notes(conn, [line.source_id])
            conn.commit()

    print()
    print(f"{note_count} note(s) added to Anki")
