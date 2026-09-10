from dataclasses import dataclass
from enum import StrEnum
from typing import Optional

import pydantic

import anki_note_tooling.lib.media


@dataclass(kw_only=True, frozen=True)
class FlMiscData:
    character_name: Optional[str] = None
    raw_text: Optional[str] = None
    english_line: Optional[str] = None
    source_tag: Optional[str] = None
    filename: Optional[str] = None
    line_id: Optional[str] = None


class MediaType(StrEnum):
    AUDIO = "audio"
    IMAGE = "image"


@dataclass(kw_only=True, frozen=True)
class AudioFile:
    path: anki_note_tooling.lib.media.MediaRef


class FlLine(pydantic.BaseModel):
    """
    One flashcard line: the text, its media, and the metadata a recipe can refer to.

    This is what every importer normalizes its input into, and so the one shape
    `anki_note_tooling.data.inserts.insert_lines` has to understand. A line that has been inserted is a
    `anki_note_tooling.data.types.StoredFlLine`, which adds the row's identity and nothing else.

    `text` is the line itself, and is the value `fl_line.text` holds. Where a source has a
    pre-normalization original that differs from it, that belongs in `misc.raw_text`.

    Frozen so that the narrowed field types on `anki_note_tooling.data.types.CompleteFlLine` are sound:
    a subclass may promise more about what a field holds only if nothing can write to it.
    """

    model_config = pydantic.ConfigDict(frozen=True)

    # core data
    source_type: str
    source_file: Optional[str]
    language: str
    text: str
    # secondary data (may not exist at first). The annotation is held as the raw string:
    # parsing it needs the source's declared slots, which a line does not carry.
    annotated_text: Optional[str]
    audio: Optional[AudioFile]
    image_path: Optional[anki_note_tooling.lib.media.MediaRef]
    # metadata
    misc: FlMiscData
