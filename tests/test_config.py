"""The shipped example configuration, checked against the schema it claims to demonstrate."""

import typer.testing

import anki_note_tooling.cli
import anki_note_tooling.config
import anki_note_tooling.data.tables

EXAMPLE_CONFIG_DIR = anki_note_tooling.config.EXAMPLE_CONFIG_DIR


def test_the_shipped_example_config_loads():
    """
    `example-config/config.toml` is what a new user copies, so it has to parse.

    None of the paths in it exist — they are placeholders — which is exactly why this can be
    checked here: nothing in `load` touches the filesystem beyond reading the TOML. What it
    does catch is the example drifting from the schema, by a section being added, renamed or
    made required without the example following.
    """
    config = anki_note_tooling.config.load(EXAMPLE_CONFIG_DIR)

    assert config.anki.profile_dir.name == "User 1"
    assert config.web.debug_server is False
    # `fs` has a validator requiring both working directories to sit inside `files_path`; a
    # loaded config is proof the example satisfies it.
    assert config.fs.managed_language_learning_files.is_relative_to(config.fs.files_path)
    assert config.fs.tmp_files.is_relative_to(config.fs.files_path)


def test_the_example_database_path_is_relative_to_the_config_directory():
    """
    The example sets `sqlite_file = "db.db"` to show that a relative path lands beside the
    config rather than in the working directory, which is the behaviour a first run depends on.
    """
    config = anki_note_tooling.config.load(EXAMPLE_CONFIG_DIR)

    assert config.database.sqlite_file == EXAMPLE_CONFIG_DIR / "db.db"


def test_config_init_writes_a_usable_config_directory(tmp_path):
    """
    What a new user's first command produces has to load without further edits.

    The paths inside are placeholders and stay wrong until edited, but everything structural —
    the sections, the sources, the recipes and the database — has to be right immediately.
    """
    destination = tmp_path / "fresh"

    typer.testing.CliRunner().invoke(
        anki_note_tooling.cli.build_app(),
        ["config", "init", str(destination)],
        catch_exceptions=False,
    )

    assert (destination / "db.db").is_file()
    config = anki_note_tooling.config.load(destination)
    assert sorted(config.notes.sources) == ["drama", "podcast"]
    assert sorted(config.notes.recipes) == ["cloze", "phrase"]
    # Stamped, so a later release can tell what it is looking at. `get_engine` refuses an
    # unstamped database, which would make this the one command whose output nothing can open.
    with anki_note_tooling.data.tables.get_engine(config.database).connect() as conn:
        assert anki_note_tooling.data.tables.schema_version(conn) == (
            anki_note_tooling.data.tables.SCHEMA_VERSION
        )


def test_config_init_refuses_to_write_into_an_occupied_directory(tmp_path):
    """Someone will point it at their real config directory sooner or later."""
    destination = tmp_path / "occupied"
    destination.mkdir()
    (destination / "config.toml").write_text("# mine\n")

    result = typer.testing.CliRunner().invoke(
        anki_note_tooling.cli.build_app(), ["config", "init", str(destination)]
    )

    assert result.exit_code != 0
    assert (destination / "config.toml").read_text() == "# mine\n"
