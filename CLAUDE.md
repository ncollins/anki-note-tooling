# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

anki-note-tooling builds Anki notes from source lines. It takes lines, normalizes them into a
shared intermediate representation, stores them in a local SQLite database, and exposes a small
Flask + htmx web UI for reviewing and editing them before generating notes.

Nothing here knows about any particular body of content, any particular deck, or any particular
language. Lines arrive through `anki-note-tooling csv load`, which reads a CSV whose headers are
the fields of a line, or from a package that depends on this one and supplies its own importers.
**Anything specific to one source of content belongs in such a package, not here.**

Which notes a line produces is **configuration, not code**: sources and note recipes are TOML
files under the config dir, and [anki_note_tooling/notes/](anki_note_tooling/notes/) renders one
against the other. [anki_note_tooling/example_config/](anki_note_tooling/example_config/) is a
complete working config directory — a commented `config.toml` plus `sources/` and
`note_recipes/`. It lives **inside the package** so that `config init` can copy it from an
installed wheel, and `anki_note_tooling.config.EXAMPLE_CONFIG_DIR` is the one way to find it.
`tests/test_config.py` loads it, so a new or renamed config section has to be reflected there or
the suite fails; `tests/test_example_config_output.py` pins the notes it renders to, which are
what the README quotes.

## Commands

This project is managed with **`uv`**; install it from <https://docs.astral.sh/uv/> if it is not
already on your PATH.

**Always run Python tools via `uv run`** — never call `python`, `python3`, `pytest`, `ruff`,
`mypy`, `ty`, or any other Python tool directly. This ensures the correct virtual environment and
dependencies are used.

Common targets are in the [Makefile](Makefile):

```
uv run pytest --doctest-modules .        # make test — run the full test suite (incl. doctests)
uv run pytest tests/web/test_server.py::test_create_app   # run a single test
uv run pytest --doctest-modules --cov=anki_note_tooling .            # make test-coverage
uv run ruff check --line-length 100 --select I,N,F401 --fix && uv run ruff format --line-length 100  # make lint-and-format
uv run ruff check --line-length 100 --select I,N,F401 && uv run ruff format --line-length 100 --check  # make lint-and-format-check
uv run mypy --ignore-missing-imports --check-untyped-defs .  # make mypy-typecheck
uv run ty check --python-version 3.12 --ignore unused-type-ignore-comment .  # make ty-typecheck
```

`lint-and-format` and its `-check` counterpart run the same rule set (`I,N,F401`), the first
fixing what it can and the second only reporting, so the two can never disagree. Linting runs
before formatting in both, because `ruff check --fix` rewrites code — reordering imports,
dropping unused ones — and what it leaves can itself need reformatting. Formatting has to come
last or `lint-and-format` can leave work that `lint-and-format-check` then rejects.

**After any changes, run `make check`** to confirm there were no regressions. This runs
`lint-and-format-check`, both typecheckers, and tests — consider a change done only after
`make check` passes.

The CLI entry point is `anki-note-tooling` (see `[project.scripts]` in
[pyproject.toml](pyproject.toml)); run `anki-note-tooling --help` to see subcommands.

Note: `pytest`'s `addopts` in `pyproject.toml` includes `--doctest-modules`, so doctests inside
`anki_note_tooling/` are collected and run as part of the normal test suite alongside `tests/`.

## Architecture

### CLI composition

[anki_note_tooling/cli.py](anki_note_tooling/cli.py) `build_app()` is the map of the whole
application: it assembles one Typer sub-app per subsystem (`config`, `csv`, `images`,
`make-anki-cards`, `sources`, `tables`, `web-server`). Each module referenced there exposes its
own `app = typer.Typer()` — that's the pattern to follow when adding a new subcommand group.

`anki-note-tooling config init <dir>` is how a new install gets a config directory: it copies the
packaged example and creates the database.

`anki-note-tooling sources validate` is the first thing to run after editing any source or recipe
TOML: it checks the whole source x recipe cross product, and checks decks, models and field names
against the real Anki collection.

Every command that writes takes a `--live-run` option defaulting to `False`; without it the
command prints the steps it *would* take and writes nothing. That is `csv load`,
`images import` and `make-anki-cards make-staging-notes`. The two exceptions are
`tables create-db`, which is idempotent (`checkfirst=True`) and whose whole purpose is the
write, and `config init`, which creates a directory and refuses to touch one that already has
anything in it. `web-server start` runs a server, so the question does not arise. Everything
else — `config validate`, `images list`, `sources validate`, `sources render`,
`tables schema-version` — only reads.

Adding a command that writes means adding the flag, and rendering the whole batch *before*
the gate so the dry run can show what the live run would do.

### Config

