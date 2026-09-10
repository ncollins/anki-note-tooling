"""
Wrappers around the third-party `anki` package.

`anki.cards`, `anki.notes` and `anki.hooks` import each other, and the cycle only resolves
when `anki.collection` is the first of them to be imported. Doing it here means importing
any module in this package is enough, rather than every caller having to get its own
import order right.
"""

import anki.collection as _anki_collection  # noqa: F401
