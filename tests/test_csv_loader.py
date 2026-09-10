"""Reading a CSV as flashcard lines, and the checks that stop a bad file being loaded."""

from pathlib import Path

import pytest

import anki_note_tooling.config
import anki_note_tooling.csv_loader
import anki_note_tooling.lib.types
import anki_note_tooling.notes.config

SOURCE = anki_note_tooling.notes.config.Source(
    name="drama",
    language="zh-yue",
    slots=("hanzi", "jyutping"),
)


def _write(tmp_path: Path, contents: str, name: str = "lines.csv") -> Path:
    path = tmp_path / name
    path.write_text(contents, encoding="utf-8")
    return path


def _fs(tmp_path: Path) -> anki_note_tooling.config.FsConfig:
    store = tmp_path / "store"
    store.mkdir(exist_ok=True)
    return anki_note_tooling.config.FsConfig(
        files_path=tmp_path, managed_language_learning_files=store, tmp_files=tmp_path
    )


def test_a_row_becomes_a_line_with_its_metadata(tmp_path):
    csv_file = _write(
        tmp_path,
        "text,annotated_text,english_line,character_name\n食飯,[[食飯::sik6 faan6]],to eat,阿明\n",
    )

    [row] = anki_note_tooling.csv_loader.read_rows(csv_file, source=SOURCE)

    assert row.line.text == "食飯"
    assert row.line.annotated_text == "[[食飯::sik6 faan6]]"
    assert row.line.misc.english_line == "to eat"
    assert row.line.misc.character_name == "阿明"


def test_the_source_supplies_the_language_and_the_file_supplies_its_own_name(tmp_path):
    """
    Neither is worth a column in the common case.

    The language is a property of the source, not of each line, and `source_file` and
    `source_tag` both answer "where did this come from" — which the file already knows.
    """
    csv_file = _write(tmp_path, "text\n食飯\n", name="episode1.csv")

    [row] = anki_note_tooling.csv_loader.read_rows(csv_file, source=SOURCE)

    assert row.line.language == "zh-yue"
    assert row.line.source_file == "episode1.csv"
    assert row.line.misc.source_tag == "episode1.csv"


def test_a_column_the_loader_does_not_know_stops_the_load(tmp_path):
    """A misspelled header would otherwise load a file with the column silently absent."""
    csv_file = _write(tmp_path, "text,englsh_line\n食飯,to eat\n")

    with pytest.raises(anki_note_tooling.csv_loader.CsvLoadError, match="englsh_line"):
        anki_note_tooling.csv_loader.read_rows(csv_file, source=SOURCE)


def test_an_annotation_is_checked_against_the_sources_slots(tmp_path):
    """
    The same check the annotation editor makes, made at load time.

    A three-slot annotation against a two-slot source renders nothing, so catching it here
    is the difference between one bad row and a source that fails at note generation.
    """
    csv_file = _write(tmp_path, "text,annotated_text\n食飯,[[食飯::sik6 faan6::eat]]\n")

    with pytest.raises(anki_note_tooling.csv_loader.CsvLoadError, match="row 2"):
        anki_note_tooling.csv_loader.read_rows(csv_file, source=SOURCE)


def test_media_is_planned_as_a_copy_into_the_store(tmp_path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "eat.mp3").write_bytes(b"audio")
    csv_file = _write(tmp_path, "text,audio\n食飯,eat.mp3\n")

    rows = anki_note_tooling.csv_loader.read_rows(csv_file, source=SOURCE)
    resolved = anki_note_tooling.csv_loader.resolve_media(
        rows, media_dir=media_dir, fs=_fs(tmp_path)
    )

    [line] = resolved.lines
    assert line.audio is not None
    assert line.audio.path.filename == "eat.mp3"
    assert [copy.source for copy in resolved.media] == [media_dir / "eat.mp3"]
    # Planned only: nothing is copied until the command commits.
    assert not (tmp_path / "store" / "eat.mp3").exists()


def test_a_named_file_that_is_not_there_stops_the_load(tmp_path):
    """
    Unlike a backfill, which records notes Anki already holds whatever survives.

    Nothing is being recovered here, so a line that names a file it cannot have is a
    mistake in the file rather than a gap to live with.
    """
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    csv_file = _write(tmp_path, "text,image\n食飯,missing.png\n")

    rows = anki_note_tooling.csv_loader.read_rows(csv_file, source=SOURCE)
    with pytest.raises(anki_note_tooling.csv_loader.CsvLoadError, match="missing.png"):
        anki_note_tooling.csv_loader.resolve_media(rows, media_dir=media_dir, fs=_fs(tmp_path))


def test_a_blank_media_cell_is_not_a_missing_file(tmp_path):
    csv_file = _write(tmp_path, "text,audio\n食飯,\n")

    rows = anki_note_tooling.csv_loader.read_rows(csv_file, source=SOURCE)
    resolved = anki_note_tooling.csv_loader.resolve_media(rows, media_dir=None, fs=_fs(tmp_path))

    assert resolved.lines[0].audio is None
    assert resolved.media == []


def test_the_help_text_documents_every_column_the_loader_accepts():
    """
    `MISC_COLUMNS` is derived from `FlMiscData`, so a field added there becomes loadable
    without anyone editing this module. The help text lists the columns by hand, which is what
    makes that convenient derivation a way for the documentation to fall silently behind: the
    new column works, and nothing tells anyone it exists.
    """
    help_text = anki_note_tooling.csv_loader.load.__doc__ or ""

    undocumented = sorted(
        column
        for column in anki_note_tooling.csv_loader.KNOWN_COLUMNS
        if f"  {column} " not in help_text
    )

    assert undocumented == [], (
        f"columns accepted but not listed in `csv load --help`: {undocumented}"
    )
