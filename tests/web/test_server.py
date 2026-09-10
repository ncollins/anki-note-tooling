import contextlib
from pathlib import Path
from unittest import mock

import flask
import pytest
import sqlalchemy

import anki_note_tooling.config
import anki_note_tooling.data.tables
import anki_note_tooling.data.types
import anki_note_tooling.lib.types
import anki_note_tooling.notes.config
import anki_note_tooling.web.server


def test_create_app():
    """
    Verifies that the web app can be created, in particular, that all routes
    can successfully be added to the app.
    """
    mock_config = mock.MagicMock()
    mock_engine = mock.create_autospec(sqlalchemy.Engine, instance=True)
    config_dir = Path("./some-config")

    with (
        mock.patch.object(
            anki_note_tooling.config, "load", autospec=True, return_value=mock_config
        ) as mock_load,
        mock.patch.object(
            anki_note_tooling.data.tables, "get_engine", autospec=True, return_value=mock_engine
        ) as mock_get_engine,
    ):
        flask_app = anki_note_tooling.web.server.create_app(config_dir)

    assert isinstance(flask_app, flask.Flask)
    mock_load.assert_called_once_with(config_dir=config_dir)
    mock_get_engine.assert_called_once_with(mock_config.database)


def test_search_no_query():
    """GET /search with no query renders blank search form."""
    mock_config = mock.MagicMock()
    mock_engine = mock.create_autospec(sqlalchemy.Engine, instance=True)
    config_dir = Path("./some-config")

    with (
        mock.patch.object(
            anki_note_tooling.config, "load", autospec=True, return_value=mock_config
        ),
        mock.patch.object(
            anki_note_tooling.data.tables, "get_engine", autospec=True, return_value=mock_engine
        ),
    ):
        flask_app = anki_note_tooling.web.server.create_app(config_dir)

    client = flask_app.test_client()
    response = client.get("/search")
    assert response.status_code == 200
    assert b'<form method="get" action="/search">' in response.data
    assert b"<table>" not in response.data


def test_search_with_query_and_pagination():
    """GET /search?query=foo&page=2 calls search_fl_lines with page=2 and reflects pagination state."""
    mock_config = mock.MagicMock()
    mock_engine = mock.create_autospec(sqlalchemy.Engine, instance=True)
    config_dir = Path("./some-config")

    mock_conn = mock.MagicMock()
    mock_engine.connect.return_value.__enter__ = mock.MagicMock(return_value=mock_conn)
    mock_engine.connect.return_value.__exit__ = mock.MagicMock(return_value=None)

    mock_config.fs.files_path = Path("/files")

    with (
        mock.patch.object(
            anki_note_tooling.config, "load", autospec=True, return_value=mock_config
        ),
        mock.patch.object(
            anki_note_tooling.data.tables, "get_engine", autospec=True, return_value=mock_engine
        ),
        mock.patch(
            "anki_note_tooling.data.queries.search_fl_lines",
            autospec=True,
            return_value=([], False),
        ) as mock_search,
        mock.patch(
            "anki_note_tooling.data.queries.get_insertions_with_dates",
            autospec=True,
            return_value=[],
        ),
    ):
        flask_app = anki_note_tooling.web.server.create_app(config_dir)
        client = flask_app.test_client()

        response = client.get("/search?query=hello&page=2&exclude_inserted=on")

        assert response.status_code == 200
        mock_search.assert_called_once_with(
            mock_conn,
            anki_note_tooling.data.types.FlLineSearchFilters(text="hello", exclude_inserted=True),
            page=2,
            page_size=100,
        )
        assert b"Query was: hello" in response.data


def test_search_exclude_inserted_persists_in_links():
    """exclude_inserted checkbox state is preserved in pagination links."""
    mock_config = mock.MagicMock()
    mock_engine = mock.create_autospec(sqlalchemy.Engine, instance=True)
    config_dir = Path("./some-config")

    mock_conn = mock.MagicMock()
    mock_engine.connect.return_value.__enter__ = mock.MagicMock(return_value=mock_conn)
    mock_engine.connect.return_value.__exit__ = mock.MagicMock(return_value=None)

    mock_config.fs.files_path = Path("/files")

    with (
        mock.patch.object(
            anki_note_tooling.config, "load", autospec=True, return_value=mock_config
        ),
        mock.patch.object(
            anki_note_tooling.data.tables, "get_engine", autospec=True, return_value=mock_engine
        ),
        mock.patch(
            "anki_note_tooling.data.queries.search_fl_lines",
            autospec=True,
            return_value=([], True),
        ),
        mock.patch(
            "anki_note_tooling.data.queries.get_insertions_with_dates",
            autospec=True,
            return_value=[],
        ),
    ):
        flask_app = anki_note_tooling.web.server.create_app(config_dir)
        client = flask_app.test_client()

        response = client.get("/search?query=hello&page=1&exclude_inserted=on")

        assert response.status_code == 200
        # exclude_inserted param is preserved in pagination links
        assert b"exclude_inserted=on" in response.data


