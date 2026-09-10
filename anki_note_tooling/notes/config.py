"""
Source definitions and note recipes, loaded from TOML.

Two kinds of file, deliberately kept apart, plus an explicit binding between them:

- `<config_dir>/sources/*.toml` — one per source. The `name` is the key into
  `fl_source_type.name`, so a line resolves to exactly one definition by construction.
  A source declares the slots its `[[a::b::c]]` annotations capture and, via `notes`,
  which recipes it generates.
- `<config_dir>/note_recipes/*.toml` — one per recipe: a deck, an Anki model, a
  cardinality (`for_each`), the `active` / `inactive` substitution templates, and the
  fields to write.

Recipes are shared across sources, because in practice several sources want the same
kinds of note; the binding lives on the source so that adding a source is one new file
rather than an edit to every recipe.
"""

import string
import tomllib
from pathlib import Path
from typing import Any, Iterable, Literal, Self, TypeVar

import pydantic

SOURCES_DIR_NAME = "sources"
NOTE_RECIPES_DIR_NAME = "note_recipes"

#: Variables every recipe may use regardless of the source it is bound to. Anything a
#: template references that is not one of these — and not `$n`, which the renderer binds
#: while expanding a substitution — has to be one of the source's declared slots.
LINE_VARIABLES = frozenset(
    {
        # The line itself, in its three forms. `$line_text` is the plain line and
        # `$annotated_text` its `[[a::b::c]]` source; both are the same for every note the
        # line makes. `$note_text` is what one note renders to, so it is bound per note by
        # `anki_note_tooling.notes.renderer.render` rather than by `_line_context`, and — being the thing
        # under construction there — it is the one variable an `active`/`inactive` template
        # cannot refer to.
        "line_text",
        "annotated_text",
        "note_text",
        "english_line",
        "source_tag",
        "character_name",
        "language",
    }
)

MEDIA_VARIABLES = frozenset({"image", "audio", "image_filename", "audio_filename"})

#: Bound by the renderer inside an `active` / `inactive` template, so it never counts as
#: a slot requirement.
SUBSTITUTION_INDEX_VARIABLE = "n"

NON_SLOT_VARIABLES = LINE_VARIABLES | MEDIA_VARIABLES | {SUBSTITUTION_INDEX_VARIABLE}


def template_variables(template: str) -> set[str]:
    """
    Every `$var` / `${var}` a template references.

    >>> sorted(template_variables("{{c$n::$kanji::$kana}}"))
    ['kana', 'kanji', 'n']
    >>> sorted(template_variables("no variables here"))
    []
    """
    return set(string.Template(template).get_identifiers())


class ImageSelection(pydantic.BaseModel):
    """
    How the image picker narrows this source's pool.

    Both keys are optional and independent: with neither, the picker offers the whole
    pool; `match` narrows to entries whose `key` equals the named line variable; and
    `refine_by` then keeps only entries agreeing with the line's current image on that
    metadata key.
    """

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    match: str | None = None
    refine_by: str | None = None

    @pydantic.field_validator("match")
    @classmethod
    def _match_names_a_line_variable(cls, value: str | None) -> str | None:
        """
        >>> ImageSelection(match="$character_name").match
        '$character_name'
        >>> ImageSelection._match_names_a_line_variable("$kanji")
        Traceback (most recent call last):
        ValueError: `match` must reference a line variable (...), got '$kanji'
        """
        if value is None:
            return value
        names = template_variables(value)
        unknown = names - LINE_VARIABLES
        if unknown or not names:
            raise ValueError(
                f"`match` must reference a line variable "
                f"({', '.join('$' + v for v in sorted(LINE_VARIABLES))}), got {value!r}"
            )
        return value


