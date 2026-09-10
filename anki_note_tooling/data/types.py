import dataclasses
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Optional

import anki_note_tooling.lib.media
import anki_note_tooling.lib.types


class StoredFlLine(anki_note_tooling.lib.types.FlLine):
    """
    One `fl_line` row, in whatever state of completeness.

    The content it holds is `anki_note_tooling.lib.types.FlLine`; what this adds is the row — an
    identity, and the bookkeeping column that goes with having been stored. `source_type_id`
    is deliberately absent: it is a foreign key belonging to the row rather than a fact about
    the line, and the human-meaningful `source_type` name is what every caller wants.
    """

    source_id: int
    up_to_date_anki_notes: bool

    def completed(self) -> "CompleteFlLine | None":
        """
        This line as a `CompleteFlLine`, or None if annotation, audio or image is missing.

        The one place completeness is decided. The equivalent WHERE clauses in
        `anki_note_tooling.data.queries` exist so that LIMIT counts only usable rows, not as a second
        definition that could disagree with this one.

        >>> line = StoredFlLine(
        ...     source_id=1,
        ...     source_type="podcast",
        ...     source_file=None,
        ...     language="ja",
        ...     text="行く",
        ...     annotated_text=None,
        ...     audio=None,
        ...     image_path=None,
        ...     misc=anki_note_tooling.lib.types.FlMiscData(),
        ...     up_to_date_anki_notes=False,
        ... )
        >>> line.completed() is None
        True
        >>> ready = line.model_copy(
        ...     update={
        ...         "annotated_text": "[[行く]]",
        ...         "audio": anki_note_tooling.lib.types.AudioFile(
        ...             path=anki_note_tooling.lib.media.MediaRef.parse("go.mp3")
        ...         ),
        ...         "image_path": anki_note_tooling.lib.media.MediaRef.parse("go.png"),
        ...     }
        ... )
        >>> complete = ready.completed()
        >>> complete.annotated_text
        '[[行く]]'
        >>> isinstance(complete, StoredFlLine)
        True
        """
        if self.annotated_text is None or self.audio is None or self.image_path is None:
            return None
        # dict() is shallow, so the nested Audio and FlMiscData pass through as they are.
        return CompleteFlLine(**dict(self))


class CompleteFlLine(StoredFlLine):
    """
    A stored line with annotation, audio and image all present, so a recipe can render it.

    A refinement of `StoredFlLine` rather than a sibling: it promises more about three
    fields and changes nothing else, so it is accepted anywhere a `StoredFlLine` is. Build one
    with `StoredFlLine.completed()` rather than by hand.
    """

    annotated_text: str
    audio: anki_note_tooling.lib.types.AudioFile
    image_path: anki_note_tooling.lib.media.MediaRef


class MiscSearchField(StrEnum):
    """
    The `FlMiscData` fields the search page can filter and suggest on.

    Each value is a JSON key that ends up inside a `json_extract` path, so parsing a request
    value through this enum is what keeps that path an allowlist rather than something a
    caller can steer.

    >>> {field.value for field in MiscSearchField} <= {
    ...     f.name for f in dataclasses.fields(anki_note_tooling.lib.types.FlMiscData)
    ... }
    True
    """

    CHARACTER_NAME = "character_name"
    FILENAME = "filename"
    SOURCE_TAG = "source_tag"


@dataclass(kw_only=True, frozen=True)
class FlLineSearchFilters:
    """
    The criteria the search page narrows `fl_line` by.

    Every field is optional and an unset one contributes no clause, so the empty instance
    matches every line. `exclude_inserted` deliberately does not count towards `is_empty`:
    on its own it would page through the entire table, which is not what ticking a box
    labelled "exclude" asks for.

    >>> FlLineSearchFilters().is_empty
    True
    >>> FlLineSearchFilters(exclude_inserted=True).is_empty
    True
    >>> FlLineSearchFilters(source_types=("podcast",)).is_empty
    False
    >>> FlLineSearchFilters(character_name="Hanako").misc_criteria
    ((<MiscSearchField.CHARACTER_NAME: 'character_name'>, 'Hanako'),)
    """

    text: Optional[str] = None
    source_types: tuple[str, ...] = ()
    language: Optional[str] = None
    character_name: Optional[str] = None
    filename: Optional[str] = None
    source_tag: Optional[str] = None
    exclude_inserted: bool = False

    @property
    def misc_criteria(self) -> tuple[tuple[MiscSearchField, str], ...]:
        """The populated `FlMiscData` criteria, as (field, substring) pairs."""
        by_field = {
            MiscSearchField.CHARACTER_NAME: self.character_name,
            MiscSearchField.FILENAME: self.filename,
            MiscSearchField.SOURCE_TAG: self.source_tag,
        }
        return tuple((field, value) for field, value in by_field.items() if value)

    @property
    def is_empty(self) -> bool:
        """Whether no criteria are set, i.e. nothing has been asked for yet."""
        return not (self.text or self.source_types or self.language or self.misc_criteria)


@dataclass(kw_only=True, frozen=True)
class ImagePoolEntry:
    """
    One row of `image_pool_entry`, serving both the insert and the query side.

    `key` is what `[source.images].match` compares a line variable against; it is
    populated at import time wherever the source has one, whether or not any config
    consults it.
    """

    image: anki_note_tooling.lib.media.MediaRef
    key: Optional[str] = None
    label: Optional[str] = None
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)