[anki_note_tooling/config.py](anki_note_tooling/config.py) loads `config.toml` from a `config_dir`
(default `~/.config/anki-note-tooling/`) into a frozen `Config` dataclass composed of per-area
pydantic/dataclass sections (`anki`, `database`, `fs`, `web`). Most CLI commands and web views take
`config_dir`/`config` as an explicit argument rather than reading global state.

A package building on this one adds its own sections by reading the same file separately, rather
than by extending `Config` — that is what keeps a required section from appearing here for one
consumer's benefit.

`Config.notes` is loaded from two directories beside `config.toml` — `sources/*.toml` and
`note_recipes/*.toml` — by
[anki_note_tooling/notes/config.py](anki_note_tooling/notes/config.py); see below.

### Data layer

[anki_note_tooling/data/](anki_note_tooling/data/) uses SQLAlchemy **Core**, not the ORM:
`tables.py` defines plain `Table`/`Column` objects against a SQLite DB (engine created via
`get_engine(config.database)`), and `queries.py`/`inserts.py` are functions that take an explicit
`sqlalchemy.Connection` and build/execute `select`/`insert` statements against those tables.
`types.py` holds the pydantic models representing flashcard lines at different completeness
stages: `StoredFlLine` (an `FlLine` plus the row's identity) and `CompleteFlLine` (a refinement of
it promising annotation, audio and image are all present). `StoredFlLine.completed()` is the one
place completeness is decided. Also here: `ImagePoolEntry`, `FlLineSearchFilters` and
`MiscSearchField`.

Source names are open, not an enum: `get_or_create_source_type(conn, name)` hands out ids on
demand, and `fl_source_type` is just an `(id, name)` registry. What a source *is* lives in its TOML.

A line's `language` is likewise a plain string, taken from the source definition. Which languages
exist is not this package's decision; the search page builds its language filter from
`get_language_counts`, i.e. from what has actually been imported.

`RecordNotFoundError` is raised for an id that has no row; the web layer registers one handler
that turns it into a 404.

#### Schema versions

The database records its own schema version in a `schema_migration` ledger — one row per change
applied, carrying the version it brought the database to, the release that applied it and when.
The current version is `MAX(version)`, and `SCHEMA_VERSION` in `tables.py` is what this code
expects.

**The counter is not the package version, and the two are not interchangeable.** `version` is a
plain integer bumped only when the tables change, and it is what upgrades key on; `applied_by`
records the release as provenance and is never compared against anything. Most releases will not
touch the schema, and comparing two release numbers cannot say whether a given one did.

`get_engine` checks the version on every open and raises `SchemaVersionError` on a mismatch,
which is why a new command needs to do nothing to be covered. The two callers that pass
`check_schema=False` are the two that cannot be subject to it: `create_db`, which is what
creates the version, and `tables schema-version`, which reports on databases the check has just
refused.

`create_db` still only ever creates tables (`checkfirst=True`) and never alters one. A database
made from scratch under a later version gets a single row naming the version it was born at
rather than a replay of history it never lived through.

The first release that changes the schema adds, and this is the contract the stamp exists for:

- `anki_note_tooling/data/migrations.py` — an ordered list of `(version, name, callable)`.
- `tables upgrade` — `--live-run` gated like every other writing command; copies the SQLite file
  to `<name>.pre-v<N>.bak` first, since the whole database is one file; applies each pending step
  in its own transaction and appends a ledger row per step.
- Each step written the way a one-off migration script is: explicit `BEGIN` on the raw DBAPI
  connection so the DDL is inside the rollback, dry run by default, and a Lossiness section in
  the docstring saying what `revert` cannot bring back.

### Shared domain types

[anki_note_tooling/lib/](anki_note_tooling/lib/) holds cross-cutting domain types independent of
any one data source: `types.py` has `FlMiscData`, `MediaType`, `AudioFile`, and `FlLine` — the
frozen pydantic model every importer normalizes its input into before it reaches the DB, and so
the one shape `anki_note_tooling.data.inserts.insert_lines` has to understand. `media.py` holds
`MediaRef`/`MediaStore`, the two forms a media location takes; `utils.py` holds `get_unique_path`.

### Line ingestion

Two ways in, and neither is source-specific.

[anki_note_tooling/csv_loader.py](anki_note_tooling/csv_loader.py) is `anki-note-tooling csv load`:
a CSV whose headers *are* the field names (`text`, `annotated_text`, `language`, `source_file`,
`audio`, `image`, plus the `FlMiscData` fields) becomes `fl_line` rows. An unrecognised header is
refused rather than ignored, since the failure it usually means — a misspelled column — is
otherwise silent. The whole file is read and every file it names located before anything is
written, so a bad row or a missing image stops the load with nothing inserted. Unlike a backfill,
a named file that is not there is an error: nothing is being recovered, so a line naming a file it
cannot have is a mistake in the file.