def test_search_exclude_inserted_not_in_links_when_unchecked():
    """exclude_inserted param is absent from links when not set."""
    mock_config = mock.MagicMock()
    mock_engine = mock.create_autospec(sqlalchemy.Engine, instance=True)
    config_dir = Path("./some-config")

    mock_conn = mock.MagicMock()
    mock_engine.connect.return_value.__enter__ = mock.MagicMock(return_value=mock_conn)
    mock_engine.connect.return_value.__exit__ = mock.MagicMock(return_value=None)

    mock_config.fs.files_path = Path("/files")

    with (
        mock.patch.object(
            anki_note_tooling.config, "load", autospec=True, return_value=mock_config
        ),
        mock.patch.object(
            anki_note_tooling.data.tables, "get_engine", autospec=True, return_value=mock_engine
        ),
        mock.patch(
            "anki_note_tooling.data.queries.search_fl_lines",
            autospec=True,
            return_value=([], True),
        ),
        mock.patch(
            "anki_note_tooling.data.queries.get_insertions_with_dates",
            autospec=True,
            return_value=[],
        ),
    ):
        flask_app = anki_note_tooling.web.server.create_app(config_dir)
        client = flask_app.test_client()

        response = client.get("/search?query=hello&page=1")

        assert response.status_code == 200
        # exclude_inserted param should NOT appear in pagination links when unchecked
        assert b"exclude_inserted=" not in response.data


def test_search_page_defaults_to_1():
    """GET /search?query=foo (no page param) defaults to page 1."""
    mock_config = mock.MagicMock()
    mock_engine = mock.create_autospec(sqlalchemy.Engine, instance=True)
    config_dir = Path("./some-config")

    mock_conn = mock.MagicMock()
    mock_engine.connect.return_value.__enter__ = mock.MagicMock(return_value=mock_conn)
    mock_engine.connect.return_value.__exit__ = mock.MagicMock(return_value=None)

    mock_config.fs.files_path = Path("/files")

    with (
        mock.patch.object(
            anki_note_tooling.config, "load", autospec=True, return_value=mock_config
        ),
        mock.patch.object(
            anki_note_tooling.data.tables, "get_engine", autospec=True, return_value=mock_engine
        ),
        mock.patch(
            "anki_note_tooling.data.queries.search_fl_lines",
            autospec=True,
            return_value=([], False),
        ) as mock_search,
        mock.patch(
            "anki_note_tooling.data.queries.get_insertions_with_dates",
            autospec=True,
            return_value=[],
        ),
    ):
        flask_app = anki_note_tooling.web.server.create_app(config_dir)
        client = flask_app.test_client()

        response = client.get("/search?query=hello")

        assert response.status_code == 200
        mock_search.assert_called_once_with(
            mock_conn,
            anki_note_tooling.data.types.FlLineSearchFilters(text="hello"),
            page=1,
            page_size=100,
        )


