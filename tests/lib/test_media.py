"""
The boundary between a media file's stored location and its path on disk.

The doctests in `anki_note_tooling.lib.media` cover the happy path; what is here is the property that
makes the two forms interchangeable, and the refusals that stop them being confused.
"""

from pathlib import Path, PurePosixPath

import hypothesis
import hypothesis.strategies as st
import pytest

import anki_note_tooling.lib.media

ROOT = Path("/files")
STORE = anki_note_tooling.lib.media.MediaStore(root=ROOT)

#: Path segments that are legal in a location and carry no meaning to the filesystem.
segments = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126, blacklist_characters="/"),
    min_size=1,
).filter(lambda s: s not in (".", ".."))


@hypothesis.given(parts=st.lists(segments, min_size=1, max_size=4))
def test_locating_a_stored_path_gives_it_back(parts):
    """`store` and `locate` are inverses for any path under the root."""
    path = ROOT.joinpath(*parts)
    assert STORE.locate(STORE.store(path)) == path


@hypothesis.given(parts=st.lists(segments, min_size=1, max_size=4))
def test_a_stored_location_round_trips_through_its_serialized_form(parts):
    """What goes into the database is what comes back out of it."""
    ref = STORE.store(ROOT.joinpath(*parts))
    assert anki_note_tooling.lib.media.MediaRef.parse(str(ref)) == ref


def test_store_refuses_a_relative_path():
    """A location that has already been converted cannot be converted again."""
    with pytest.raises(anki_note_tooling.lib.media.MediaPathError):
        STORE.store(Path("images/portrait.png"))


def test_store_refuses_a_path_outside_the_root():
    with pytest.raises(anki_note_tooling.lib.media.MediaPathError):
        STORE.store(Path("/elsewhere/portrait.png"))


def test_parse_refuses_an_absolute_location():
    with pytest.raises(anki_note_tooling.lib.media.MediaPathError):
        anki_note_tooling.lib.media.MediaRef.parse("/files/images/portrait.png")


@pytest.mark.parametrize("value", ["../secrets.png", "images/../../secrets.png", ".."])
def test_parse_refuses_a_location_that_climbs_out_of_the_root(value):
    """
    `locate` joins onto the root without resolving, so `..` has to be refused here.

    These values reach `parse` from a submitted form, which is the reason it matters.
    """
    with pytest.raises(anki_note_tooling.lib.media.MediaPathError):
        anki_note_tooling.lib.media.MediaRef.parse(value)


def test_filename_is_the_basename():
    ref = anki_note_tooling.lib.media.MediaRef(path=PurePosixPath("a/b/c.png"))
    assert ref.filename == "c.png"
