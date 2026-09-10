"""
The version a database records, and the guard that reads it back.

The stamp is the one part of schema versioning that cannot be added later: an unstamped
database in the wild has to be identified by inspecting its tables. So what is pinned here is
that a database is stamped however it came to exist, and that a database this code does not
understand is refused rather than read.
"""

import sys

import pytest
import sqlalchemy
import typer.testing

import anki_note_tooling.cli
import anki_note_tooling.config
import anki_note_tooling.data.tables as tables


def make_config_dir(path):
    """A config directory with a database, made the way a new user makes one."""
    typer.testing.CliRunner().invoke(
        anki_note_tooling.cli.build_app(),
        ["config", "init", str(path)],
        catch_exceptions=False,
    )
    return anki_note_tooling.config.load(path)


def ledger_rows(database):
    engine = tables.get_engine(database, check_schema=False)
    with engine.connect() as conn:
        return conn.execute(sqlalchemy.select(tables.schema_migration)).fetchall()


def test_a_new_database_records_the_version_it_was_created_at(tmp_path):
    """Provenance and current version in one row: what created it, and what schema that is."""
    config = make_config_dir(tmp_path / "fresh")

    (row,) = ledger_rows(config.database)

    assert row.version == tables.SCHEMA_VERSION
    assert row.name == "initial schema"
    assert row.applied_by == tables.package_version()


def test_creating_the_database_twice_records_one_version(tmp_path):
    """
    `create_db` is idempotent and gets re-run against a live database.

    A second row would claim a change that never happened, and since the current version is
    `MAX(version)` rather than a row count, the damage would be to the history rather than to
    the version — which is the kind of wrong that goes unnoticed.
    """
    destination = tmp_path / "fresh"
    config = make_config_dir(destination)

    tables.create_db(destination)

    assert len(ledger_rows(config.database)) == 1


def test_a_database_from_a_newer_release_is_refused(tmp_path):
    """
    Reading a schema this code predates would misread it, so it is refused rather than tried.

    The remedy is the opposite of the usual one — upgrade the tool, not the database — so the
    message has to say which way round it is.
    """
    config = make_config_dir(tmp_path / "fresh")
    engine = tables.get_engine(config.database, check_schema=False)
    with engine.connect() as conn:
        conn.execute(
            sqlalchemy.update(tables.schema_migration).values(version=tables.SCHEMA_VERSION + 1)
        )
        conn.commit()

    with pytest.raises(tables.SchemaVersionError) as excinfo:
        tables.get_engine(config.database)

    assert "Upgrade anki-note-tooling" in str(excinfo.value)


def test_a_path_with_no_database_names_the_command_that_makes_one(tmp_path):
    """
    SQLite creates a file for any path it is pointed at, so a typo in `sqlite_file` produces an
    empty database rather than an error. Without the guard the first symptom is `no such table:
    fl_line` from whichever query ran first, which names neither cause nor remedy.
    """
    config = make_config_dir(tmp_path / "fresh")
    database = config.database.model_copy(update={"sqlite_file": tmp_path / "nothing-here.db"})

    with pytest.raises(tables.SchemaVersionError) as excinfo:
        tables.get_engine(database)

    assert "tables create-db" in str(excinfo.value)


def test_a_database_predating_version_tracking_is_refused(tmp_path):
    """
    Tables but no ledger is a database from before this table existed — a different problem
    from an empty file, and told apart by whether `fl_line` is there.
    """
    config = make_config_dir(tmp_path / "fresh")
    engine = tables.get_engine(config.database, check_schema=False)
    with engine.connect() as conn:
        tables.schema_migration.drop(conn)
        conn.commit()

    with pytest.raises(tables.SchemaVersionError) as excinfo:
        tables.get_engine(config.database)

    assert "predates version tracking" in str(excinfo.value)


def test_schema_version_reports_a_database_the_guard_would_refuse(tmp_path):
    """
    This is the command reached for when something else has just refused to open the database,
    so it has to read one the guard would reject — otherwise it can only report the cases
    nobody needs it for.
    """
    destination = tmp_path / "fresh"
    config = make_config_dir(destination)
    engine = tables.get_engine(config.database, check_schema=False)
    with engine.connect() as conn:
        tables.schema_migration.drop(conn)
        conn.commit()

    result = typer.testing.CliRunner().invoke(
        anki_note_tooling.cli.build_app(),
        ["tables", "schema-version", "--config-dir", str(destination)],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "schema version 0" in result.stdout
    assert "No schema version recorded." in result.stdout


def test_the_cli_reports_a_schema_mismatch_as_a_message_and_a_failing_exit_status(
    tmp_path, monkeypatch, capsys
):
    """
    The entry point is what turns the exception into something a user can act on.

    Tested through `main` rather than through `CliRunner`, because `CliRunner` invokes the
    Typer app directly and never reaches the handler — so a test that went that way would pass
    with the handler deleted.
    """
    destination = tmp_path / "fresh"
    config = make_config_dir(destination)
    engine = tables.get_engine(config.database, check_schema=False)
    with engine.connect() as conn:
        conn.execute(
            sqlalchemy.update(tables.schema_migration).values(version=tables.SCHEMA_VERSION + 1)
        )
        conn.commit()

    monkeypatch.setattr(
        sys,
        "argv",
        # Any command that opens the database will do; `create-db` is the one that will not,
        # being the command that passes `check_schema=False`.
        ["anki-note-tooling", "sources", "validate", "--config-dir", str(destination)],
    )

    with pytest.raises(SystemExit) as excinfo:
        anki_note_tooling.cli.main()

    assert excinfo.value.code == 1
    assert "Upgrade anki-note-tooling" in capsys.readouterr().err
