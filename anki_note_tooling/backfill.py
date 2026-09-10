"""
Recording lines and notes that are already in Anki but were never given a row.

A source whose notes reached Anki outside the database leaves nothing behind that points
at them: `managed_anki_note` has no row, so `get_complete_fl_lines(selection="uninserted")`
still offers those lines and re-inserting would duplicate every note. The way back is to
create the `fl_line` rows, render what each line produces, match those notes to the ones
already in the collection, and insert a `managed_anki_note` row for each.

The only thing the two sides share is the rendered line, so that is what the match is on.
It is exact: a note whose text has drifted since it was added — a re-tokenized annotation,
a corrected reading — will not be found, and the command fails rather than guessing. That
is the intended behaviour. A backfill that silently skipped what it could not place would
leave exactly the gap it exists to close.
"""

import datetime
import filecmp
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import anki.collection
import anki.notes
import sqlalchemy

import anki_note_tooling.anki_lib.queries
import anki_note_tooling.anki_lib.utils
import anki_note_tooling.config
import anki_note_tooling.data.inserts
import anki_note_tooling.data.queries
import anki_note_tooling.lib.media
import anki_note_tooling.lib.types
import anki_note_tooling.notes.config
import anki_note_tooling.notes.note

#: The template a recipe field must hold for that field to be the rendered line.
NOTE_TEXT_TEMPLATE = "$note_text"


class BackfillError(Exception):
    """A note already in Anki could not be identified, so nothing should be written."""


@dataclass(kw_only=True, frozen=True)
class MediaCopy:
    """A media file to bring into the store, and the location it will be recorded as."""

    source: Path
    ref: anki_note_tooling.lib.media.MediaRef


@dataclass(kw_only=True, frozen=True)
class MissingMedia:
    """
    A file a line needs that is in none of the places it could be.

    Not fatal: the note it belongs to is in Anki whatever anki_note_tooling can find, so the line is
    still recorded and this is reported instead. What it costs is the media reference, so
    the line stays incomplete until someone gives it one.
    """

    line_text: str
    description: str
    searched: tuple[Path, ...]

    @property
    def expected(self) -> Path:
        """Where the file would be if it were anywhere — the first place looked."""
        return self.searched[0]

    def __str__(self) -> str:
        return f"{self.description} for {self.line_text!r} is missing: looked in " + ", ".join(
            str(path) for path in self.searched
        )


def planned_copy(path: Path, *, fs: anki_note_tooling.config.FsConfig) -> MediaCopy:
    """Where `path` will sit once it is inside the media root."""
    return MediaCopy(
        source=path, ref=fs.media.store(fs.managed_language_learning_files / path.name)
    )


def locate_media(
    candidates: Sequence[Path],
    *,
    fs: anki_note_tooling.config.FsConfig,
    line_text: str,
    description: str,
) -> MediaCopy | MissingMedia:
    """
    The first candidate that is there, planned as a copy — or a record that none was.

    Media a source keeps in its own directory has to be brought inside the media root
    before a line can refer to it, since a stored location is relative to that root. The
    copy keeps the file name, which is also what a rendered note refers to, so a line
    renders the same either side of the move.

    Planning the copy rather than doing it is what lets a command see every file it needs,
    and every file it cannot find, before it writes anything.
    """
    for path in candidates:
        if path.is_file():
            return planned_copy(path, fs=fs)
    return MissingMedia(line_text=line_text, description=description, searched=tuple(candidates))


def locate_optional_media(
    candidates: Sequence[Path], *, fs: anki_note_tooling.config.FsConfig
) -> MediaCopy | None:
    """
    The first candidate that is there, or None because the line has none.

    For media a line is not obliged to have: finding nothing says the line never had one,
    which is not a gap to report.
    """
    for path in candidates:
        if path.is_file():
            return planned_copy(path, fs=fs)
    return None


@dataclass(kw_only=True, frozen=True)
class PreparedLine:
    """
    One line to store, beside the notes it renders to.

    The notes are rendered from the source data rather than from the stored line, because
    a line the source could not give an image survives here and would not survive
    `StoredFlLine.completed()`. Every note already in Anki has to be matchable, including
    those made from a line with no image.
    """

    line: anki_note_tooling.lib.types.FlLine
    notes: tuple[anki_note_tooling.notes.note.RenderedNote, ...]
    #: The media to bring into the store before this line's rows are of any use.
    media: tuple[MediaCopy, ...] = ()
    #: What could not be found for it, which is reported rather than raised.
    missing_media: tuple[MissingMedia, ...] = ()


def copy_media(copies: Iterable[MediaCopy], *, fs: anki_note_tooling.config.FsConfig) -> int:
    """
    Copies each planned file into the media store, and says how many were new.

    A file already there under the same name is left alone when it matches and refuses the
    copy when it does not: the store is flat, as Anki's media directory is, so two
    different files wanting one name is a clash worth stopping for rather than an overwrite
    to make quietly.
    """
    fs.managed_language_learning_files.mkdir(parents=True, exist_ok=True)
    copied = 0
    for planned in copies:
        destination = fs.media.locate(planned.ref)
        if destination.exists():
            if not filecmp.cmp(planned.source, destination, shallow=False):
                raise BackfillError(
                    f"{destination} already holds a different file; "
                    f"{planned.source} cannot be stored under that name"
                )
            continue
        shutil.copy(planned.source, destination)
        copied += 1
    return copied


