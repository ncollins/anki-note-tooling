"""
The two forms a media file's location takes, and the only conversions between them.

Every image and audio file anki_note_tooling records lives under one root directory. What the database
holds is a location relative to that root; what opening a file needs is an absolute path.
Giving those separate types is what stops one being used where the other is meant.
"""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class MediaPathError(ValueError):
    """A path is not in the form the conversion being asked for requires."""


@dataclass(kw_only=True, frozen=True)
class MediaRef:
    """
    Where a media file sits relative to the media root.

    Deliberately not a filesystem location: it has no `open`, `exists` or `parent`, so code
    wanting the bytes has to go through `MediaStore.locate` and cannot quietly treat a stored
    location as an absolute one. `PurePosixPath` because this is data — it is serialized into
    SQLite and into URLs — rather than a live path on this machine.

    >>> ref = MediaRef(path=PurePosixPath("images/portrait.png"))
    >>> str(ref)
    'images/portrait.png'
    >>> ref.filename
    'portrait.png'
    """

    path: PurePosixPath

    def __str__(self) -> str:
        return str(self.path)

    @property
    def filename(self) -> str:
        """The basename, which is the name the file takes inside Anki's media directory."""
        return self.path.name

    @classmethod
    def parse(cls, value: str | PurePosixPath) -> "MediaRef":
        """
        The reference a stored value denotes.

        A classmethod rather than a `MediaStore` method because reading a stored location back
        needs no root — only writing one and resolving one do. That is what keeps every query
        function free of the store.

        >>> MediaRef.parse("images/portrait.png")
        MediaRef(path=PurePosixPath('images/portrait.png'))

        Rejecting `..` as well as an absolute value is what makes `MediaStore.locate` safe to
        hand a location that arrived from a request:

        >>> MediaRef.parse("/files/images/portrait.png")
        Traceback (most recent call last):
            ...
        anki_note_tooling.lib.media.MediaPathError: stored media location is absolute: /files/images/portrait.png
        >>> MediaRef.parse("../../etc/passwd")
        Traceback (most recent call last):
            ...
        anki_note_tooling.lib.media.MediaPathError: stored media location escapes the root: ../../etc/passwd
        """
        path = PurePosixPath(value)
        if path.is_absolute():
            raise MediaPathError(f"stored media location is absolute: {path}")
        if ".." in path.parts:
            raise MediaPathError(f"stored media location escapes the root: {path}")
        return cls(path=path)


@dataclass(kw_only=True, frozen=True)
class MediaStore:
    """
    The directory every stored media location is relative to.

    >>> store = MediaStore(root=Path("/files"))
    >>> ref = store.store(Path("/files/images/portrait.png"))
    >>> ref
    MediaRef(path=PurePosixPath('images/portrait.png'))
    >>> store.locate(ref)
    PosixPath('/files/images/portrait.png')

    `store` refuses anything it is not for, so a location that has already been converted
    cannot be converted a second time unnoticed:

    >>> store.store(Path("images/portrait.png"))
    Traceback (most recent call last):
        ...
    anki_note_tooling.lib.media.MediaPathError: not an absolute path: images/portrait.png
    >>> store.store(Path("/elsewhere/portrait.png"))
    Traceback (most recent call last):
        ...
    anki_note_tooling.lib.media.MediaPathError: /elsewhere/portrait.png is not inside /files
    """

    root: Path

    def store(self, path: Path) -> MediaRef:
        """The reference to record for an absolute path inside the root."""
        if not path.is_absolute():
            raise MediaPathError(f"not an absolute path: {path}")
        if not path.is_relative_to(self.root):
            raise MediaPathError(f"{path} is not inside {self.root}")
        return MediaRef(path=PurePosixPath(path.relative_to(self.root)))

    def locate(self, ref: MediaRef) -> Path:
        """The absolute path to open for `ref`."""
        return self.root / ref.path
