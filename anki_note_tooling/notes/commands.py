"""
CLI for inspecting the source and recipe configuration.

A config-driven pipeline is only usable if you can see what it will do before it touches
Anki, which is what these two commands are for.
"""

from pathlib import Path

import typer

import anki_note_tooling.anki_lib.utils
import anki_note_tooling.config
import anki_note_tooling.data.queries
import anki_note_tooling.data.tables
import anki_note_tooling.make_anki_cards
import anki_note_tooling.notes.config

app = typer.Typer()


def _check_against_anki(
    config: anki_note_tooling.config.Config, notes: anki_note_tooling.notes.config.NoteConfig
) -> tuple[list[str], list[str]]:
    """
    Checks every recipe's deck, model and field names against the real collection.

    Returns (problems, warnings). Not being able to open the collection is a warning:
    validating the config itself should still work on a machine where Anki is not set up.
    """
    problems: list[str] = []
    try:
        col = anki_note_tooling.anki_lib.utils.get_collection(config)
    except Exception as e:
        return problems, [f"could not open the Anki collection, skipping deck/field checks: {e}"]

    try:
        for recipe in notes.recipes.values():
            if col.decks.id_for_name(recipe.deck) is None:
                problems.append(f"recipe {recipe.name!r}: no deck named {recipe.deck!r}")

            model = col.models.by_name(recipe.model)
            if model is None:
                problems.append(f"recipe {recipe.name!r}: no model named {recipe.model!r}")
                continue

            available = [field["name"] for field in model["flds"]]
            unknown = sorted(set(recipe.fields) - set(available))
            if unknown:
                problems.append(
                    f"recipe {recipe.name!r}: model {recipe.model!r} has no field(s) "
                    f"{', '.join(unknown)} (it has: {', '.join(available)})"
                )
    finally:
        col.close()

    return problems, []


def _check_against_database(
    config: anki_note_tooling.config.Config, notes: anki_note_tooling.notes.config.NoteConfig
) -> list[str]:
    """
    Cross-checks source names against `fl_source_type`, as warnings only.

    A definition may legitimately be written before the first import, and lines may be
    imported before anyone decides what notes they should make, so neither direction is an
    error.
    """
    engine = anki_note_tooling.data.tables.get_engine(config.database)
    with engine.connect() as conn:
        known = anki_note_tooling.data.queries.get_source_type_names(conn)

    warnings = [
        f"source {name!r} has lines in the database but no sources/{name}.toml"
        for name in anki_note_tooling.notes.config.unconfigured_source_names(notes, known)
    ]
    warnings.extend(
        f"source {name!r} is defined but has no fl_source_type row yet"
        for name in sorted(set(notes.sources) - set(known))
    )
    return warnings


@app.command()
def validate(
    config_dir: Path = anki_note_tooling.config.cli_option, check_anki: bool = typer.Option(True)
):
    """
    Check every source against every recipe it binds to, and both against Anki.

    Recipes are shared, so this is a check over the source x recipe cross product rather
    than over each file in isolation.
    """
    # Loaded without the binding check, so an unsatisfiable binding is reported below
    # alongside everything else rather than raising out of `anki_note_tooling.config.load`.
    config = anki_note_tooling.config.load(config_dir, check_notes=False)
    notes = config.notes

    print(f"{len(notes.sources)} source(s), {len(notes.recipes)} recipe(s)")

    problems = anki_note_tooling.notes.config.check_bindings(notes)
    warnings = _check_against_database(config, notes)

    if check_anki:
        anki_problems, anki_warnings = _check_against_anki(config, notes)
        problems.extend(anki_problems)
        warnings.extend(anki_warnings)

    for source in notes.sources.values():
        recipes = ", ".join(source.notes) or "(none)"
        images = "no image picker" if source.images is None else f"images {source.images}"
        print(f"  {source.name}: slots [{', '.join(source.slots)}] -> {recipes}; {images}")

    for warning in warnings:
        print(f"warning: {warning}")
    for problem in problems:
        print(f"error: {problem}")

    if problems:
        raise typer.Exit(code=1)
    print("OK")


@app.command()
def render(source_id: int, config_dir: Path = anki_note_tooling.config.cli_option):
    """Print the exact fields a line would write, without touching Anki."""
    config = anki_note_tooling.config.load(config_dir)
    engine = anki_note_tooling.data.tables.get_engine(config.database)

    with engine.connect() as conn:
        line = anki_note_tooling.data.queries.get_single_complete_line(conn, source_id)

    if line is None:
        raise typer.BadParameter(
            f"no complete line with id {source_id} "
            "(it may be missing, or missing its annotation, image or audio)"
        )

    print(f"source {line.source_type}, line {line.source_id}")
    print(f"annotated: {line.annotated_text}")

    for note in anki_note_tooling.make_anki_cards.create_notes(
        line, note_config=config.notes, media=config.fs.media
    ):
        print()
        print(f"  ref   {note.ref}")
        print(f"  deck  {note.deck}")
        print(f"  model {note.model}")
        print(f"  tags  {', '.join(note.tags)}")
        for name, value in note.fields.items():
            print(f"    {name} = {value}")
