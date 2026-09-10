from typing import Sequence

import anki.collection
import anki.notes


def get_notes_in_deck(
    col: anki.collection.Collection,
    deck: str,
) -> list[tuple[anki.notes.NoteId, list[str]]]:
    """
    Every note in `deck`, as `(id, fields)`.

    The deck name is quoted into the search rather than interpolated bare, so one
    containing a space still names a single deck.
    """
    note_ids: Sequence[anki.notes.NoteId] = col.find_notes(f'"deck:{deck}"')
    return [(note_id, col.get_note(note_id).fields) for note_id in note_ids]


def get_note_ids_by_field(
    col: anki.collection.Collection,
    *,
    deck: str,
    field: str,
) -> dict[str, anki.notes.NoteId]:
    """
    The notes in `deck` that have `field`, keyed by what that field holds.

    Read by field name rather than by position, so a deck holding more than one model —
    or a model whose fields have been reordered inside Anki — still keys on the right
    value; notes of a model without the field are skipped rather than mis-keyed.

    Two notes with the same value collapse to whichever comes last. That is a real
    possibility (the same line matched under two sources), and there is nothing here that
    could choose between them.
    """
    note_ids_by_field_value: dict[str, anki.notes.NoteId] = {}
    for note_id in col.find_notes(f'"deck:{deck}"'):
        note = col.get_note(note_id)
        if field in note.keys():
            note_ids_by_field_value[note[field]] = note_id
    return note_ids_by_field_value