class Source(pydantic.BaseModel):
    """One `sources/*.toml` file."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    name: str
    language: str
    slots: tuple[str, ...]
    notes: tuple[str, ...] = ()
    volume_scale_percentage: int | None = None
    images: ImageSelection | None = None

    @pydantic.field_validator("slots")
    @classmethod
    def _slots_are_distinct_and_non_empty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("`slots` must declare at least one slot")
        if len(set(value)) != len(value):
            raise ValueError(f"`slots` must be distinct, got {list(value)}")
        shadowed = set(value) & NON_SLOT_VARIABLES
        if shadowed:
            raise ValueError(f"slots {sorted(shadowed)} shadow built-in variables; rename them")
        return value

    @property
    def surface_slot(self) -> str:
        """
        The slot holding the surface form — the text as it appears in the line.

        >>> Source(name="podcast", language="ja", slots=("kanji", "kana")).surface_slot
        'kanji'
        """
        return self.slots[0]


class Recipe(pydantic.BaseModel):
    """One `note_recipes/*.toml` file."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    name: str
    deck: str
    model: str
    for_each: Literal["line", "substitution"]
    active: str
    inactive: str | None = None
    default_group_by: str | None = None
    highlight: Literal["first", "all"] = "first"
    tags: tuple[str, ...] = ()
    fields: dict[str, str] = pydantic.Field(default_factory=dict)

    @pydantic.model_validator(mode="after")
    def _inactive_is_meaningless_for_a_whole_line(self) -> Self:
        """
        With `for_each = "line"` every substitution is active, so an `inactive` template
        would never be rendered — setting one means the recipe was written for the other
        cardinality.
        """
        if self.for_each == "line" and self.inactive is not None:
            raise ValueError(
                f'recipe {self.name!r} sets `inactive`, but with for_each = "line" every '
                "substitution is active, so it would never be rendered"
            )
        return self

    @pydantic.model_validator(mode="after")
    def _fields_are_declared(self) -> Self:
        if not self.fields:
            raise ValueError(f"recipe {self.name!r} declares no [recipe.fields]")
        return self

    def templates(self) -> list[str]:
        """Every template string this recipe renders, in no particular order."""
        inactive = [] if self.inactive is None else [self.inactive]
        return [self.active, *inactive, *self.tags, *self.fields.values()]

    def required_slots(self) -> set[str]:
        """
        The slots this recipe's templates require, inferred rather than declared.

        Inferring it means there is no `requires_slots` key to drift out of sync with the
        templates that actually matter.

        >>> recipe = Recipe(
        ...     name="phrase", deck="d", model="m", for_each="substitution",
        ...     active="$kanji", fields={"Romanji": "$romanji", "Text": "$note_text"},
        ... )
        >>> sorted(recipe.required_slots())
        ['kanji', 'romanji']
        """
        used: set[str] = set()
        for template in self.templates():
            used |= template_variables(template)
        if self.default_group_by is not None:
            used.add(self.default_group_by)
        return used - NON_SLOT_VARIABLES


class SourceConfigError(Exception):
    """A source or recipe file is missing, malformed, or binds to something it cannot."""


class NoteConfig(pydantic.BaseModel):
    """Every source and recipe found under a config directory, with the bindings resolved."""

    model_config = pydantic.ConfigDict(frozen=True)

    sources: dict[str, Source]
    recipes: dict[str, Recipe]

    def source_for(self, source_name: str) -> Source:
        """
        The definition named `source_name`, or `SourceConfigError` naming what is defined.

        Every importer needs this before it can read anything: the language a line defaults
        to and the slots its annotation must fill are the source's business, not the
        importer's, so one importer serves every source that shares its input format.

        >>> config = NoteConfig(sources={}, recipes={})
        >>> config.source_for("drama")
        Traceback (most recent call last):
        anki_note_tooling.notes.config.SourceConfigError: no source definition for 'drama' (defined: none)
        """
        source = self.sources.get(source_name)
        if source is None:
            defined = ", ".join(sorted(self.sources)) or "none"
            raise SourceConfigError(
                f"no source definition for {source_name!r} (defined: {defined})"
            )
        return source

    def recipes_for(self, source_name: str) -> list[Recipe]:
        """
        The recipes bound to `source_name`, in the order the source lists them.

        Raises `SourceConfigError` for a source with no definition — a normal state for a
        line imported before its TOML was written, and one callers are expected to report
        rather than crash on.
        """
        source = self.sources.get(source_name)
        if source is None:
            raise SourceConfigError(f"no source definition for {source_name!r}")
        return [self.recipes[name] for name in source.notes]


