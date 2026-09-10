"""
The record a recipe renders a line into, and the JSON shape it is stored as.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anki_note_tooling.lib.types


@dataclass(kw_only=True, frozen=True)
class RenderedNote:
    """
    One Anki note, ready to write.

    `ref` identifies the note within its line, so that regenerating a line whose
    substitutions have changed can tell an update from an insert from an orphan. It is
    authored (named after the recipe and the group it came from) rather than positional,
    so a note keeps its identity when the substitutions around it change.
    """

    recipe: str
    ref: str
    deck: str
    model: str
    fields: dict[str, str]
    #: The line as this recipe rendered it — the value of `$note_text`. Kept alongside the
    #: fields because it identifies the note to a human (and to the append-only log the
    #: ingesters check for already-added lines) without knowing which field holds it.
    text: str = ""
    tags: tuple[str, ...] = ()
    media: tuple[tuple[anki_note_tooling.lib.types.MediaType, Path], ...] = ()

    def to_record(self) -> dict[str, Any]:
        """
        The JSON stored in `managed_anki_note.note`.

        Self-describing and diffable, unlike the `jsons` dump with an embedded `-meta`
        classname it replaces: nothing here needs the class that wrote it to still exist.
        Media is derived from the line, so it is not stored.

        >>> note = RenderedNote(recipe="phrase", ref="phrase:kae", deck="D", model="M",
        ...                     fields={"Romanji": "kae"}, text="帰ろう", tags=("scene_C",))
        >>> note.to_record()
        {'recipe': 'phrase', 'ref': 'phrase:kae', 'deck': 'D', 'model': 'M', \
'fields': {'Romanji': 'kae'}, 'text': '帰ろう', 'tags': ['scene_C']}
        """
        return {
            "recipe": self.recipe,
            "ref": self.ref,
            "deck": self.deck,
            "model": self.model,
            "fields": dict(self.fields),
            "text": self.text,
            "tags": list(self.tags),
        }


@dataclass(kw_only=True, frozen=True)
class StoredNote:
    """
    A `managed_anki_note` row as the reconciler sees it.

    `anki_note_id` may point at a note that no longer exists in the collection, so
    resolving it is the caller's problem rather than something to assume.
    """

    row_id: int
    anki_note_id: int | None
    ref: str | None
    record: dict[str, Any]

    @property
    def fields(self) -> dict[str, str]:
        return self.record.get("fields", {})

    @property
    def text(self) -> str:
        return self.record.get("text", "")
