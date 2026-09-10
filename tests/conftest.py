# anki's own modules have a circular import that only resolves when anki.collection is
# imported first; pytest's collection order otherwise trips over it.
import anki.collection  # noqa: F401
