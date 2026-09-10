import shutil
from pathlib import Path
from typing import Optional

# `anki.collection` first: anki's own modules import each other in a cycle that only
# resolves when it is the one to be imported first.
import anki.collection
import anki.collection_pb2
import anki.notes
from anki.collection import Collection

import anki_note_tooling.anki_lib.utils
import anki_note_tooling.config
import anki_note_tooling.lib.types
import anki_note_tooling.notes.note
from anki_note_tooling.file_utils import clean_file_name, create_mp3_file


class AnkiWriteError(Exception):
    """A note could not be written to the collection as the recipe describes it."""


def _add_media_to_anki(
    note: anki_note_tooling.notes.note.RenderedNote,
    anki_media_directory: Path,
    volume_scale: Optional[float],
):
    """
    Copies a note's media into the collection.

    `note.media` holds absolute paths — whoever built the note resolved them — so nothing
    here needs to know where the media root is.
    """
    for media_type, media_file in note.media:
        print(f"Handing {(media_type, media_file)}")
        match media_type:
            case anki_note_tooling.lib.types.MediaType.AUDIO:
                output_audio = anki_media_directory / clean_file_name(
                    media_file.with_suffix(".mp3").name
                )
                create_mp3_file(
                    input_file=media_file,
                    output_file=output_audio,
                    volume_scale=volume_scale,
                )
                assert output_audio.exists()
            case anki_note_tooling.lib.types.MediaType.IMAGE:
                output_image = anki_media_directory / clean_file_name(media_file.name)
                shutil.copy(media_file, output_image)
                print(f"Copied {media_file} to {output_image}")


def _write_fields(n: anki.notes.Note, note: anki_note_tooling.notes.note.RenderedNote) -> None:
    """
    Writes the recipe's fields onto `n` by name.

    Assigning `n.fields` positionally would silently corrupt every note if the field
    order were ever changed inside Anki. Writing by name also means fields the recipe does
    not mention keep whatever is already in them, so edits made directly in Anki survive an
    update.
    """
    available = set(n.keys())
    unknown = sorted(set(note.fields) - available)
    if unknown:
        raise AnkiWriteError(
            f"model {note.model!r} has no field(s) {', '.join(unknown)} "
            f"(it has: {', '.join(sorted(available))})"
        )
    for name, value in note.fields.items():
        n[name] = value


def add_note_and_media_to_anki(
    anki_profile: Path,
    note: anki_note_tooling.notes.note.RenderedNote,
    *,
    volume_scale: Optional[float] = None,
    anki_note_id: anki.notes.NoteId | None = None,
) -> anki.notes.NoteId:
    if anki_note_id is None:
        print(f"About to add {note} to Anki")
    else:
        print(f"About to updated {anki_note_id} to {note}")

    anki_collection_path = anki_note_tooling.anki_lib.utils.collection_path(
        anki_profile_dir=anki_profile
    )
    if anki_note_tooling.anki_lib.utils.is_file_open(anki_collection_path):
        raise Exception("Anki is already open")
    col = Collection(str(anki_collection_path))
    media_directory = anki_note_tooling.anki_lib.utils.media_directory(
        anki_profile_dir=anki_profile
    )

    print(f"Col = {col}, media_directory = {media_directory}")

    _add_media_to_anki(note, media_directory, volume_scale=volume_scale)

    print(f"Added media for {note}")

    deck_id = col.decks.id_for_name(note.deck)
    if deck_id is None:
        raise AnkiWriteError(f"Could not find deck: {note.deck}")

    n: anki.notes.Note
    if anki_note_id is None:
        note_type = col.models.by_name(note.model)
        if note_type is None:
            raise AnkiWriteError(f"Could not find model: {note.model}")

        n = anki.notes.Note(col=col, model=note_type, id=None)
        print(f"Created note: {n}")
    else:
        n = anki.notes.Note(col=col, model=None, id=anki_note_id)
        print(f"Found existing note: {n}")

    _write_fields(n, note)
    for tag in note.tags:
        n.add_tag(tag)  # TODO: add support for deleting tags

    _res: anki.collection_pb2.OpChanges
    if anki_note_id is None:
        _res = col.add_note(n, deck_id)
    else:
        _res = col.update_note(n)

    col.autosave()
    col.close_for_full_sync()

    return anki_note_id or n.id
