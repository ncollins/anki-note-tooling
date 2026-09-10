"""
Choosing which images a line's picker offers, from the declarative `[source.images]` block.

Three concerns are kept apart. Acquisition — scraping a game wiki — is genuinely
source-specific and stays in the importers. Storage is one `image_pool_entry` table shared
by every source. Selection is this module: `match` narrows the pool to entries whose `key`
equals a line variable, and `refine_by` then keeps only entries agreeing with the line's
current image on one metadata key.
"""

import string
from pathlib import Path
from typing import Iterable, Mapping

import sqlalchemy
import typer

import anki_note_tooling.config
import anki_note_tooling.data.inserts
import anki_note_tooling.data.queries
import anki_note_tooling.data.tables
import anki_note_tooling.data.types
import anki_note_tooling.lib.media
import anki_note_tooling.notes.config

app = typer.Typer()

#: File types the directory importer will pick up.
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})


def _resolve_match(match: str, line_variables: Mapping[str, str | None]) -> str | None:
    """
    The value `match` refers to, or None when the line does not carry it.

    >>> _resolve_match("$character_name", {"character_name": "はなこ"})
    'はなこ'
    >>> _resolve_match("$character_name", {"character_name": None}) is None
    True
    """
    resolved = string.Template(match).safe_substitute(
        {k: v for k, v in line_variables.items() if v}
    )
    return resolved if resolved and "$" not in resolved else None


def refine(
    entries: list[anki_note_tooling.data.types.ImagePoolEntry],
    *,
    refine_by: str | None,
    current_image: anki_note_tooling.lib.media.MediaRef | None,
) -> list[anki_note_tooling.data.types.ImagePoolEntry]:
    """
    Narrows `entries` to those agreeing with the current image on the `refine_by` metadata key.

    Skipped when the current image is not in the pool at all, or carries no such key —
    both of which mean there is nothing to agree with.

    >>> def entry(image, number=None):
    ...     return anki_note_tooling.data.types.ImagePoolEntry(
    ...         image=anki_note_tooling.lib.media.MediaRef.parse(image),
    ...         metadata={} if number is None else {"number": number})
    >>> ref = anki_note_tooling.lib.media.MediaRef.parse
    >>> pool = [entry("a.png", 1), entry("b.png", 2), entry("c.png")]
    >>> [str(e.image) for e in refine(pool, refine_by="number", current_image=ref("b.png"))]
    ['b.png']
    >>> [str(e.image) for e in refine(pool, refine_by="number", current_image=ref("c.png"))]
    ['a.png', 'b.png', 'c.png']
    >>> [str(e.image) for e in refine(pool, refine_by=None, current_image=ref("b.png"))]
    ['a.png', 'b.png', 'c.png']
    """
    if refine_by is None or current_image is None:
        return entries

    by_path = {entry.image: entry for entry in entries}
    current = by_path.get(current_image)
    if current is None or refine_by not in current.metadata:
        return entries

    wanted = current.metadata[refine_by]
    return [entry for entry in entries if entry.metadata.get(refine_by) == wanted]


def select(
    conn: sqlalchemy.Connection,
    *,
    source: anki_note_tooling.notes.config.Source,
    source_type_id: int,
    line_variables: Mapping[str, str | None],
    current_image: anki_note_tooling.lib.media.MediaRef | None,
) -> list[anki_note_tooling.data.types.ImagePoolEntry]:
    """
    The images this line's picker should offer.

    A source with no `[source.images]` block offers none, and the details page hides the
    button rather than raising. Falling back to the whole pool happens when the `match`
    *variable* is absent — every `drama` line has a null `character_name`, so that
    branch is forced by the data — but not when it resolves to a value matching nothing:
    a generic NPC with no portraits should show an empty picker, not all 860 images.
    """
    if source.images is None:
        return []

    key = None
    if source.images.match is not None:
        key = _resolve_match(source.images.match, line_variables)

    entries = anki_note_tooling.data.queries.get_image_pool_entries(
        conn, source_type_id=source_type_id, key=key
    )
    return refine(entries, refine_by=source.images.refine_by, current_image=current_image)


def entries_from_directory(
    directory: Path, *, media: anki_note_tooling.lib.media.MediaStore
) -> list[anki_note_tooling.data.types.ImagePoolEntry]:
    """
    Reads a directory of images into pool entries, convention first.

    A subdirectory name becomes the entry's `key` (`images/Hanako/*.png` gives
    `key="Hanako"`); images sitting directly in `directory` get no key, which is the
    whole-pool case.

    `directory` is resolved first, so that a relative path typed at the CLI still yields the
    absolute paths `MediaStore.store` requires.
    """
    directory = directory.resolve()
    entries = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        relative_to_root = path.relative_to(directory)
        key = relative_to_root.parts[0] if len(relative_to_root.parts) > 1 else None
        entries.append(
            anki_note_tooling.data.types.ImagePoolEntry(
                image=media.store(path),
                key=key,
                label=key or path.stem,
                metadata={},
            )
        )
    return entries


def describe(entries: Iterable[anki_note_tooling.data.types.ImagePoolEntry]) -> str:
    """A one-line-per-entry rendering, for the `--live-run`-less path of the importer."""
    return "\n".join(f"{entry.key or '(no key)'}\t{entry.image}" for entry in entries)


@app.command("import")
def import_images(
    source: str = typer.Option(..., help="The source name these images belong to."),
    directory: Path = typer.Option(..., "--dir", help="Directory of images to import."),
    config_dir: Path = anki_note_tooling.config.cli_option,
    live_run: bool = typer.Option(False),
):
    """
    Populate a source's image pool from a directory, without writing any Python.

    Convention first: a subdirectory name becomes the entry's key
    (`images/Hanako/*.png` gives `key="Hanako"`), and images sitting directly in the
    directory get no key. Re-running is a no-op, not a duplicate of every row.
    """
    config = anki_note_tooling.config.load(config_dir)
    if source not in config.notes.sources:
        raise typer.BadParameter(
            f"no sources/{source}.toml; define the source before importing images for it"
        )

    entries = entries_from_directory(directory, media=config.fs.media)
    if not entries:
        raise typer.BadParameter(f"no images found under {directory}")

    engine = anki_note_tooling.data.tables.get_engine(config.database)
    if not live_run:
        print(f"Would import {len(entries)} image(s) for {source!r}:")
        print(describe(entries))
        return

    with engine.connect() as conn:
        source_type_id = anki_note_tooling.data.queries.get_or_create_source_type(conn, source)
        before = len(
            anki_note_tooling.data.queries.get_image_pool_entries(
                conn, source_type_id=source_type_id
            )
        )
        anki_note_tooling.data.inserts.insert_image_pool_entries(
            conn, source_type_id=source_type_id, entries=entries
        )
        after = len(
            anki_note_tooling.data.queries.get_image_pool_entries(
                conn, source_type_id=source_type_id
            )
        )
        conn.commit()

    print(f"Imported {after - before} new image(s) for {source!r} ({len(entries)} seen).")


@app.command("list")
def list_images(
    source: str = typer.Option(...),
    config_dir: Path = anki_note_tooling.config.cli_option,
):
    """Show a source's image pool."""
    config = anki_note_tooling.config.load(config_dir)
    engine = anki_note_tooling.data.tables.get_engine(config.database)
    with engine.connect() as conn:
        source_type_id = anki_note_tooling.data.queries.get_or_create_source_type(conn, source)
        entries = anki_note_tooling.data.queries.get_image_pool_entries(
            conn, source_type_id=source_type_id
        )
    print(f"{len(entries)} image(s) for {source!r}")
    print(describe(entries))
