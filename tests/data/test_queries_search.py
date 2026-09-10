"""
Query-level tests for the search filters, against a real SQLite database.

These run against real tables and real `misc` blobs rather than mocks, because the thing
most likely to break silently is the JSON path `json_extract` is handed: a mocked
connection would happily accept a path that matches nothing.
"""

from pathlib import Path

import pytest
import sqlalchemy

import anki_note_tooling.config
import anki_note_tooling.data.inserts
import anki_note_tooling.data.queries
import anki_note_tooling.data.tables
import anki_note_tooling.data.types
import anki_note_tooling.lib.types
import anki_note_tooling.notes.config

FILTERS = anki_note_tooling.data.types.FlLineSearchFilters
MISC_FIELD = anki_note_tooling.data.types.MiscSearchField


def _config(tmp_path: Path) -> anki_note_tooling.config.Config:
    return anki_note_tooling.config.Config(
        anki=anki_note_tooling.config.AnkiConfig(profile_dir=tmp_path),
        database=anki_note_tooling.config.DatabaseConfig(
            sqlite_file=tmp_path / "db.db", echo_queries_in_log=False
        ),
        fs=anki_note_tooling.config.FsConfig(
            files_path=tmp_path,
            managed_language_learning_files=tmp_path,
            tmp_files=tmp_path,
        ),
        web=anki_note_tooling.config.WebConfig(debug_server=False),
        notes=anki_note_tooling.notes.config.NoteConfig(sources={}, recipes={}),
    )


def _line(
    text: str,
    *,
    source_type: str = "podcast",
    language: str = "ja",
    character_name: str | None = None,
    filename: str | None = None,
    source_tag: str | None = None,
) -> anki_note_tooling.lib.types.FlLine:
    return anki_note_tooling.lib.types.FlLine(
        source_type=source_type,
        source_file=filename,
        language=language,
        text=text,
        annotated_text=None,
        audio=None,
        image_path=None,
        misc=anki_note_tooling.lib.types.FlMiscData(
            character_name=character_name, filename=filename, source_tag=source_tag
        ),
    )


@pytest.fixture
def conn(tmp_path):
    """
    A connection to a populated database: two sources with lines, one registered but empty.

    The empty source is what the search page's source checkboxes have to keep offering, so
    it is part of the fixture rather than a special case in one test.
    """
    config = _config(tmp_path)
    engine = sqlalchemy.create_engine(f"sqlite+pysqlite:///{config.database.sqlite_file}")

    with engine.connect() as connection:
        anki_note_tooling.data.tables.fl_source_type.create(connection)
        anki_note_tooling.data.tables.fl_line.create(connection)
        anki_note_tooling.data.tables.managed_anki_note.create(connection)

        podcast = anki_note_tooling.data.queries.get_or_create_source_type(connection, "podcast")
        drama = anki_note_tooling.data.queries.get_or_create_source_type(connection, "drama")
        anki_note_tooling.data.queries.get_or_create_source_type(connection, "notebook")

        anki_note_tooling.data.inserts.insert_lines(
            connection,
            [
                _line(
                    "好きです",
                    character_name="由紀",
                    filename="episode_12_yuki.csv",
                    source_tag="episode_12_yuki",
                ),
                _line(
                    "おはよう",
                    character_name="健二",
                    filename="episode_12_kenji.csv",
                    source_tag="episode_12_kenji",
                ),
            ],
            source_type_id=podcast,
        )
        anki_note_tooling.data.inserts.insert_lines(
            connection,
            [
                _line(
                    "我係陳生。",
                    source_type="drama",
                    language="zh-yue",
                    source_tag="drama-episode_04.csv",
                )
            ],
            source_type_id=drama,
        )
        connection.commit()

        yield connection


def _texts(conn, filters) -> list[str]:
    lines, _ = anki_note_tooling.data.queries.search_fl_lines(conn, filters)
    return [line.text for line in lines]


def test_no_filters_matches_everything(conn):
    assert len(_texts(conn, FILTERS())) == 3


