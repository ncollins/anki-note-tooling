"""
Bringing a source's media inside the media root.

A stored location is relative to `fs.files_path`, so a file a source keeps in its own
directory has to be copied in before a line can point at it. What these pin is that the
copy keeps the name a rendered note refers to, that re-running is safe, and that two
different files wanting one name stops the run.
"""

from pathlib import Path

import pytest

import anki_note_tooling.backfill as backfill
import anki_note_tooling.config


def _fs(tmp_path: Path) -> anki_note_tooling.config.FsConfig:
    return anki_note_tooling.config.FsConfig(
        files_path=tmp_path,
        managed_language_learning_files=tmp_path / "managed",
        tmp_files=tmp_path / "tmp",
    )


def _source_file(tmp_path: Path, name: str, content: bytes = b"audio") -> Path:
    source_dir = tmp_path / "export"
    source_dir.mkdir(exist_ok=True)
    path = source_dir / name
    path.write_bytes(content)
    return path


def test_a_planned_copy_keeps_the_name_a_note_refers_to(tmp_path):
    fs = _fs(tmp_path)
    planned = backfill.locate_media(
        [_source_file(tmp_path, "これは本です.mp3")], fs=fs, line_text="x", description="audio"
    )
    assert isinstance(planned, backfill.MediaCopy)

    assert planned.ref.filename == "これは本です.mp3"
    assert str(planned.ref) == "managed/これは本です.mp3"


def test_copying_is_safe_to_repeat(tmp_path):
    """A second run over the same export should adopt what the first one stored."""
    fs = _fs(tmp_path)
    planned = backfill.locate_media(
        [_source_file(tmp_path, "line.mp3")], fs=fs, line_text="x", description="audio"
    )
    assert isinstance(planned, backfill.MediaCopy)

    assert backfill.copy_media([planned], fs=fs) == 1
    assert backfill.copy_media([planned], fs=fs) == 0
    assert (fs.managed_language_learning_files / "line.mp3").read_bytes() == b"audio"


def test_two_different_files_wanting_one_name_stop_the_run(tmp_path):
    fs = _fs(tmp_path)
    first = backfill.locate_media(
        [_source_file(tmp_path, "line.mp3", b"one")], fs=fs, line_text="x", description="audio"
    )
    assert isinstance(first, backfill.MediaCopy)
    backfill.copy_media([first], fs=fs)

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    (other_dir / "line.mp3").write_bytes(b"two")
    second = backfill.locate_media(
        [other_dir / "line.mp3"], fs=fs, line_text="x", description="audio"
    )
    assert isinstance(second, backfill.MediaCopy)

    with pytest.raises(backfill.BackfillError, match="already holds a different file"):
        backfill.copy_media([second], fs=fs)
    # The file that was there first is untouched.
    assert (fs.managed_language_learning_files / "line.mp3").read_bytes() == b"one"


def test_a_file_in_none_of_the_places_looked_is_reported_with_all_of_them(tmp_path):
    fs = _fs(tmp_path)
    gap = backfill.locate_media(
        [tmp_path / "export" / "gone.mp3", tmp_path / "anki" / "gone.mp3"],
        fs=fs,
        line_text="本 を 読む",
        description="audio",
    )

    assert isinstance(gap, backfill.MissingMedia)
    assert gap.expected == tmp_path / "export" / "gone.mp3"
    assert "本 を 読む" in str(gap)
    assert str(tmp_path / "anki" / "gone.mp3") in str(gap)


def test_optional_media_that_is_nowhere_is_simply_absent(tmp_path):
    """A line that never had an image is not a line with a missing one."""
    assert backfill.locate_optional_media([tmp_path / "gone.png"], fs=_fs(tmp_path)) is None