[anki_note_tooling/backfill.py](anki_note_tooling/backfill.py) records lines and notes that reached
Anki outside the database: it renders what each line produces, matches those notes to the ones
already in the collection by rendered text, and inserts a `managed_anki_note` row for each. The
match is exact and the command fails rather than guessing — a backfill that silently skipped what
it could not place would leave exactly the gap it exists to close. Its `locate_media` /
`planned_copy` / `copy_media` plan every file move before anything is written, which is what lets
`--live-run` mean something.

A package with its own importers supplies one Typer `app` per source and a `SOURCE_NAME` constant
matching its `sources/*.toml`, converges on `anki_note_tooling.lib.types.FlLine`, and inserts
through `anki_note_tooling.data.inserts`. Note construction is **not** an importer's job — that is
`anki_note_tooling.notes.renderer.render_all`.

### Note generation (`notes/`)

[anki_note_tooling/notes/](anki_note_tooling/notes/) is where a stored line becomes Anki notes.
Nothing here knows about particular decks, models or note types — those come from config.

- `config.py`: pydantic models for the two TOML shapes. A **source** (`sources/<name>.toml`, keyed
  by `fl_source_type.name`) declares its `language`, the `slots` its `[[a::b::c]]` annotations
  capture, which recipes it generates (`notes = [...]`), and an optional `[source.images]` block. A
  **recipe** (`note_recipes/<name>.toml`) declares a `deck`, an Anki `model`, a cardinality
  (`for_each = "line"` or `"substitution"`), the `active`/`inactive` substitution templates, and
  `[recipe.fields]` keyed by real Anki field name. The slots a recipe needs are **inferred** from
  its templates via `string.Template.get_identifiers()`, so there is no declaration to drift out of
  sync.
- `annotation.py`: parses `[[...]]` into positional slot values plus the optional `#tag`. Arity
  comes from the source, so one-slot and four-slot sources both work.
- `renderer.py`: group keys, fan-out, `$n` numbering, `active`/`inactive` rendering, field
  substitution. Templating is `string.Template` (`$var`), not Jinja2, because recipes must emit
  `{{c1::...}}` cloze markup literally. Always `substitute`, never `safe_substitute`.
- `note.py`: `RenderedNote` (what a recipe produced) and `StoredNote` (a `managed_anki_note` row).
- `reconcile.py`: the three-way diff between what a line renders to now and what is already
  recorded for it — update / insert / orphan. Orphans are **reported, never deleted**.
- `commands.py`: `anki-note-tooling sources validate` and `anki-note-tooling sources render`.

Note **refs** are authored, not positional: `"{recipe}"` for `for_each = "line"` and
`"{recipe}:{group key}"` for `for_each = "substitution"`. They only need to be unique within a
line, since `managed_anki_note.source_id` scopes them.

**HTML escaping**: slot values are escaped (they are line fragments this package split out
itself); line variables such as `$english_line` are **not**, because they routinely carry
deliberate markup.

### Images (`images/`)