def test_templates_escape_markup():
    """
    Templates escape what they interpolate, in body and attribute contexts alike.

    The query is reflected into both, and line text legitimately contains `<br>` (the
    imported dialogue lines), so a page that renders either as markup is a page that has
    lost autoescaping.
    """
    mock_config = mock.MagicMock()
    mock_engine = mock.create_autospec(sqlalchemy.Engine, instance=True)
    config_dir = Path("./some-config")

    mock_conn = mock.MagicMock()
    mock_engine.connect.return_value.__enter__ = mock.MagicMock(return_value=mock_conn)
    mock_engine.connect.return_value.__exit__ = mock.MagicMock(return_value=None)

    mock_config.fs.files_path = Path("/files")

    line = anki_note_tooling.data.types.StoredFlLine(
        source_id=1,
        source_type="podcast",
        source_file="episode_12.csv",
        language="ja",
        text="A「お名前は？」<br>B「トムです。」",
        annotated_text=None,
        audio=None,
        image_path=None,
        misc=anki_note_tooling.lib.types.FlMiscData(),
        up_to_date_anki_notes=False,
    )

    with (
        mock.patch.object(
            anki_note_tooling.config, "load", autospec=True, return_value=mock_config
        ),
        mock.patch.object(
            anki_note_tooling.data.tables, "get_engine", autospec=True, return_value=mock_engine
        ),
        mock.patch(
            "anki_note_tooling.data.queries.search_fl_lines",
            autospec=True,
            return_value=([line], False),
        ),
        mock.patch(
            "anki_note_tooling.data.queries.get_insertions_with_dates",
            autospec=True,
            return_value=[],
        ),
    ):
        flask_app = anki_note_tooling.web.server.create_app(config_dir)
        client = flask_app.test_client()

        response = client.get('/search?query="><script>alert(1)</script>')

    assert response.status_code == 200
    assert b"<script>alert(1)</script>" not in response.data
    assert b"&lt;script&gt;alert(1)&lt;/script&gt;" in response.data
    assert "A「お名前は？」&lt;br&gt;B「トムです。」".encode() in response.data


@contextlib.contextmanager
def _search_client(
    *,
    results: list[anki_note_tooling.data.types.StoredFlLine] | None = None,
    has_next: bool = False,
    source_type_counts: list[tuple[str, int]] | None = None,
    suggestions: list[str] | None = None,
):
    """
    A test client for the search page with every query it makes stubbed out.

    Yields the client alongside the `search_fl_lines` mock, since most of these tests are
    about what the filters parse to rather than what comes back.
    """
    mock_config = mock.MagicMock()
    mock_config.fs.files_path = Path("/files")

    mock_engine = mock.create_autospec(sqlalchemy.Engine, instance=True)
    mock_conn = mock.MagicMock()
    mock_engine.connect.return_value.__enter__ = mock.MagicMock(return_value=mock_conn)
    mock_engine.connect.return_value.__exit__ = mock.MagicMock(return_value=None)

    with (
        mock.patch.object(
            anki_note_tooling.config, "load", autospec=True, return_value=mock_config
        ),
        mock.patch.object(
            anki_note_tooling.data.tables, "get_engine", autospec=True, return_value=mock_engine
        ),
        mock.patch(
            "anki_note_tooling.data.queries.search_fl_lines",
            autospec=True,
            return_value=(results or [], has_next),
        ) as mock_search,
        mock.patch(
            "anki_note_tooling.data.queries.get_insertions_with_dates",
            autospec=True,
            return_value=[],
        ),
        mock.patch(
            "anki_note_tooling.data.queries.get_source_type_line_counts",
            autospec=True,
            return_value=source_type_counts if source_type_counts is not None else [("podcast", 2)],
        ),
        mock.patch(
            "anki_note_tooling.data.queries.get_misc_value_suggestions",
            autospec=True,
            return_value=suggestions or [],
        ) as mock_suggest,
    ):
        flask_app = anki_note_tooling.web.server.create_app(Path("./some-config"))
        yield flask_app.test_client(), mock_search, mock_suggest


def test_search_parses_every_filter_from_the_query_string():
    """The whole form round-trips through the query string into one filters object."""
    with _search_client() as (client, mock_search, _):
        response = client.get(
            "/search?query=hello&source=podcast&source=drama&language=ja"
            "&character_name=%E5%81%A5%E4%BA%8C"
            "&filename=episode_12_kenji&source_tag=episode_12&exclude_inserted=on"
        )

    assert response.status_code == 200
    filters = mock_search.call_args.args[1]
    assert filters == anki_note_tooling.data.types.FlLineSearchFilters(
        text="hello",
        source_types=("podcast", "drama"),
        language="ja",
        character_name="健二",
        filename="episode_12_kenji",
        source_tag="episode_12",
        exclude_inserted=True,
    )


def test_search_runs_with_no_text_query():
    """A metadata-only or source-only search is a search; text is not required."""
    with _search_client() as (client, mock_search, _):
        response = client.get("/search?source=podcast&character_name=%E5%81%A5%E4%BA%8C")

    assert response.status_code == 200
    mock_search.assert_called_once()
    assert mock_search.call_args.args[1] == anki_note_tooling.data.types.FlLineSearchFilters(
        source_types=("podcast",), character_name="健二"
    )


