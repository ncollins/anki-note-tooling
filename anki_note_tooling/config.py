import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

import typer
from pydantic import BaseModel, model_validator
from rich.pretty import pprint

import anki_note_tooling.lib.media
import anki_note_tooling.notes.config

app = typer.Typer()

_DEFAULT_CONFIG_PATH = Path.home() / ".config/anki-note-tooling/"

#: The example configuration, shipped inside the package so that `config init` works from an
#: installed wheel rather than only from a checkout.
EXAMPLE_CONFIG_DIR = Path(__file__).parent / "example_config"


class DatabaseConfig(BaseModel):
    sqlite_file: Path
    echo_queries_in_log: bool


class FsConfig(BaseModel):
    files_path: Path
    managed_language_learning_files: Path
    tmp_files: Path

    @model_validator(mode="after")
    def _directories_are_inside_files_path(self) -> "FsConfig":
        """
        Both working directories have to sit under `files_path`.

        Everything written into them is recorded as a location relative to `files_path`, so
        one placed outside it could be saved but never read back.
        """
        for name in ("managed_language_learning_files", "tmp_files"):
            directory: Path = getattr(self, name)
            if not directory.is_relative_to(self.files_path):
                raise ValueError(f"fs.{name} ({directory}) is not inside fs.files_path")
        return self

    @property
    def media(self) -> anki_note_tooling.lib.media.MediaStore:
        """The store every stored media location is written and resolved through."""
        return anki_note_tooling.lib.media.MediaStore(root=self.files_path)


class AnkiConfig(BaseModel):
    profile_dir: Path


class WebConfig(BaseModel):
    debug_server: bool


@dataclass(kw_only=True, frozen=True)
class Config:
    anki: AnkiConfig
    database: DatabaseConfig
    fs: FsConfig
    web: WebConfig
    # Sources and note recipes come from their own directories rather than config.toml:
    # they are one file per source and per recipe, so that adding either is a new file.
    notes: anki_note_tooling.notes.config.NoteConfig


def _resolve_database_config(raw: dict, config_dir: Path) -> DatabaseConfig:
    """
    Builds a `DatabaseConfig`, resolving a relative `sqlite_file` against `config_dir`.

    >>> _resolve_database_config(
    ...     {"sqlite_file": "db.db", "echo_queries_in_log": False}, Path("/home/x/.config/anki-note-tooling")
    ... ).sqlite_file
    PosixPath('/home/x/.config/anki-note-tooling/db.db')
    >>> _resolve_database_config(
    ...     {"sqlite_file": "/var/db.db", "echo_queries_in_log": False}, Path("/home/x/.config/anki-note-tooling")
    ... ).sqlite_file
    PosixPath('/var/db.db')
    """
    database = DatabaseConfig(**raw)
    if database.sqlite_file.is_absolute():
        return database
    return database.model_copy(update={"sqlite_file": config_dir / database.sqlite_file})


def load(config_dir: Path, *, check_notes: bool = True) -> Config:
    """
    Loads `config.toml` plus the source and recipe directories beside it.

    `check_notes=False` skips the source-to-recipe binding check, so that
    `anki_note_tooling sources validate` can *report* an unsatisfiable binding instead of dying of one.
    """
    config_file = config_dir / "config.toml"
    with config_file.open("rb") as f:
        raw = tomllib.load(f)
        return Config(
            anki=AnkiConfig(**raw["anki"]),
            database=_resolve_database_config(raw["database"], config_dir),
            fs=FsConfig(**raw["fs"]),
            web=WebConfig(**raw["web"]),
            notes=(
                anki_note_tooling.notes.config.load(config_dir)
                if check_notes
                else anki_note_tooling.notes.config.load_unchecked(config_dir)
            ),
        )


cli_option = typer.Option(default=_DEFAULT_CONFIG_PATH)


@app.command()
def validate(config_dir: Path = cli_option):
    config = load(config_dir)
    pprint(config)


@app.command()
def init(config_dir: Path = typer.Argument(..., help="Directory to create the config in.")):
    """
    Creates a config directory from the shipped example, with an empty database beside it.

    Copies `config.toml`, `sources/` and `note_recipes/`, then creates the tables in the
    database `config.toml` names — which is `db.db` in the new directory, since the example
    gives a relative path.

    The paths inside the copied `config.toml` are placeholders and have to be edited before
    anything else will work; `config validate` is the check that they parse.

    Refuses to write into a directory that already has anything in it, so it can never take
    someone's existing config with it. That is also why it has no `--live-run`: there is no
    state to lose, and creating the directory is the whole point of the command.
    """
    if config_dir.exists() and any(config_dir.iterdir()):
        raise typer.BadParameter(
            f"{config_dir} already exists and is not empty; "
            "point `config init` at a new directory, or empty this one first"
        )

    shutil.copytree(EXAMPLE_CONFIG_DIR, config_dir, dirs_exist_ok=True)
    copied = sorted(
        path.relative_to(config_dir).as_posix() for path in config_dir.rglob("*") if path.is_file()
    )
    for name in copied:
        print(f"  wrote {name}")

    # Imported here rather than at module scope: `data.tables` needs `config` for
    # `DatabaseConfig` and `cli_option`, so importing it the other way round at the top would
    # be circular.
    import anki_note_tooling.data.tables

    engine = anki_note_tooling.data.tables.create_db(config_dir)
    print(f"  created {engine.url.database}")

    print()
    print(f"Config directory ready at {config_dir}.")
    print("Next: edit the paths in config.toml (every one of them is a placeholder), then run")
    print(f"  anki-note-tooling config validate --config-dir {config_dir}")
    print(f"  anki-note-tooling sources validate --config-dir {config_dir}")