[anki_note_tooling/images/pool.py](anki_note_tooling/images/pool.py) holds the picker's selection
logic and the `anki-note-tooling images` CLI. One generic `image_pool_entry` table serves every
source; `[source.images]` decides how it is queried (`match` narrows by a line variable,
`refine_by` then narrows by a metadata key on the line's current image). Omitting the block means
no picker for that source.

### Anki integration

[anki_note_tooling/anki_shared.py](anki_note_tooling/anki_shared.py) and
[anki_note_tooling/anki_lib/](anki_note_tooling/anki_lib/) wrap the third-party `anki` package
(Anki's own collection library) to read/write the actual Anki collection and media directory.
Fields are written **by name** (`n[field] = value`), so fields a recipe does not mention keep
whatever is already in them.
[anki_note_tooling/make_anki_cards.py](anki_note_tooling/make_anki_cards.py) adapts a stored line
into something the renderer can use.

Importing anything from the `anki` package requires `import anki.collection` first — its own
modules have a circular import that only resolves in that order. `tests/conftest.py` does this for
the suite.

### Web UI (`web/`)

Flask + htmx admin UI for reviewing/editing lines before note generation.

- [anki_note_tooling/web/server.py](anki_note_tooling/web/server.py): `create_app(config_dir)`
  loads config, builds the DB engine, creates the Flask app, registers the `RecordNotFoundError`
  handler, and registers `ROUTES` via `anki_note_tooling.web.routing.add_routes`.
- [anki_note_tooling/web/routing.py](anki_note_tooling/web/routing.py): `add_routes` does
  annotation-based dependency injection — if a view function declares a parameter named `db`
  annotated `sqlalchemy.Engine` and/or `config` annotated `anki_note_tooling.config.Config`, that
  parameter is partial-bound automatically before the view is registered with Flask. New view
  functions that need the DB or config should declare those parameters with those exact
  names/annotations rather than importing globals.
- [anki_note_tooling/web/views/](anki_note_tooling/web/views/): the view functions themselves
  (`misc.py`, `fl_line.py`).
- [anki_note_tooling/web/templates/](anki_note_tooling/web/templates/): Jinja2 (`.html.j2`)
  templates, htmx-driven partial updates (many routes return template fragments, not full pages).
- [anki_note_tooling/web/static/](anki_note_tooling/web/static/): everything the browser loads,
  all of it vendored — `htmx-1.9.5.min.js`, `paper-1.9.2.min.css`, `fonts.css` and the `fonts/`
  it points at, plus `no_image.png` (the placeholder for a line with no image). The base template
  loads `fonts.css` **before** PaperCSS, because it declares the two faces PaperCSS then asks for.
  Nothing here reaches the network; see "Licensing and vendored assets" below before changing any
  of it.

## Licensing and vendored assets

This package is MIT ([LICENSE](LICENSE)). The browser assets under `web/static/` are **not** —
they are third-party works under their own licences and copyright holders: PaperCSS (ISC),
normalize.css (MIT, bundled inside the PaperCSS file), htmx (BSD 2-Clause), and the Neucha and
Patrick Hand SC typefaces (SIL OFL 1.1).

Three things follow, and all three are easy to break by accident:

- **Adding or upgrading a vendored asset means updating
  [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) and adding its full licence text to
  [licenses/](licenses/)** in the same change. `license-files` in `pyproject.toml` ships both into
  the wheel's `dist-info/licenses/`, which is what actually satisfies the notice requirements —
  BSD 2-Clause in particular asks for the notice in "materials provided with the distribution",
  which a comment inside a minified file does not give.
- **`paper-1.9.2.min.css` is deliberately modified**: its `@import` of the Google Fonts API is
  removed, and the file says so in a comment after its licence banner. Re-vendoring it from
  upstream without removing that import again would silently make every page load call out to
  Google. The fonts are served locally instead.
- **The two vendored text assets keep a `/*! ... */` banner** naming version, licence and
  copyright. That form survives minifiers, so the notice travels with the file when a browser
  fetches it on its own. In `paper-1.9.2.min.css` the banner sits *after* `@charset "UTF-8";`
  rather than above it, because `@charset` is only honoured as the very first token of a
  stylesheet. The `.woff2` files are binary and carry no banner, which is why the notices file
  and `licenses/` are what cover them.

`no_image.png` is the author's own work and is covered by this package's own licence.

## Conventions

- Domain/value types are frozen, `kw_only=True` dataclasses; pydantic `BaseModel` is used for
  config and data shapes that need validation/(de)serialization.
- Prefer pure functions; write one that mutates program data only when the pure version would be
  convoluted. Name mutating functions/methods after their side effects, and give them a short
  docstring describing what they change. Mutating a local variable inside a function is always fine
  when it reads better (e.g. building up a return value incrementally).
- "Parse, don't validate" — convert data to concrete types as early as possible.
- When a value can take several distinct shapes, model it as a union type or a class hierarchy
  rather than as one type with boolean/enum fields selecting the shape.
- Pass resources (configs, database connections, Anki collections) as function arguments rather
  than reading global state.
- Exceptions are named with an `Error` suffix (ruff's `N818` enforces this).
- The web UI makes no outbound network requests. Anything it loads is vendored under
  `web/static/`, which carries the licensing obligations described above.
- Comments and docstrings describe the code as it stands now, not how it got there. Explain what
  something does and why — including what a choice prevents ("written by name so a field reorder in
  Anki cannot corrupt every note") — but never contrast it with an earlier version of the code
  ("...which the old positional write could not manage", "this used to raise"). Someone reading the
  file has no access to the state being contrasted against, and the note goes stale the moment the
  next change lands. The exceptions are planning docs, migration commands and one-off scripts,
  where the historical shape of the *data* is what the code is about.
- For testing:
  - Testing does not need to be exhaustive.
  - Hypothesis is a dev dependency; prefer it where an invariant is universally quantified (the
    renderer has several) and example-based tests where specific output is being pinned.
  - If something can be tested with a doctest, that's preferable to a standalone test.
  - Mocking should be kept to a minimum; ideally instantiate real objects directly (e.g.
    `Config(anki=..., database=..., ...)`), but use autospec mocks when referring to
    operations/objects that would touch real resources (e.g. file I/O, DB, Anki collection), or to
    verify side effects.
- Ruff import sorting is enabled (`select = ["I"]`); run `make lint-and-format` rather than
  hand-ordering imports.
