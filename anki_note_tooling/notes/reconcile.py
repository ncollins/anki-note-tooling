"""
Matching the notes a line renders to now against the ones already recorded for it.

With user-editable recipes, a line's set of notes changing shape is routine rather than an
edge case: an annotation gains or loses a substitution and the notes it renders to shift
with it. So matching is a three-way diff on authored refs — present in both is an update,
new is an insert, and **recorded but no longer generated is reported, never silently
deleted**. Looking notes up positionally instead would repoint a surviving note onto a
different note's content.
"""

from dataclasses import dataclass, field

import anki_note_tooling.notes.note


@dataclass(kw_only=True, frozen=True)
class Reconciliation:
    """What to do with one line's notes."""

    to_insert: list[anki_note_tooling.notes.note.RenderedNote] = field(default_factory=list)
    to_update: list[
        tuple[anki_note_tooling.notes.note.StoredNote, anki_note_tooling.notes.note.RenderedNote]
    ] = field(default_factory=list)
    #: Recorded notes the line no longer renders. Deleting these needs an explicit
    #: `--prune`: an edited annotation that drops a substitution is a routine mistake, and
    #: the Anki note behind it may hold review history worth keeping.
    orphaned: list[anki_note_tooling.notes.note.StoredNote] = field(default_factory=list)
    #: Recorded notes whose `anki_note_id` is missing or no longer resolves in the
    #: collection. They cannot be updated in place, so they are surfaced rather than
    #: assumed away.
    unresolved: list[anki_note_tooling.notes.note.StoredNote] = field(default_factory=list)

    def summary(self) -> str:
        """
        >>> Reconciliation().summary()
        '0 to insert, 0 to update, 0 orphaned, 0 unresolved'
        """
        return (
            f"{len(self.to_insert)} to insert, {len(self.to_update)} to update, "
            f"{len(self.orphaned)} orphaned, {len(self.unresolved)} unresolved"
        )


def reconcile(
    rendered: list[anki_note_tooling.notes.note.RenderedNote],
    stored: list[anki_note_tooling.notes.note.StoredNote],
    *,
    resolvable: frozenset[int] | None = None,
) -> Reconciliation:
    """
    Splits `rendered` against `stored`, matching on ref.

    `resolvable`, when given, is the set of `anki_note_id`s that still exist in the
    collection; a stored row pointing outside it is unresolved rather than updatable. Two
    of the production rows are in exactly that state.

    >>> from anki_note_tooling.notes.note import RenderedNote, StoredNote
    >>> def rendered_note(ref):
    ...     return RenderedNote(recipe="phrase", ref=ref, deck="d", model="m", fields={})
    >>> def stored_note(row_id, ref, anki_note_id=1):
    ...     return StoredNote(row_id=row_id, anki_note_id=anki_note_id, ref=ref, record={})
    >>> result = reconcile(
    ...     [rendered_note("phrase:a"), rendered_note("phrase:new")],
    ...     [stored_note(1, "phrase:a"), stored_note(2, "phrase:gone")],
    ... )
    >>> [n.ref for n in result.to_insert]
    ['phrase:new']
    >>> [(s.row_id, n.ref) for s, n in result.to_update]
    [(1, 'phrase:a')]
    >>> [s.ref for s in result.orphaned]
    ['phrase:gone']

    A stored row whose Anki note has been deleted is reported, not updated:

    >>> result = reconcile([rendered_note("phrase:a")], [stored_note(1, "phrase:a", 99)],
    ...                    resolvable=frozenset({1}))
    >>> [s.row_id for s in result.unresolved], result.to_update
    ([1], [])
    """
    stored_by_ref = {note.ref: note for note in stored if note.ref is not None}
    result = Reconciliation(unresolved=[note for note in stored if note.ref is None])

    for note in rendered:
        existing = stored_by_ref.get(note.ref)
        if existing is None:
            result.to_insert.append(note)
        elif existing.anki_note_id is None or (
            resolvable is not None and existing.anki_note_id not in resolvable
        ):
            result.unresolved.append(existing)
        else:
            result.to_update.append((existing, note))

    generated = {note.ref for note in rendered}
    result.orphaned.extend(note for ref, note in stored_by_ref.items() if ref not in generated)

    return result