def test_search_with_no_criteria_does_not_query():
    """
    An untouched form shows the form only.

    `exclude_inserted` alone is a modifier rather than a criterion: acting on it would page
    through every line in the database.
    """
    with _search_client() as (client, mock_search, _):
        assert client.get("/search").status_code == 200
        assert client.get("/search?exclude_inserted=on").status_code == 200

    mock_search.assert_not_called()


def test_search_offers_every_registered_source_with_counts():
    """A registered source with no lines still gets a checkbox, showing zero."""
    with _search_client(source_type_counts=[("notebook", 0), ("podcast", 46889)]) as (client, _, _):
        response = client.get("/search")

    assert b'value="notebook"' in response.data
    assert b"notebook (0)" in response.data
    assert "podcast (46,889)".encode() in response.data


def test_search_pagination_preserves_every_filter():
    """
    Pagination links carry the whole query string, not just the text query.

    Rebuilding them from a hand-written list of parameters is what silently drops a filter
    added later.
    """
    with _search_client(has_next=True) as (client, _, _):
        response = client.get(
            "/search?query=hello&source=podcast&character_name=%E5%81%A5%E4%BA%8C&page=1"
        )

    assert response.status_code == 200
    body = response.data.decode()
    next_link = next(line for line in body.splitlines() if "Next" in line)
    assert "source=podcast" in next_link
    assert "character_name=%E5%81%A5%E4%BA%8C" in next_link
    assert "page=2" in next_link


def test_search_suggest_returns_a_datalist():
    with _search_client(suggestions=["episode_03_scene_C.csv"]) as (client, _, mock_suggest):
        response = client.get("/search/suggest?field=filename&filename=kenji&source=podcast")

    assert response.status_code == 200
    assert b'id="filename-suggestions"' in response.data
    assert b'value="episode_03_scene_C.csv"' in response.data
    assert (
        mock_suggest.call_args.kwargs["field"]
        is anki_note_tooling.data.types.MiscSearchField.FILENAME
    )
    assert mock_suggest.call_args.kwargs["prefix"] == "kenji"
    assert mock_suggest.call_args.kwargs["source_types"] == ["podcast"]


def test_search_suggest_rejects_an_unknown_field():
    """
    The field names a JSON path, so anything not in the enum is a 404 rather than a query.
    """
    with _search_client() as (client, _, mock_suggest):
        assert client.get("/search/suggest?field=english_line").status_code == 404
        assert client.get("/search/suggest?field=%27%29+or+1%3D1--").status_code == 404

    mock_suggest.assert_not_called()


#: The three-slot Japanese shape every annotation test below is written against.
ANNOTATION_SOURCE = anki_note_tooling.notes.config.Source(
    name="podcast", language="ja", slots=("kanji", "kana", "romanji")
)


def _annotated_line(annotated_text: str | None) -> anki_note_tooling.data.types.StoredFlLine:
    return anki_note_tooling.data.types.StoredFlLine(
        source_id=7,
        source_type="podcast",
        source_file="episode_12_yuki.csv",
        language="ja",
        text="好きです",
        annotated_text=annotated_text,
        audio=None,
        image_path=None,
        misc=anki_note_tooling.lib.types.FlMiscData(),
        up_to_date_anki_notes=False,
    )


@contextlib.contextmanager
def _client_for(line: anki_note_tooling.data.types.StoredFlLine, *, source=ANNOTATION_SOURCE):
    """
    A test client serving one line, with the queries the details page makes stubbed out.

    `notes.sources` is a real dict rather than a mock attribute, so a source the config
    does not define resolves to None the way it does in production.
    """
    mock_config = mock.MagicMock()
    mock_config.fs.files_path = Path("/files")
    mock_config.notes.sources = {source.name: source} if source is not None else {}

    mock_engine = mock.create_autospec(sqlalchemy.Engine, instance=True)
    mock_conn = mock.MagicMock()
    mock_engine.connect.return_value.__enter__ = mock.MagicMock(return_value=mock_conn)
    mock_engine.connect.return_value.__exit__ = mock.MagicMock(return_value=None)

    with (
        mock.patch.object(
            anki_note_tooling.config, "load", autospec=True, return_value=mock_config
        ),
        mock.patch.object(
            anki_note_tooling.data.tables, "get_engine", autospec=True, return_value=mock_engine
        ),
        mock.patch(
            "anki_note_tooling.data.queries.get_single_fl_line", autospec=True, return_value=line
        ),
        mock.patch(
            "anki_note_tooling.data.queries.get_is_starred", autospec=True, return_value=False
        ),
        mock.patch(
            "anki_note_tooling.data.queries.get_stored_anki_notes", autospec=True, return_value=[]
        ),
        mock.patch("anki_note_tooling.data.queries.mark_not_up_to_date_anki_notes", autospec=True),
        mock.patch("anki_note_tooling.data.inserts.update_fl_line", autospec=True) as mock_update,
    ):
        yield (
            anki_note_tooling.web.server.create_app(Path("./some-config")).test_client(),
            mock_update,
        )


