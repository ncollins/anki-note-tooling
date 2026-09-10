"""
Parsing `[[native::hint::roman]]` annotations into positional slot values.

Rendering lives in `anki_note_tooling.notes.renderer`; this module only turns an annotated line into
substitutions and the text between them. How many slots an annotation carries, and what
they are called, comes from the source definition rather than being fixed at three.
"""

import re
from dataclasses import dataclass
from typing import Iterator, Sequence

#: An annotation is a `[[...]]` run containing no further `[`, so an unterminated `[[`
#: falls through as ordinary text and is caught by the stray-bracket check below.
_ANNOTATION = re.compile(r"(\[\[[^\[]*\]\])")

_TAG_DELIMITER = "#"
_SLOT_DELIMITER = "::"

#: What may follow the `#`. A tag becomes a group key and from there a note's ref, so it
#: is held to ASCII letters and digits: anything else is a typo often enough — a stray
#: `]` from a mistyped annotation reads as a perfectly good tag — that accepting it means
#: accepting note identity nobody chose.
_TAG = re.compile(r"[A-Za-z0-9]+")


@dataclass(kw_only=True, frozen=True)
class Substitution:
    """
    One annotation: a value per declared slot, plus the optional `#tag` grouping suffix,
    which is ASCII letters and digits.

    `values` is positional and always exactly as long as the source's `slots`; slots are
    never individually optional, which is what keeps the parser a plain split and keeps
    recipe templates free of conditionals.
    """

    values: tuple[str, ...]
    tag: str | None = None

    @property
    def surface(self) -> str:
        """
        The text as it appears in the line. The first slot is the surface form by
        definition — it is what an unfocused substitution renders as, and what
        `surface_text` joins to reconstruct the plain line.

        >>> Substitution(values=("本当", "ほんとう", "hontou")).surface
        '本当'
        """
        return self.values[0]

    def by_slot(self, slots: Sequence[str]) -> dict[str, str]:
        """
        >>> sub = Substitution(values=("好", "す", "su"))
        >>> sub.by_slot(["kanji", "kana", "romanji"])
        {'kanji': '好', 'kana': 'す', 'romanji': 'su'}
        """
        return dict(zip(slots, self.values, strict=True))


@dataclass(kw_only=True, frozen=True)
class AnnotatedLine:
    """An annotated line split into literal text and `Substitution`s, in order."""

    text: str
    parts: tuple[str | Substitution, ...]

    def substitutions(self) -> list[Substitution]:
        return [p for p in self.parts if isinstance(p, Substitution)]

    def surface_text(self) -> str:
        r"""
        The line with every annotation replaced by its surface form.

        >>> line = parse("[[水::みず::mizu]]と[[火::ひ::hi]]", slots=["kanji", "kana", "romanji"])
        >>> line.surface_text()
        '水と火'
        """
        return "".join(p if isinstance(p, str) else p.surface for p in self.parts)

    def __iter__(self) -> Iterator[str | Substitution]:
        return iter(self.parts)


def _parse_annotation(annotation: str, *, slots: Sequence[str]) -> Substitution:
    inner = annotation[2:-2]

    if inner.count(_TAG_DELIMITER) > 1:
        raise ValueError(
            f"{annotation!r} has more than one {_TAG_DELIMITER!r}; the grouping tag is a "
            "single trailing suffix"
        )
    body, tagged, tag = inner.partition(_TAG_DELIMITER)
    if tagged and _TAG.fullmatch(tag) is None:
        raise ValueError(
            f"{annotation!r} has the grouping tag {tag!r}; a tag must be one or more "
            "ASCII letters or digits"
        )

    values = tuple(body.split(_SLOT_DELIMITER))
    if len(values) != len(slots):
        raise ValueError(
            f"{annotation!r} has {len(values)} slot(s), but this source declares "
            f"{len(slots)}: [{', '.join(slots)}]"
        )
    # The surface form restating its own first hint is always a mis-typed annotation
    # rather than a meaningful one, and rejecting it is what stops junk reaching Anki.
    if len(values) >= 2 and values[0] == values[1]:
        raise ValueError(f"{annotation!r} repeats {values[0]!r} in its first two slots")

    return Substitution(values=values, tag=tag or None)


def parse(annotated_text: str, *, slots: Sequence[str]) -> AnnotatedLine:
    r"""
    Parses `annotated_text` against a source's declared `slots`.

    >>> line = parse("[[本当::ほんとう::hontou]]はね", slots=["kanji", "kana", "romanji"])
    >>> line.parts
    (Substitution(values=('本当', 'ほんとう', 'hontou'), tag=None), 'はね')

    Arity follows the source, so a one-slot Cantonese line parses just as well:

    >>> parse("[[好好#1]][[小姐]]", slots=["hanzi"]).substitutions()
    [Substitution(values=('好好',), tag='1'), Substitution(values=('小姐',), tag=None)]

    An annotation with the wrong number of slots names the ones that were expected:

    >>> parse("[[好::す]]", slots=["kanji", "kana", "romanji"])
    Traceback (most recent call last):
    ValueError: '[[好::す]]' has 2 slot(s), but this source declares 3: [kanji, kana, romanji]

    A `#` with nothing usable after it is an error rather than a tag silently dropped:

    >>> parse("[[好#]]", slots=["hanzi"])
    Traceback (most recent call last):
    ValueError: '[[好#]]' has the grouping tag ''; a tag must be one or more ASCII letters or digits
    """
    parts: list[str | Substitution] = []
    for raw in _ANNOTATION.split(annotated_text):
        if raw == "":
            continue
        if raw.startswith("[[") and raw.endswith("]]"):
            parts.append(_parse_annotation(raw, slots=slots))
        elif "[[" in raw or "]]" in raw:
            raise ValueError(f"unterminated annotation in {raw!r}")
        else:
            parts.append(raw)

    return AnnotatedLine(text=annotated_text, parts=tuple(parts))