def note_text_field(recipe: anki_note_tooling.notes.config.Recipe) -> str:
    """
    The Anki field `recipe` writes the rendered line into.

    Derived from the recipe rather than assumed to be the first field, so a model holding
    its text somewhere other than position 0 — or a deck holding two models — is matched
    on the right value.

    >>> recipe = anki_note_tooling.notes.config.Recipe(
    ...     name="phrase", deck="d", model="m", for_each="line", active="$kanji",
    ...     fields={"Text": "$note_text", "Romanji": "$romanji"},
    ... )
    >>> note_text_field(recipe)
    'Text'
    """
    fields = [name for name, template in recipe.fields.items() if template == NOTE_TEXT_TEMPLATE]
    if len(fields) != 1:
        raise BackfillError(
            f"recipe {recipe.name!r} must write {NOTE_TEXT_TEMPLATE} to exactly one field "
            f"for its notes to be identified in Anki, but writes it to {len(fields)}"
        )
    return fields[0]


def anki_note_ids_by_text_by_recipe(
    col: anki.collection.Collection,
    *,
    source_name: str,
    note_config: anki_note_tooling.notes.config.NoteConfig,
) -> dict[str, dict[str, anki.notes.NoteId]]:
    """
    For each recipe the source generates, its deck's note ids keyed by the rendered line.

    The outer key is the recipe rather than the deck: two recipes may share a deck while
    writing their text to different fields, and the recipe is what a rendered note names.
    """
    return {
        recipe.name: anki_note_tooling.anki_lib.queries.get_note_ids_by_field(
            col, deck=recipe.deck, field=note_text_field(recipe)
        )
        for recipe in note_config.recipes_for(source_name)
    }


def _anki_note_record(
    note: anki_note_tooling.notes.note.RenderedNote,
    *,
    source_id: int,
    anki_note_id: anki.notes.NoteId,
    added: datetime.datetime,
    volume_scale_percentage: int | None,
) -> dict[str, Any]:
    """One `managed_anki_note` row for a note matched in the collection."""
    return {
        "source_id": source_id,
        "deck_name": note.deck,
        "note": note.to_record(),
        "misc": {
            "internal_note_ref": note.ref,
            "volume_scale_percentage": volume_scale_percentage,
        },
        # The collection's own timestamp: anki_note_tooling is recording when the note was added, not
        # when it got around to noticing.
        "inserted_at": added,
        "anki_note_id": anki_note_id,
    }


def inserted_source_ids(records: list[dict[str, Any]]) -> list[int]:
    """
    The lines `records` cover, each once.

    >>> inserted_source_ids([{"source_id": 7}, {"source_id": 7}, {"source_id": 9}])
    [7, 9]
    """
    return list(dict.fromkeys(record["source_id"] for record in records))


@dataclass(kw_only=True, frozen=True)
class RecipeMatch:
    """
    How much of one recipe's output for one line was found in Anki.

    The three states are what the rule below turns on, so they are named rather than
    recomputed from the counts at each place a decision is made.
    """

    recipe: str
    matched: tuple[tuple[anki_note_tooling.notes.note.RenderedNote, anki.notes.NoteId], ...]
    missing: tuple[anki_note_tooling.notes.note.RenderedNote, ...]

    @property
    def is_complete(self) -> bool:
        """Every note this recipe renders for the line is in Anki."""
        return bool(self.matched) and not self.missing

    @property
    def is_absent(self) -> bool:
        """None of them is — the recipe was never run for this line, or not kept."""
        return not self.matched

    @property
    def is_partial(self) -> bool:
        """Some but not all, which is the state nothing here can explain."""
        return bool(self.matched) and bool(self.missing)


@dataclass(kw_only=True, frozen=True)
class AbsentRecipe:
    """A recipe whose notes for one line are wholly absent from the collection."""

    source_id: int
    line_text: str
    recipe: str
    note_count: int

    def __str__(self) -> str:
        return (
            f"line {self.source_id} ({self.line_text!r}): recipe {self.recipe!r} has no "
            f"notes in Anki, so its {self.note_count} were not recorded"
        )


@dataclass(kw_only=True, frozen=True)
class MatchResult:
    """What a run inserted, and what it found nothing for."""

    records: list[dict[str, Any]]
    absent: tuple[AbsentRecipe, ...]


def _match_recipe(
    recipe: str,
    notes: list[anki_note_tooling.notes.note.RenderedNote],
    *,
    anki_note_ids_by_text: dict[str, anki.notes.NoteId],
) -> RecipeMatch:
    """Which of `notes` are in the collection, keeping the ones that are not."""
    matched = []
    missing = []
    for note in notes:
        anki_note_id = anki_note_ids_by_text.get(note.text)
        if anki_note_id is None:
            missing.append(note)
        else:
            matched.append((note, anki_note_id))
    return RecipeMatch(recipe=recipe, matched=tuple(matched), missing=tuple(missing))


