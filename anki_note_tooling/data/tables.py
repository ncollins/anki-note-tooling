import importlib.metadata
from pathlib import Path

import typer
from sqlalchemy import (
    Boolean,
    Column,
    Connection,
    DateTime,
    Engine,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.types import JSON

import anki_note_tooling.config

app = typer.Typer()

#: The schema this code expects. A plain counter, bumped only when the shape of the tables
#: below changes, and the value every future upgrade keys on.
#:
#: Deliberately not the package version: most releases will not touch the schema, and comparing
#: two release numbers cannot say whether a given one did. The package version is recorded
#: alongside it as provenance — see `schema_migration`.
SCHEMA_VERSION = 1

# A registry handing out stable integer ids for source names, nothing more: `fl_line` and
# `image_pool_entry` both reference it, so renaming a source is one UPDATE rather than a
# rewrite of every line. What a source *is* — its slots, recipes and image selection —
# lives in `<config_dir>/sources/*.toml`.
fl_source_type = Table(
    "fl_source_type",
    MetaData(),
    Column("id", Integer, primary_key=True),
    Column("name", String, unique=True),
)

fl_line = Table(
    "fl_line",
    MetaData(),
    Column("id", Integer, primary_key=True),
    Column("source_type_id", Integer, ForeignKey(fl_source_type.c.id), nullable=False),
    Column("source_file", String),
    Column("language", String),
    Column("text", String),
    Column("annotated_text", String),
    Column("image", String),
    # none_as_null is load-bearing: without it SQLAlchemy stores Python None as the JSON text
    # 'null', which is not SQL NULL, and every audio-less line would look "complete".
    Column("audio", JSON(none_as_null=True)),
    Column("up_to_date_anki_notes", Boolean, default=False, nullable=False, index=True),
    Column("misc", JSON(none_as_null=True)),
    Column("created_at", DateTime, server_default=func.now()),
    Column("modified_at", DateTime, server_default=func.now(), onupdate=func.now()),
)

fl_starred = Table(
    "fl_starred",
    MetaData(),
    Column("id", Integer, primary_key=True),
    Column("source_id", Integer, ForeignKey(fl_line.c.id), nullable=False, unique=True),
    Column("created_at", DateTime, server_default=func.now()),
    Column("modified_at", DateTime, server_default=func.now(), server_onupdate=func.now()),  # type: ignore
)

managed_anki_note = Table(
    "managed_anki_note",
    MetaData(),
    Column("id", Integer, primary_key=True),
    Column("source_id", Integer, ForeignKey(fl_line.c.id), nullable=False, index=True),
    Column("deck_name", String),
    Column("note", JSON),
    Column("misc", JSON),
    Column("anki_note_id", Integer, unique=True),
    Column("inserted_at", DateTime, nullable=True),
    Column("created_at", DateTime, server_default=func.now()),
    Column("modified_at", DateTime, server_default=func.now(), server_onupdate=func.now()),  # type: ignore
)

# One pool of candidate images per source, in the shape every source needs — (pool, key,
# image, label, metadata) — with `[source.images]` in the source definition deciding how it
# is queried.
image_pool_entry = Table(
    "image_pool_entry",
    MetaData(),
    Column("id", Integer, primary_key=True),
    Column("source_type_id", Integer, ForeignKey(fl_source_type.c.id), nullable=False),
    # Nullable: a pool with no per-line key is offered whole.
    Column("key", String),
    # Relative to config.fs.files_path, like fl_line.image.
    Column("image", String, nullable=False),
    # What the picker shows under the thumbnail.
    Column("label", String),
    # NOT NULL: nothing here distinguishes "absent" from "empty", so `refine_by` stays a
    # plain dict lookup with no NULL branch.
    Column("metadata", JSON, nullable=False, default=dict),
    Column("created_at", DateTime, server_default=func.now()),
    Column("modified_at", DateTime, server_default=func.now(), onupdate=func.now()),
    Index("ix_image_pool_entry_lookup", "source_type_id", "key"),
    # Makes re-running an importer idempotent. Keying on (key, image) rather than image alone still lets one image appear under two
    # keys — a group shot listed under both characters.
    UniqueConstraint("source_type_id", "key", "image"),
    # SQLite treats NULLs as distinct from each other, so the constraint above does not
    # cover a keyless pool — re-importing a flat directory would duplicate every row. This
    # partial index closes that gap without giving up NULL as the way to say "no key".
    Index(
        "ix_image_pool_entry_keyless",
        "source_type_id",
        "image",
        unique=True,
        sqlite_where=text("key IS NULL"),
    ),
)


# The database's own record of which schema it is on. Append-only: one row per change applied,
# so the table answers when each ran and what ran it, not only where the database stands now.
#
# A database created from scratch gets a single row naming the version it was born at rather
# than a replay of the history it never lived through, so `MAX(version)` — not the row count —
# is the current version.
schema_migration = Table(
    "schema_migration",
    MetaData(),
    # Not autoincrement: the value is the schema version this row brought the database to,
    # chosen by whatever applied it rather than handed out by SQLite.
    Column("version", Integer, primary_key=True, autoincrement=False),
    Column("name", String, nullable=False),
    # The release that applied it. Provenance for a bug report — never compared against
    # anything, because that is `version`'s job.
    Column("applied_by", String, nullable=False),
    Column("applied_at", DateTime, server_default=func.now()),
)


class SchemaVersionError(Exception):
    """The database on disk is not the schema this code was written against."""


def package_version() -> str:
    """
    The installed release of this package, or `"unknown"` if it cannot be determined.

    Only ever written to `schema_migration.applied_by`, so a missing distribution — a source
    tree on `sys.path` with nothing installed — is worth recording as unknown rather than
    worth failing a `create_db` over.
    """
    try:
        return importlib.metadata.version("anki-note-tooling")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def schema_version(conn: Connection) -> int:
    """
    The schema version of the database behind `conn`.

    Zero when `schema_migration` is absent, which is both a database written before this table
    existed and a path SQLite has just created as an empty file. The two are told apart by
    whether any other table is there; `_require_current_schema` does that, since only the
    error message differs.
    """
    if not inspect(conn).has_table(schema_migration.name):
        return 0
    return conn.execute(select(func.max(schema_migration.c.version))).scalar() or 0


def _stamp_initial_version(conn: Connection) -> None:
    """
    Records the version a newly created database was born at, if it has no record yet.

    Guarded on the ledger being empty because `create_db` is idempotent and gets re-run: a
    second row would claim a change that never happened.
    """
    if schema_version(conn) != 0:
        return
    conn.execute(
        schema_migration.insert().values(
            version=SCHEMA_VERSION, name="initial schema", applied_by=package_version()
        )
    )


def _require_current_schema(conn: Connection, db_file: Path) -> None:
    found = schema_version(conn)
    if found == SCHEMA_VERSION:
        return

    if found > SCHEMA_VERSION:
        raise SchemaVersionError(
            f"{db_file} is on schema version {found}, but this anki-note-tooling "
            f"({package_version()}) only understands version {SCHEMA_VERSION}. "
            "Upgrade anki-note-tooling; this version would misread the database."
        )

    if found == 0:
        # A file SQLite created on the spot from a mistyped or not-yet-initialised path is by
        # far the likeliest way to get here, so the message leads with that rather than with
        # the migration story.
        if not inspect(conn).has_table(fl_line.name):
            raise SchemaVersionError(
                f"{db_file} has no tables. Run `anki-note-tooling tables create-db` to create "
                "them, or check that `sqlite_file` in config.toml points where you meant."
            )
        raise SchemaVersionError(
            f"{db_file} has tables but no schema version, so it predates version tracking. "
            "See `anki-note-tooling tables schema-version`."
        )

    raise SchemaVersionError(
        f"{db_file} is on schema version {found}; this anki-note-tooling "
        f"({package_version()}) expects version {SCHEMA_VERSION}. "
        "Run `anki-note-tooling tables upgrade`."
    )


@app.command()
def create_db(config_dir: Path = anki_note_tooling.config.cli_option) -> Engine:
    config = anki_note_tooling.config.load(config_dir)
    # The schema check is exactly what this command exists to satisfy, so it cannot be subject
    # to it: on a first run there is nothing there to check.
    engine = get_engine(config.database, check_schema=False)

    with engine.connect() as conn:
        fl_source_type.create(conn, checkfirst=True)
        fl_line.create(conn, checkfirst=True)
        fl_starred.create(conn, checkfirst=True)
        managed_anki_note.create(conn, checkfirst=True)
        image_pool_entry.create(conn, checkfirst=True)
        schema_migration.create(conn, checkfirst=True)
        _stamp_initial_version(conn)
        conn.commit()

    return engine


# Named explicitly: the function cannot be `schema_version`, which is the helper above, and
# Typer would otherwise derive the command name from whatever the function is called.
@app.command("schema-version")
def schema_version_command(config_dir: Path = anki_note_tooling.config.cli_option):
    """
    Reports the schema version of the configured database, and how it got there.

    Opens the database without the version check the other commands go through: this is the
    command you reach for when that check has just refused something, so it has to be able to
    read a database it would not otherwise open.
    """
    config = anki_note_tooling.config.load(config_dir, check_notes=False)
    engine = get_engine(config.database, check_schema=False)

    with engine.connect() as conn:
        found = schema_version(conn)
        print(f"{engine.url.database}")
        print(f"  database:   schema version {found}")
        print(
            f"  this build: schema version {SCHEMA_VERSION} (anki-note-tooling {package_version()})"
        )

        if found == 0:
            print()
            print("  No schema version recorded.")
            return

        rows = conn.execute(
            select(schema_migration).order_by(schema_migration.c.version)
        ).fetchall()

    print()
    for row in rows:
        print(f"  {row.version}  {row.name}  (applied by {row.applied_by} at {row.applied_at})")


def get_engine(
    database: anki_note_tooling.config.DatabaseConfig, *, check_schema: bool = True
) -> Engine:
    """
    The engine for the configured SQLite file, checked against `SCHEMA_VERSION` by default.

    The check is on by default so that every command reading or writing through this function
    gets it without opting in. `create_db` and `tables schema-version` pass
    `check_schema=False`, being respectively what creates the version and what reports it.
    """
    db_file = database.sqlite_file.absolute()
    engine = create_engine(f"sqlite+pysqlite:///{db_file}", echo=database.echo_queries_in_log)

    if check_schema:
        with engine.connect() as conn:
            _require_current_schema(conn, db_file)

    return engine
