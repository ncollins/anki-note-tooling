"""
Turning an annotated line plus a recipe into `RenderedNote`s.

The two axes a recipe declares are orthogonal: `for_each` decides *how many* notes a line
produces, and the `active` / `inactive` templates decide how each substitution *renders*
into `$note_text`. Cloze is not a special case here — it is an `active` template
that happens to emit `{{c1::...}}`.

Both cardinalities share one notion of "these substitutions are the same thing", the
**group key**, defined once so the two cannot drift apart.
"""

import html
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import anki_note_tooling.file_utils
import anki_note_tooling.lib.types
import anki_note_tooling.notes.annotation
import anki_note_tooling.notes.config
import anki_note_tooling.notes.note


class RenderError(Exception):
    """A recipe could not be rendered against a line."""


@dataclass(kw_only=True, frozen=True)
class LineToRender:
    """
    Everything a recipe can refer to about one line, independent of how it was stored.

    Keeping this separate from `CompleteFlLine` is what lets the renderer serve both the
    database path and the ingesters, which build notes before a line has a row.
    """

    source_name: str
    annotated_text: str
    #: The line as it stands, unannotated. Recipes reach it as `$line_text`.
    text: str
    language: str
    english_line: str = ""
    source_tag: str = ""
    character_name: str = ""
    audio_path: Path | None = None
    image_path: Path | None = None


def _media_context(line: LineToRender) -> dict[str, str]:
    """
    The media variables, as *complete* Anki fragments.

    Expanding to the whole fragment rather than a bare filename is what removes the need
    for conditionals in templates: a line with no image yields `""`, not `<img src="">`.
    """
    audio_filename = (
        anki_note_tooling.file_utils.clean_file_name(line.audio_path.with_suffix(".mp3").name)
        if line.audio_path is not None
        else ""
    )
    image_filename = (
        anki_note_tooling.file_utils.clean_file_name(line.image_path.name)
        if line.image_path is not None
        else ""
    )
    return {
        "audio": f"[sound:{audio_filename}]" if audio_filename else "",
        "image": f'<img src="{image_filename}">' if image_filename else "",
        "audio_filename": audio_filename,
        "image_filename": image_filename,
    }


def _line_context(line: LineToRender) -> dict[str, str]:
    # Line variables are *not* escaped. They routinely carry deliberate markup — 66 of the
    # 67 annotated drama lines have `<br>` or `&nbsp;` in `english_line` — so
    # escaping them would turn working cards into visible entities. Slot values, which
    # anki_note_tooling splits out of the annotation itself, are escaped; see `_slot_context`.
    return {
        "line_text": line.text,
        "annotated_text": line.annotated_text,
        "english_line": line.english_line,
        "source_tag": line.source_tag,
        "character_name": line.character_name,
        "language": line.language,
    }


def _slot_context(
    substitution: anki_note_tooling.notes.annotation.Substitution, slots: Sequence[str]
) -> dict[str, str]:
    """Slot values, HTML-escaped: they are line fragments, never authored markup."""
    return {name: html.escape(value) for name, value in substitution.by_slot(slots).items()}


def _substitute(template: str, context: Mapping[str, Any], *, what: str) -> str:
    """
    `Template.substitute`, never `safe_substitute`: a mistyped `$romaji` has to raise
    rather than silently produce an empty Anki field discovered weeks later.
    """
    try:
        return string.Template(template).substitute(context)
    except KeyError as e:
        raise RenderError(
            f"{what} refers to ${e.args[0]}, which is not available here "
            f"(available: {', '.join('$' + k for k in sorted(context))})"
        ) from e
    except ValueError as e:
        raise RenderError(f"{what} is not a valid template: {e}") from e


#: A group key is `(kind, text)`. The kind is carried so that an occurrence-unique key —
#: which exists only to give each cloze blank its own number — is never mistaken for an
#: authored one and turned into a note ref.
GroupKey = tuple[str, str]


