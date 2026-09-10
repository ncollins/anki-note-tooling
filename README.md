# anki-note-tooling

[Anki](https://apps.ankiweb.net/) supports the creation of flashcards using
[cloze deletion](https://docs.ankiweb.net/editing.html#cloze-deletion), where
part of a card's text is hidden, or replaced by a hint.

For example, the text `{{c1::Constantinople}} was renamed Istanbul in {{c2::1930::19**}}` would produce two flashcards:
- `[...] was renamed Istanbul in 1930` (answer: `Constantinople`)
- `Constantinople was renamed Istanbul in [19**]` (answer: `1930`)

or the text `{{c1::本::ほん}}を{{c2::読む::よむ}}` would test whether a learner
knows the correct Japanese kanji for words written phonetically in hiragana
by producing two cards:
- `[ほん]を読む` (answer: `本`)
- `本を[よむ]` (answer: `読む`)

The motivation for creating this tooling is that in the Japanese example it would
also be useful to test whether the learner knows the correct hiragana for a
given word written with kanji, but Anki doesn't support generating cards with the
"answer" and "hint" reversed.

This tooling takes sentences with annotations that resemble
Anki's cloze notes, for example `[[本::ほん::hon]]を[[読む::よむ::yomu]]`,
and then creates multiple Anki notes from these entries.

The first versions of this tooling were CLI scripts that took .csv files as input and inserted notes directly
into Anki using [the Anki Python package](https://pypi.org/project/anki/). Later a database
and a web UI were added to help manage notes created by this tooling. Finally (with an LLM's assistance)
the hardcoded references to the author's Anki decks and note types were replaced with a configurable
system for generating notes from the annotations. Here a **source** declares what its annotations capture
and which **recipes** it feeds, a recipe declares a deck, a note type and the templates for its fields,
and the renderer does the rest.

## Getting started

This project is written in [Python](https://www.python.org/) and uses [uv](https://docs.astral.sh/uv/) to
manage the development environment. [ffmpeg](https://ffmpeg.org/) is needed for audio conversion. The code has only been tested on Linux (Ubuntu)
and macOS.

If you have `uv` and `make` in your PATH, `make install-local` will install
the `anki-note-tooling` command locally. The tool has a number of subcommands;
every command takes `--config-dir`, defaulting to `~/.config/anki-note-tooling`,
and every command that writes takes `--live-run` — without it the
command prints what it would do and writes nothing.

Close Anki before running anything that touches the collection. The collection is opened
directly rather than through AnkiConnect, so a command refuses to start while Anki holds it.

**1. Create a config directory and its database.**

```
anki-note-tooling config init ~/.config/anki-note-tooling
```

Writes a commented `config.toml`, a `sources/` and a `note_recipes/` directory, and an empty
database beside them. It refuses to write into a directory that already has anything in it.
Every path in the `config.toml` is a placeholder, so edit them before going further.

**2. Check the configuration.**

```
anki-note-tooling config validate
anki-note-tooling sources validate
```

`config validate` parses `config.toml` and prints what it read. `sources validate` checks the
whole source × recipe cross product, and checks decks, models and field names against the real
Anki collection. Run it again after editing any source or recipe TOML.

**3. Load some lines into the application.**

`anki-note-tooling csv load` takes a .csv file with one compulsory column `text`
(the line itself, unannotated) and a number of optional columns.
`--source` names the `sources/<name>.toml` the lines belong to, and `--media-dir` is where the
`audio` and `image` columns look for files.

```sh
# use --help to learn about the command and optional columns
anki-note-tooling csv load --help

# without --live-run the command will abort the transaction
anki-note-tooling csv load lines.csv --source podcast --media-dir ./media

# with --live-run the lines will be added to the database and media will be copied
anki-note-tooling csv load lines.csv --source podcast --media-dir ./media --live-run
```

If you want to load a selection of relevant images for the source, but initially
not link them to any specific line (for example if you have dialogue lines that
came from a subtitle file, but they aren't tagged by character yet),
you can use
`anki-note-tooling images import --source podcast --dir <files_path>/pictures --live-run`.

**4. Review and edit what you loaded.**

`anki-note-tooling web-server start` runs a small Flask + htmx UI at `http://localhost:5000`
for searching lines, creating/editing annotations, and adding images or audio to the line.

**5. Make the notes.**

Once a line has (i) the original text, (ii) annotated text, (iii) an image,
and (iv) an audio file, notes can be created and inserted into Anki.
This can be done from the web UI's **Notes to insert** page or from the CLI:

```sh
anki-note-tooling make-anki-cards make-staging-notes
anki-note-tooling make-anki-cards make-staging-notes --live-run
```

The difference between the two is that the CLI renders the whole batch before writing
anything, so a source it cannot render is reported before the first note reaches Anki.

The web UI's **Notes to update** page has no CLI equivalent yet.
Once a line's notes are in Anki, changing its
annotation, image, or audio leaves them stale; that page rewrites the notes that still correspond, inserts any the
new annotation adds, and reports any it no longer generates rather than deleting them.

### The rest

`anki-note-tooling --help` lists every group: `config`, `csv`, `images`, `make-anki-cards`,
`sources`, `tables`, `web-server`.

`anki-note-tooling sources render <source_id>` prints the notes one stored line would produce,
which is the quickest way to see what a recipe change does. The line has to be complete — it
needs its annotation, audio and image — which is the same bar `make-staging-notes` applies.

`anki-note-tooling tables schema-version` reports the schema version the database was created
under. A release that changes the schema will ship a command to upgrade an existing database in
place; until then, a database written by a newer release than the one you are running is refused
rather than misread.

## A worked example

This is the whole pipeline, end to end, using exactly the configuration `config init` gives you.

### The source

`sources/podcast.toml` says what an annotation captures and which recipes the source feeds:

```toml
[source]
name     = "podcast"
language = "ja"
slots    = ["written", "reading", "roman"]   # what a [[a::b::c]] annotation captures, in order
notes    = ["cloze", "phrase"]               # which recipes this source generates

# Omit this block entirely and the line details page offers no image picker.
[source.images]
match     = "$character_name"   # a line variable, compared against image_pool_entry.key
refine_by = "scene"             # narrow to entries agreeing with the current image here
```

### The recipes

`note_recipes/cloze.toml` — `for_each = "line"`, so the whole line becomes one note with every
substitution clozed:

```toml
# One cloze note per line: every annotated substitution becomes a blank to fill in.
[recipe]
name     = "cloze"
deck     = "Example Cloze Deck"
model    = "Example Cloze"
for_each = "line"
# `for_each = "line"` means every substitution is active, so there is no `inactive`
# template. `$n` is the cloze number, assigned by the renderer.
active   = "{{c$n::$written::$reading}}"
tags     = ["$source_tag"]

# Keyed by real Anki field name. Fields the model has but this recipe does not name are
# left untouched, so anything typed into them inside Anki survives an update.
  [recipe.fields]
  Expression     = "$note_text"
  Audio          = "$audio"
  Images         = "$image"
  Translation    = "$english_line"
  Raw_expression = "$line_text"
```

`note_recipes/phrase.toml` — `for_each = "substitution"`, so the line becomes one note per
distinct word, each highlighting the one it tests:

```toml
# One note per distinct substitution, each highlighting the word it tests.
[recipe]
name     = "phrase"
deck     = "Example Phrase Deck"
model    = "Example Phrase"
for_each = "substitution"
# No `default_group_by`: substitutions group only when identical in every slot, or when an
# explicit `#tag` says so.
active   = """<font color="#0000ff">$written</font>"""   # the focused substitution
inactive = "$written"                                    # all the others
tags     = ["$source_tag"]

  [recipe.fields]
  Text        = "$note_text"
  Reading     = "$reading"      # slot variables refer to the focused substitution
  Image       = "$image"
  Audio       = "$audio"
  Translation = "$english_line"
```

### The line

```
[[大きい::おおきい::ookii]][[犬::いぬ::inu#inu]]と[[小さい::ちいさい::chiisai]][[犬::いぬ::inu#inu]]
```

Four substitutions, three slots each, matching `slots = ["written", "reading", "roman"]`. Say it
carries `english_line = "a big dog and a small dog"`, `source_tag = "episode_12"`, and audio and
an image.

犬 appears twice, and the `#inu` suffix on both is what says they are the same word rather than
two words that happen to look alike. That is the **grouping tag**: a `#` followed by ASCII
letters or digits, written after the last slot. Substitutions sharing a tag are one thing to
test, however many places they occur in.

### What it renders to

Four notes: one from `cloze`, and one per *group* from `phrase` — three, not four, because the
two 犬 are a single group.

### `cloze` — ref `cloze`

deck `Example Cloze Deck`, model `Example Cloze`, tags `('episode_12',)`

| field | value |
|---|---|
| `Expression` | `{{c1::大きい::おおきい}}{{c2::犬::いぬ}}と{{c3::小さい::ちいさい}}{{c2::犬::いぬ}}` |
| `Audio` | `[sound:episode_12_01.mp3]` |
| `Images` | `<img src="episode_12_01.png">` |
| `Translation` | `a big dog and a small dog` |
| `Raw_expression` | `大きい犬と小さい犬` |

### `phrase` — ref `phrase:大きい::おおきい::ookii`

deck `Example Phrase Deck`, model `Example Phrase`, tags `('episode_12',)`

| field | value |
|---|---|
| `Text` | `<font color="#0000ff">大きい</font>犬と小さい犬` |
| `Reading` | `おおきい` |
| `Image` | `<img src="episode_12_01.png">` |
| `Audio` | `[sound:episode_12_01.mp3]` |
| `Translation` | `a big dog and a small dog` |

escaped: `&lt;font color=&quot;#0000ff&quot;&gt;大きい&lt;/font&gt;犬と小さい犬`

### `phrase` — ref `phrase:inu`

deck `Example Phrase Deck`, model `Example Phrase`, tags `('episode_12',)`

| field | value |
|---|---|
| `Text` | `大きい<font color="#0000ff">犬</font>と小さい犬` |
| `Reading` | `いぬ` |
| `Image` | `<img src="episode_12_01.png">` |
| `Audio` | `[sound:episode_12_01.mp3]` |
| `Translation` | `a big dog and a small dog` |

escaped: `大きい&lt;font color=&quot;#0000ff&quot;&gt;犬&lt;/font&gt;と小さい犬`

### `phrase` — ref `phrase:小さい::ちいさい::chiisai`

deck `Example Phrase Deck`, model `Example Phrase`, tags `('episode_12',)`

| field | value |
|---|---|
| `Text` | `大きい犬と<font color="#0000ff">小さい</font>犬` |
| `Reading` | `ちいさい` |
| `Image` | `<img src="episode_12_01.png">` |
| `Audio` | `[sound:episode_12_01.mp3]` |
| `Translation` | `a big dog and a small dog` |

escaped: `大きい犬と&lt;font color=&quot;#0000ff&quot;&gt;小さい&lt;/font&gt;犬`

Note what varies and what does not. `$note_text` is the whole line as that note presents it, so
the cloze note gets every word clozed while each phrase note colours exactly one group.
`$reading` resolves to the *focused* group, which is why the three phrase notes differ in
`Reading` but share `Translation`. `$n` is the cloze number, assigned by the renderer rather
than written in the template.

The tag earns its keep in both recipes, differently. In the cloze note the two 犬 both render as
`{{c2::...}}` — one blank appearing in two places, hidden and revealed together — where untagged
they would have been `c2` and a separate `c4`, two blanks to answer independently. In the phrase
notes it is note *identity*: the pair becomes one card instead of two, and the ref is the tag,
`phrase:inu`, rather than the composite `phrase:犬::いぬ::inu`. Only the first occurrence is
coloured, which is `highlight = "first"`, the default; `highlight = "all"` colours every member
of the group.

### Markup, and why the escaped form matters

The `Text` field holds real markup, not text that looks like markup — Anki must receive the
tags in order to render a blue 犬. The `escaped:` lines above show the same string with its
markup neutralised: that is what a double-escaping bug produces, and the symptom is a card
displaying `<font color="#0000ff">` as literal text.

The rule the renderer follows is **slot values are escaped, line variables are not**. A slot
value is a fragment this tool split out of a line itself, so a `<` in one is a `<` and nothing
more. Line variables — `$line_text`, `$note_text`, `$english_line` — routinely carry markup that
is meant to render, the `<font>` tags above being exactly that case.

## Licence

MIT — see [LICENSE](LICENSE).

The web UI bundles PaperCSS (ISC), normalize.css (MIT), htmx (BSD 2-Clause) and the Neucha and
Patrick Hand SC typefaces (SIL OFL 1.1), each under its own licence and copyright. It makes no
outbound network requests. See [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) for the details
and [licenses/](licenses/) for the full texts.