def _check_line_is_explainable(
    matches: list[RecipeMatch], *, source_id: int, line_text: str
) -> None:
    """
    Raises unless what Anki holds for one line is a state a previous run could have left.

    A recipe is either wholly in the collection or wholly out of it: a run that added
    notes added all of a recipe's, and one that never ran added none. Anything between the
    two means the notes drifted after they were added, and matching on rendered text
    cannot tell which Anki note a changed line belongs to — so the run stops rather than
    recording a line against some of its notes and silently orphaning the rest.

    At least one recipe has to be complete, or there is no evidence this line was ever
    inserted and the backfill would be inventing a record rather than recovering one.
    """
    partial = [match for match in matches if match.is_partial]
    if partial:
        raise BackfillError(
            f"line {source_id} ({line_text!r}) is partly in Anki: "
            + "; ".join(
                f"recipe {match.recipe!r} has {len(match.matched)} of "
                f"{len(match.matched) + len(match.missing)} notes, missing "
                + ", ".join(repr(note.text) for note in match.missing)
                for match in partial
            )
        )
    if not any(match.is_complete for match in matches):
        raise BackfillError(
            f"line {source_id} ({line_text!r}) has no recipe whose notes are all in Anki, "
            f"so there is nothing to record it against (recipes: "
            + ", ".join(repr(match.recipe) for match in matches)
            + ")"
        )


def _notes_by_recipe(
    notes: Iterable[anki_note_tooling.notes.note.RenderedNote],
) -> dict[str, list[anki_note_tooling.notes.note.RenderedNote]]:
    """The line's notes grouped by the recipe that produced them, in render order."""
    grouped: dict[str, list[anki_note_tooling.notes.note.RenderedNote]] = {}
    for note in notes:
        grouped.setdefault(note.recipe, []).append(note)
    return grouped


def match_and_insert_existing_lines_and_notes(
    conn: sqlalchemy.Connection,
    prepared: list[PreparedLine],
    *,
    col: anki.collection.Collection,
    config: anki_note_tooling.config.Config,
    source_name: str,
    volume_scale_percentage: int | None = None,
) -> MatchResult:
    """
    Inserts the `fl_line` and `managed_anki_note` rows for lines whose notes are in Anki.

    A line is recorded when at least one of its recipes has all its notes in the
    collection and every other recipe has all or none of its own — see
    `_check_line_is_explainable` for why anything else stops the run. Recipes with none
    are reported: their notes were never added, so there is nothing to record for them.

    Every line is checked before anything is inserted, so a run that stops leaves the
    transaction untouched by note rows. Committing is the caller's, which is what makes a
    failure here leave nothing behind.

    `volume_scale_percentage` describes how the audio was scaled when these notes were
    added, which is a fact about the run being recorded rather than about the source.
    """
    anki_note_ids_by_text_by_recipe_name = anki_note_ids_by_text_by_recipe(
        col, source_name=source_name, note_config=config.notes
    )

    source_type_id = anki_note_tooling.data.queries.get_or_create_source_type(conn, source_name)
    stored_lines = anki_note_tooling.data.inserts.insert_lines(
        conn, [p.line for p in prepared], source_type_id=source_type_id
    )

    records: list[dict[str, Any]] = []
    absent: list[AbsentRecipe] = []
    for stored_line, prepared_line in zip(stored_lines, prepared, strict=True):
        matches = []
        for recipe, notes in _notes_by_recipe(prepared_line.notes).items():
            anki_note_ids_by_text = anki_note_ids_by_text_by_recipe_name.get(recipe)
            if anki_note_ids_by_text is None:
                raise BackfillError(
                    f"note {notes[0].ref!r} names recipe {recipe!r}, which "
                    f"{source_name!r} does not generate"
                )
            matches.append(
                _match_recipe(recipe, notes, anki_note_ids_by_text=anki_note_ids_by_text)
            )

        _check_line_is_explainable(
            matches, source_id=stored_line.source_id, line_text=stored_line.text
        )

        for match in matches:
            if match.is_absent:
                absent.append(
                    AbsentRecipe(
                        source_id=stored_line.source_id,
                        line_text=stored_line.text,
                        recipe=match.recipe,
                        note_count=len(match.missing),
                    )
                )
                continue
            for note, anki_note_id in match.matched:
                added = anki_note_tooling.anki_lib.utils.get_note_added(col, anki_note_id)
                records.append(
                    _anki_note_record(
                        note,
                        source_id=stored_line.source_id,
                        anki_note_id=anki_note_id,
                        added=datetime.datetime.fromtimestamp(added, tz=datetime.timezone.utc),
                        volume_scale_percentage=volume_scale_percentage,
                    )
                )

    anki_note_tooling.data.inserts.insert_anki_notes(conn, records=records)
    return MatchResult(records=records, absent=tuple(absent))