def test_filter_by_text(conn):
    assert _texts(conn, FILTERS(text="おはよ")) == ["おはよう"]


def test_filter_by_single_source(conn):
    assert sorted(_texts(conn, FILTERS(source_types=("drama",)))) == ["我係陳生。"]


def test_filter_by_multiple_sources(conn):
    both = _texts(conn, FILTERS(source_types=("podcast", "drama")))
    assert len(both) == 3


def test_filter_by_source_with_no_lines(conn):
    """A registered but unimported source is a valid filter that simply matches nothing."""
    assert _texts(conn, FILTERS(source_types=("notebook",))) == []


def test_filter_by_language(conn):
    assert _texts(conn, FILTERS(language="zh-yue")) == ["我係陳生。"]


@pytest.mark.parametrize(
    "filters,expected",
    [
        (FILTERS(character_name="由紀"), ["好きです"]),
        # Substring, not prefix: the useful part of a filename is in the middle.
        (FILTERS(filename="kenji"), ["おはよう"]),
        (FILTERS(source_tag="drama-episode"), ["我係陳生。"]),
        # The drama line has no character_name, so it must not match on one even though
        # "陳" appears in its text.
        (FILTERS(character_name="陳"), []),
        (FILTERS(character_name="nobody"), []),
    ],
)
def test_filter_by_misc_field(conn, filters, expected):
    assert _texts(conn, filters) == expected


def test_filters_combine(conn):
    """Criteria intersect, so a source that cannot contain the character matches nothing."""
    assert _texts(conn, FILTERS(source_types=("podcast",), character_name="健二")) == ["おはよう"]
    assert _texts(conn, FILTERS(source_types=("drama",), character_name="健二")) == []


def test_exclude_inserted_is_applied_before_paging(conn):
    """
    Excluding inserted lines narrows the SQL, so a full page still holds a full page of rows.

    Filtering after the query instead would leave this page one row short.
    """
    conn.execute(
        anki_note_tooling.data.tables.managed_anki_note.insert(),
        [{"source_id": 1, "deck_name": "d", "note": {}, "misc": {}, "anki_note_id": 99}],
    )

    lines, has_next = anki_note_tooling.data.queries.search_fl_lines(
        conn, FILTERS(exclude_inserted=True), page=1, page_size=2
    )
    assert [line.text for line in lines] == ["おはよう", "我係陳生。"]
    assert has_next is False


def test_paging_reports_has_next(conn):
    first, has_next = anki_note_tooling.data.queries.search_fl_lines(
        conn, FILTERS(), page=1, page_size=2
    )
    assert len(first) == 2
    assert has_next is True

    second, has_next = anki_note_tooling.data.queries.search_fl_lines(
        conn, FILTERS(), page=2, page_size=2
    )
    assert len(second) == 1
    assert has_next is False


def test_source_type_line_counts_keeps_empty_sources(conn):
    assert anki_note_tooling.data.queries.get_source_type_line_counts(conn) == [
        ("drama", 1),
        ("notebook", 0),
        ("podcast", 2),
    ]


def test_source_type_names(conn):
    assert anki_note_tooling.data.queries.get_source_type_names(conn) == [
        "drama",
        "notebook",
        "podcast",
    ]


def test_misc_value_suggestions_are_distinct_and_sorted(conn):
    assert anki_note_tooling.data.queries.get_misc_value_suggestions(
        conn, field=MISC_FIELD.FILENAME
    ) == [
        "episode_12_kenji.csv",
        "episode_12_yuki.csv",
    ]


def test_misc_value_suggestions_narrow_by_prefix_and_source(conn):
    assert anki_note_tooling.data.queries.get_misc_value_suggestions(
        conn, field=MISC_FIELD.FILENAME, prefix="kenji"
    ) == ["episode_12_kenji.csv"]
    assert (
        anki_note_tooling.data.queries.get_misc_value_suggestions(
            conn, field=MISC_FIELD.CHARACTER_NAME, source_types=["drama"]
        )
        == []
    )
