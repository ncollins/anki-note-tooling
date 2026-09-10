import sys

import typer

import anki_note_tooling.config
import anki_note_tooling.csv_loader
import anki_note_tooling.data.tables
import anki_note_tooling.images.pool
import anki_note_tooling.make_anki_cards
import anki_note_tooling.notes.commands
import anki_note_tooling.web.server


def build_app() -> typer.Typer:
    """
    Builds the typer.Typer CLI application.
    >>> isinstance(build_app(), typer.Typer)  # confirm that the app builds with no exceptions
    True
    """
    app = typer.Typer()

    app.add_typer(anki_note_tooling.config.app, name="config")
    app.add_typer(anki_note_tooling.csv_loader.app, name="csv")
    app.add_typer(anki_note_tooling.images.pool.app, name="images")
    app.add_typer(anki_note_tooling.make_anki_cards.app, name="make-anki-cards")
    app.add_typer(anki_note_tooling.notes.commands.app, name="sources")
    app.add_typer(anki_note_tooling.data.tables.app, name="tables")
    app.add_typer(anki_note_tooling.web.server.app, name="web-server")

    return app


def main():
    """
    The console entry point, which turns a schema mismatch into a message rather than a crash.

    `SchemaVersionError` is raised from the data layer, which the web server also calls, so it
    cannot be a `typer` exception. It is nonetheless an ordinary thing for a user to hit — a
    database from another release, or a `sqlite_file` pointing somewhere unexpected — and its
    message already says what to do about it. A traceback would bury that.

    Only this exception is caught. Anything else is a bug and keeps its traceback.
    """
    app = build_app()
    try:
        app()
    except anki_note_tooling.data.tables.SchemaVersionError as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