def group_key(
    substitution: anki_note_tooling.notes.annotation.Substitution,
    occurrence: int,
    *,
    recipe: anki_note_tooling.notes.config.Recipe,
    slots: Sequence[str],
) -> GroupKey:
    """
    Which group a substitution belongs to, resolved in one place for both cardinalities.

    The hand-written `#tag` wins, then the recipe's `default_group_by` slot, then a
    default that follows the cardinality — because the key is doing a different job in
    each. Under `for_each = "line"` it only assigns cloze numbers, and two identical
    blanks in one sentence are still two blanks to test; under
    `for_each = "substitution"` it decides note *identity*, so two substitutions
    identical in every slot are one card.

    >>> from anki_note_tooling.notes.annotation import Substitution
    >>> from anki_note_tooling.notes.config import Recipe
    >>> cloze = Recipe(name="c", deck="d", model="m", for_each="line",
    ...                active="$kanji", fields={"F": "$note_text"})
    >>> phrase = Recipe(name="p", deck="d", model="m", for_each="substitution",
    ...                 active="$kanji", fields={"F": "$note_text"})
    >>> slots = ["kanji", "kana", "romanji"]
    >>> kaeru = Substitution(values=("帰", "かえ", "kae"))
    >>> group_key(kaeru, 3, recipe=cloze, slots=slots)
    ('occurrence', '3')
    >>> group_key(kaeru, 3, recipe=phrase, slots=slots)
    ('composite', '帰::かえ::kae')
    >>> tagged = Substitution(values=("食", "た", "ta"), tag="eat")
    >>> group_key(tagged, 0, recipe=phrase, slots=slots)
    ('tag', 'eat')
    """
    if substitution.tag is not None:
        return ("tag", substitution.tag)
    if recipe.default_group_by is not None:
        return ("slot", substitution.by_slot(slots)[recipe.default_group_by])
    if recipe.for_each == "line":
        return ("occurrence", str(occurrence))
    return ("composite", "::".join(substitution.values))


def _group_keys(
    line: anki_note_tooling.notes.annotation.AnnotatedLine,
    *,
    recipe: anki_note_tooling.notes.config.Recipe,
    slots: Sequence[str],
) -> list[GroupKey]:
    return [
        group_key(substitution, occurrence, recipe=recipe, slots=slots)
        for occurrence, substitution in enumerate(line.substitutions())
    ]


def _ordered_groups(keys: Iterable[GroupKey]) -> list[GroupKey]:
    """The distinct group keys, in order of first appearance."""
    return list(dict.fromkeys(keys))


def _render_text(
    line: anki_note_tooling.notes.annotation.AnnotatedLine,
    *,
    recipe: anki_note_tooling.notes.config.Recipe,
    slots: Sequence[str],
    context: Mapping[str, str],
    keys: Sequence[GroupKey],
    focus: GroupKey | None,
) -> str:
    """
    The line rendered into `$note_text`.

    With `focus` set, the focused group renders through `active` and everything else
    through `inactive`; with `focus` None — `for_each = "line"` — every substitution is
    active. `$n` is the group's 1-based ordinal, so substitutions sharing a group key
    share a number.
    """
    inactive = recipe.inactive if recipe.inactive is not None else f"${slots[0]}"
    ordinals = {key: i + 1 for i, key in enumerate(_ordered_groups(keys))}
    first_in_group: dict[GroupKey, int] = {}
    for occurrence, key in enumerate(keys):
        first_in_group.setdefault(key, occurrence)

    rendered: list[str] = []
    occurrence = 0
    for part in line:
        if isinstance(part, str):
            rendered.append(part)
            continue

        key = keys[occurrence]
        is_active = focus is None or (
            key == focus and (recipe.highlight == "all" or first_in_group[key] == occurrence)
        )
        template = recipe.active if is_active else inactive
        rendered.append(
            _substitute(
                template,
                {**context, **_slot_context(part, slots), "n": ordinals[key]},
                what=f"recipe {recipe.name!r} {'active' if is_active else 'inactive'} template",
            )
        )
        occurrence += 1

    return "".join(rendered)