def test_update_line_rejects_bad_annotation_without_losing_it():
    """
    A malformed annotation comes back in an open editor with the error beside it.

    The whole point of the rejection path: the user's typing survives, and nothing is
    written.
    """
    with _client_for(_annotated_line(None)) as (client, mock_update):
        response = client.post(
            "/post/update_line/7",
            data={"annotated": "[[好::す]]きです", "update-form-image-file": ""},
        )

    assert response.status_code == 400
    body = response.data.decode()
    assert "<textarea" in body
    assert "[[好::す]]きです" in body
    assert "has 2 slot(s), but this source declares 3: [kanji, kana, romanji]" in body
    mock_update.assert_not_called()


def test_update_line_rejection_keeps_the_media_that_was_picked():
    """
    An image chosen in the same submit is still selected on the page that comes back, so
    one corrected save applies the whole change rather than half of it.
    """
    with _client_for(_annotated_line(None)) as (client, _):
        response = client.post(
            "/post/update_line/7",
            data={
                "annotated": "[[好::す]]きです",
                "update-form-image-file": "tmp/picked-image.png",
                "update-form-audio-file": "tmp/picked-audio.mp3",
            },
        )

    body = response.data.decode()
    assert 'value="tmp/picked-image.png"' in body
    assert 'value="tmp/picked-audio.mp3"' in body


@pytest.mark.parametrize("value", ["/etc/passwd", "../../etc/passwd", "images/../../etc/passwd"])
def test_update_line_refuses_a_media_location_outside_the_root(value):
    """
    A posted location that escapes the media root is a bad request, not a 500.

    These fields are filled in by the picker and the upload view, so a value like this is a
    malformed request rather than something the user could correct on the page.
    """
    with _client_for(_annotated_line(None)) as (client, mock_update):
        response = client.post(
            "/post/update_line/7",
            data={"annotated": "", "update-form-image-file": value},
        )

    assert response.status_code == 400
    mock_update.assert_not_called()


def test_update_line_accepts_a_good_annotation():
    """The happy path still redirects and writes, unchanged by the rejection branch."""
    with _client_for(_annotated_line(None)) as (client, mock_update):
        response = client.post("/post/update_line/7", data={"annotated": "[[好::す::su]]きです"})

    assert response.status_code == 302
    assert response.headers["Location"] == "/fl_line_details/7"
    mock_update.assert_called_once()
    assert mock_update.call_args.kwargs["changes"]["annotated_text"] == "[[好::す::su]]きです"


def test_validate_annotation_reports_a_problem_and_disables_saving():
    with _client_for(_annotated_line(None)) as (client, _):
        response = client.post(
            "/fl_line_validate_annotation/7", data={"annotated": "[[好::す]]きです"}
        )

    assert response.status_code == 200
    body = response.data.decode()
    assert "has 2 slot(s), but this source declares 3: [kanji, kana, romanji]" in body
    assert "disabled" in body


def test_validate_annotation_passes_a_good_annotation():
    with _client_for(_annotated_line(None)) as (client, _):
        response = client.post(
            "/fl_line_validate_annotation/7", data={"annotated": "[[好::す::su]]きです"}
        )

    body = response.data.decode()
    assert "disabled" not in body
    assert '<p id="annotation-error" class="text-danger"></p>' in body


def test_validate_annotation_accepts_the_stored_text_unchanged():
    """
    Text equal to what is stored is valid even when it would not parse today.

    `update_line` only validates an annotation it is about to write, so a line stored
    under an earlier `slots` definition must not have its save button disabled — that
    would block editing the line's image and audio too.
    """
    with _client_for(_annotated_line("[[好::す]]きです")) as (client, _):
        response = client.post(
            "/fl_line_validate_annotation/7", data={"annotated": "[[好::す]]きです"}
        )

    assert "disabled" not in response.data.decode()
