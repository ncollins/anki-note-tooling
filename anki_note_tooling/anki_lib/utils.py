import subprocess
from pathlib import Path

import anki.cards
import anki.collection
import anki.notes

import anki_note_tooling.config


def collection_path(*, anki_profile_dir):
    return anki_profile_dir / "collection.anki2"


def media_directory(*, anki_profile_dir: Path) -> Path:
    """
    Where Anki keeps a copy of every file its notes refer to.

    Flat, and named by `anki_note_tooling.file_utils.clean_file_name`, which is what makes it the
    last place a file a note was built from can still be found.
    """
    return anki_profile_dir / "collection.media"


def is_file_open(anki_collection: Path) -> bool:
    """
    Whether any process currently holds the Anki collection open.

    Both fusers agree on what matters — the PIDs go to stdout, and stdout is empty when
    nothing holds the file — but they disagree on the exit status, measured as:

        |                       | exit | stdout      |
        |-----------------------|------|-------------|
        | macOS, file open      | 0    | b'57881'    |
        | macOS, file free      | 0    | b''         |
        | Linux (psmisc), open  | 0    | b' 29015'   |
        | Linux (psmisc), free  | 1    | b''         |

    So the status must be ignored rather than allowed to raise: `check_output` turned
    "the collection is free, go ahead" into a crash everywhere but macOS.
    """
    result = subprocess.run(
        ["fuser", str(anki_collection)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return len(result.stdout.strip()) > 0


def get_collection(config: anki_note_tooling.config.Config) -> anki.collection.Collection:
    anki_profile_dir = config.anki.profile_dir
    anki_collection_path = collection_path(anki_profile_dir=anki_profile_dir)
    if is_file_open(anki_collection_path):
        raise Exception("Anki is already open")
    col = anki.collection.Collection(str(anki_collection_path))
    return col


def get_card_added(col: anki.collection.Collection, card_id: anki.cards.CardId):
    stats = col.card_stats_data(card_id)
    return stats.added


def get_note_added(col: anki.collection.Collection, note_id: anki.notes.NoteId):
    note = col.get_note(note_id)
    for card_id in note.card_ids():
        return get_card_added(col, card_id)
    else:
        raise Exception(f"Could not find cards for note {note_id}")


# The two markups Anki wraps a media reference in when it writes one into a field. Parsing
# them lives here rather than beside any one importer: it is Anki's syntax, so every reader
# of an exported collection meets the same shapes.
def parse_image_field(s: str) -> str:
    """
    The file name inside an `<img>` tag.

    Prefix and suffix are removed whole rather than as sets of characters, so a name
    beginning or ending with one of those characters survives intact.

    >>> parse_image_field('<img src="mochi_118-719_b7bb08e3.png" />')
    'mochi_118-719_b7bb08e3.png'
    """
    return s.strip().removeprefix('<img src="').removesuffix('" />')


def parse_sound_field(s: str) -> str:
    """
    The file name inside a `[sound:...]` field.

    >>> parse_sound_field("[sound:sound__6b57793e__00!01!48!420.mp3]")
    'sound__6b57793e__00!01!48!420.mp3'

    Anki writes a `<br>` after the tag when the field holds more than one line, so a
    trailing one is part of the markup rather than of the name:

    >>> parse_sound_field("[sound:episode_03-01.mp3]<br>")
    'episode_03-01.mp3'

    A `<br>` anywhere else means the field holds several sound tags, which is one file
    name too many to return:

    >>> parse_sound_field("[sound:a.mp3]<br>[sound:b.mp3]")
    Traceback (most recent call last):
        ...
    ValueError: field holds more than one sound tag: '[sound:a.mp3]<br>[sound:b.mp3]'
    """
    name = s.strip().removeprefix("[sound:").removesuffix("<br>").removesuffix("]")
    if "<br>" in name:
        raise ValueError(f"field holds more than one sound tag: {s!r}")
    return name