def _media(line: LineToRender) -> tuple[tuple[anki_note_tooling.lib.types.MediaType, Path], ...]:
    media = []
    if line.audio_path is not None:
        media.append((anki_note_tooling.lib.types.MediaType.AUDIO, line.audio_path))
    if line.image_path is not None:
        media.append((anki_note_tooling.lib.types.MediaType.IMAGE, line.image_path))
    return tuple(media)


def _note(
    *,
    recipe: anki_note_tooling.notes.config.Recipe,
    ref: str,
    context: Mapping[str, str],
    line: LineToRender,
) -> anki_note_tooling.notes.note.RenderedNote:
    return anki_note_tooling.notes.note.RenderedNote(
        recipe=recipe.name,
        ref=ref,
        deck=recipe.deck,
        model=recipe.model,
        text=context["note_text"],
        fields={
            name: _substitute(template, context, what=f"recipe {recipe.name!r} field {name!r}")
            for name, template in recipe.fields.items()
        },
        tags=tuple(
            _substitute(template, context, what=f"recipe {recipe.name!r} tag")
            for template in recipe.tags
        ),
        media=_media(line),
    )


def render(
    line: LineToRender,
    *,
    recipe: anki_note_tooling.notes.config.Recipe,
    source: anki_note_tooling.notes.config.Source,
) -> list[anki_note_tooling.notes.note.RenderedNote]:
    """
    Every note `recipe` makes from `line`, in the order their groups first appear.

    Refs are unique within a line, which `managed_anki_note.source_id` already scopes, so
    they carry no source component and renaming a source does not invalidate them.
    """
    parsed = anki_note_tooling.notes.annotation.parse(line.annotated_text, slots=source.slots)
    keys = _group_keys(parsed, recipe=recipe, slots=source.slots)
    base = {**_line_context(line), **_media_context(line)}

    if recipe.for_each == "line":
        text = _render_text(
            parsed, recipe=recipe, slots=source.slots, context=base, keys=keys, focus=None
        )
        return [
            _note(recipe=recipe, ref=recipe.name, context={**base, "note_text": text}, line=line)
        ]

    notes = []
    refs: dict[str, GroupKey] = {}
    # TODO: choose a group's representative deliberately instead of by dict ordering.
    # A repeated key keeps the *last* substitution, while `_render_text` highlights the
    # *first* (the default `highlight = "first"`). The two agree only when a group's members
    # are identical, which a plain repeated word is. They disagree the moment a `#tag` spans
    # different forms of a word — the note then colours 書く and gives かける as its reading.
    by_key = {key: sub for key, sub in zip(keys, parsed.substitutions())}
    for key in _ordered_groups(keys):
        ref = f"{recipe.name}:{key[1]}"
        if ref in refs:
            raise RenderError(
                f"{line.source_name} line renders two groups ({refs[ref]} and {key}) to "
                f"the same ref {ref!r}; rename one of the #tags"
            )
        refs[ref] = key
        text = _render_text(
            parsed, recipe=recipe, slots=source.slots, context=base, keys=keys, focus=key
        )
        context = {
            **base,
            "note_text": text,
            **_slot_context(by_key[key], source.slots),
        }
        notes.append(_note(recipe=recipe, ref=ref, context=context, line=line))

    return notes


def render_all(
    line: LineToRender, *, config: anki_note_tooling.notes.config.NoteConfig
) -> list[anki_note_tooling.notes.note.RenderedNote]:
    """
    Every note the line's source is configured to produce.

    Raises `SourceConfigError` when the line's source has no definition — a normal state
    for a line imported before anyone wrote its TOML, and one callers report rather than
    crash on.
    """
    source = config.sources.get(line.source_name)
    if source is None:
        raise anki_note_tooling.notes.config.SourceConfigError(
            f"no source definition for {line.source_name!r}"
        )
    notes = []
    for recipe in config.recipes_for(line.source_name):
        notes.extend(render(line, recipe=recipe, source=source))
    return notes