def _read_toml(path: Path, section: str) -> dict[str, Any]:
    with path.open("rb") as f:
        raw = tomllib.load(f)
    if section not in raw:
        raise SourceConfigError(f"{path} has no [{section}] table")
    return raw[section]


_Definition = TypeVar("_Definition", bound=pydantic.BaseModel)


def _load_directory(
    directory: Path, section: str, model: type[_Definition]
) -> dict[str, _Definition]:
    loaded: dict[str, _Definition] = {}
    if not directory.is_dir():
        return loaded
    for path in sorted(directory.glob("*.toml")):
        try:
            parsed = model.model_validate(_read_toml(path, section))
        except pydantic.ValidationError as e:
            raise SourceConfigError(f"{path} is not a valid [{section}]:\n{e}") from e
        name: str = parsed.name  # type: ignore[attr-defined]
        if name in loaded:
            raise SourceConfigError(f"{path} redefines {section} {name!r}")
        loaded[name] = parsed
    return loaded


def check_bindings(config: NoteConfig) -> list[str]:
    """
    Every way a source and the recipes it binds to can disagree, as a list of messages.

    Recipes are shared, so this is a check over the source x recipe cross product rather
    than over each file in isolation.

    >>> source = Source(name="cantonese_drama", language="zh-yue",
    ...                 slots=("hanzi", "jyutping"), notes=("phrase",))
    >>> recipe = Recipe(name="phrase", deck="d", model="m", for_each="substitution",
    ...                 active="$hanzi", fields={"Romanji": "$romanji"})
    >>> check_bindings(NoteConfig(sources={source.name: source}, recipes={recipe.name: recipe}))
    ["source 'cantonese_drama' binds recipe 'phrase', which uses $romanji, but declares slots [hanzi, jyutping]"]
    """
    problems = []
    for source in config.sources.values():
        for recipe_name in source.notes:
            recipe = config.recipes.get(recipe_name)
            if recipe is None:
                problems.append(
                    f"source {source.name!r} binds recipe {recipe_name!r}, which does not exist"
                )
                continue
            missing = sorted(recipe.required_slots() - set(source.slots))
            if missing:
                problems.append(
                    f"source {source.name!r} binds recipe {recipe.name!r}, which uses "
                    f"{', '.join('$' + slot for slot in missing)}, but declares slots "
                    f"[{', '.join(source.slots)}]"
                )
    return problems


def load_unchecked(config_dir: Path) -> NoteConfig:
    """
    Loads every source and recipe under `config_dir` without checking the bindings.

    For `sources validate`, which has to be able to *report* an unsatisfiable binding
    rather than die of one.
    """
    return NoteConfig(
        sources=_load_directory(config_dir / SOURCES_DIR_NAME, "source", Source),
        recipes=_load_directory(config_dir / NOTE_RECIPES_DIR_NAME, "recipe", Recipe),
    )


def load(config_dir: Path) -> NoteConfig:
    """
    Loads every source and recipe under `config_dir`, failing on an unsatisfiable binding.
    """
    config = load_unchecked(config_dir)
    problems = check_bindings(config)
    if problems:
        raise SourceConfigError("\n".join(problems))
    return config


def unconfigured_source_names(config: NoteConfig, known_names: Iterable[str]) -> list[str]:
    """
    Source names present in the database but with no definition on disk.

    A warning rather than an error: a definition may legitimately be written after the
    first import, and lines can be imported before anyone decides what notes they make.
    """
    return sorted(set(known_names) - set(config.sources))
