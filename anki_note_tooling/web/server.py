import sys
from pathlib import Path
from typing import Any, Callable

import cheroot.wsgi
import flask
import typer

import anki_note_tooling.config
import anki_note_tooling.data.queries
import anki_note_tooling.data.tables
import anki_note_tooling.web.routing
import anki_note_tooling.web.views.fl_line
import anki_note_tooling.web.views.misc

app = typer.Typer()

ROUTES: list[tuple[str, list[str], Callable[..., Any]]] = [
    ("/", ["GET"], anki_note_tooling.web.views.misc.index),
    ("/audio_file/<path:file_sub_path>", ["GET"], anki_note_tooling.web.views.misc.audio_file),
    ("/search", ["GET"], anki_note_tooling.web.views.misc.search),
    ("/search/suggest", ["GET"], anki_note_tooling.web.views.misc.search_suggest),
    ("/inserted", ["GET"], anki_note_tooling.web.views.misc.inserted),
    (
        "/fl_line_details/<int:fl_source_line_id>",
        ["GET"],
        anki_note_tooling.web.views.fl_line.details,
    ),
    (
        "/toggle_starred/<int:fl_source_line_id>",
        ["GET"],
        anki_note_tooling.web.views.fl_line.toggle_starred,
    ),
    (
        "/fl_line_alternative_image_selection/<int:fl_source_line_id>",
        ["GET"],
        anki_note_tooling.web.views.fl_line.alternative_image_selection,
    ),
    (
        "/fl_line_form_image_override",
        ["GET"],
        anki_note_tooling.web.views.fl_line.form_image_override,
    ),
    (
        "/fl_line_text_editable/<int:fl_source_line_id>",
        ["GET"],
        anki_note_tooling.web.views.fl_line.text_editable,
    ),
    (
        "/fl_line_text_uneditable/<int:fl_source_line_id>",
        ["GET"],
        anki_note_tooling.web.views.fl_line.text_uneditable,
    ),
    (
        "/fl_line_validate_annotation/<int:fl_source_line_id>",
        ["POST"],
        anki_note_tooling.web.views.fl_line.validate_annotation,
    ),
    (
        "/post/update_line/<int:fl_source_line_id>",
        ["POST"],
        anki_note_tooling.web.views.fl_line.update_line,
    ),
    (
        "/fl_line_upload_tmp_file/<int:fl_source_line_id>",
        ["POST"],
        anki_note_tooling.web.views.fl_line.upload_tmp_file,
    ),
    ("/notes_to_update", ["GET"], anki_note_tooling.web.views.misc.notes_to_update),
    ("/update_anki_notes", ["POST"], anki_note_tooling.web.views.misc.update_anki_notes),
    ("/notes_to_insert", ["GET"], anki_note_tooling.web.views.misc.notes_to_insert),
    ("/insert_anki_notes", ["POST"], anki_note_tooling.web.views.misc.insert_anki_notes),
]


def create_app(config_dir: Path) -> flask.Flask:
    config = anki_note_tooling.config.load(config_dir=config_dir)
    db = anki_note_tooling.data.tables.get_engine(config.database)
    flask_app = flask.Flask(__name__)
    # Autoescaping has to be explicitly enabled, as this project uses `.html.j2` as
    # the template file extension which is not recognized by default. It must be set
    # before the first render, since Flask builds `jinja_env` from these options once
    # and caches it. Autoescaping ensures that markup in lines and notes (e.g. <br>, <img>)
    # will be displayed to the user.
    flask_app.jinja_options = {**flask_app.jinja_options, "autoescape": True}

    # Registered once rather than caught at each of the six views that look a line up by id:
    # an id that is not there is a 404, and that is true wherever it is asked for.
    flask_app.register_error_handler(
        anki_note_tooling.data.queries.RecordNotFoundError, lambda e: (str(e), 404)
    )

    anki_note_tooling.web.routing.add_routes(
        config=config, db=db, flask_app=flask_app, routes=ROUTES
    )

    return flask_app


@app.command()
def start(config_dir: Path = anki_note_tooling.config.cli_option):
    config = anki_note_tooling.config.load(config_dir)
    flask_app = create_app(config_dir)

    try:
        if config.web.debug_server:
            flask_app.run(debug=True)
        else:
            server = cheroot.wsgi.Server(("127.0.0.1", 5000), flask_app)
            server.start()
    except KeyboardInterrupt:
        # TODO: could try using server.stop() to exit more cleanly
        sys.exit(0)


if __name__ == "__main__":
    start()
