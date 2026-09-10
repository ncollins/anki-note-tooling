# Third-party notices

anki-note-tooling is MIT licensed (see [LICENSE](LICENSE)). It also redistributes the
third-party files below, each under its own licence and each with its own copyright holder.
Those licences are **not** MIT and are not superseded by this project's licence.

Full licence texts are in [licenses/](licenses/), and are installed alongside the package's
own licence under `anki_note_tooling-<version>.dist-info/licenses/`.

## Bundled web assets

All three live in [anki_note_tooling/web/static/](anki_note_tooling/web/static/) and are served
by the web UI.

### PaperCSS 1.9.2 — ISC

- File: `anki_note_tooling/web/static/paper-1.9.2.min.css`
- Copyright (c) 2017–2018, Rhyne Vlaservich \<rhyneav@gmail.com\>
- Upstream: <https://github.com/papercss/papercss> — <https://www.getpapercss.com>
- Licence text: [licenses/papercss-1.9.2.LICENSE](licenses/papercss-1.9.2.LICENSE)
- SPDX: `ISC`

### normalize.css 7.0.0 — MIT

Bundled *inside* the PaperCSS file above rather than vendored separately; its banner comment
is preserved in place.

- File: within `anki_note_tooling/web/static/paper-1.9.2.min.css`
- Copyright © Nicolas Gallagher and Jonathan Neal
- Upstream: <https://github.com/necolas/normalize.css>
- Licence text: [licenses/normalize.css-7.0.0.LICENSE](licenses/normalize.css-7.0.0.LICENSE)
- SPDX: `MIT`

### htmx 1.9.5 — BSD 2-Clause

- File: `anki_note_tooling/web/static/htmx-1.9.5.min.js`
- Copyright (c) 2020, Big Sky Software. All rights reserved.
- Upstream: <https://github.com/bigskysoftware/htmx> — <https://htmx.org>
- Licence text: [licenses/htmx-1.9.5.LICENSE](licenses/htmx-1.9.5.LICENSE)
- SPDX: `BSD-2-Clause`

Clause 2 of the BSD 2-Clause licence requires binary redistributions to reproduce the notice
"in the documentation and/or other materials provided with the distribution" — which is what
this file and `licenses/` are for. The banner comment in the minified file alone would not
satisfy it.

### Neucha — SIL Open Font License 1.1

- Files: `anki_note_tooling/web/static/fonts/neucha-v18-*.woff2`
- Copyright (c) 2008-2010 by Jovanny Lemonad (<http://www.jovanny.ru>)
- Upstream: <https://fonts.google.com/specimen/Neucha> — <https://github.com/google/fonts/tree/main/ofl/neucha>
- Licence text: [licenses/neucha.OFL.txt](licenses/neucha.OFL.txt)
- SPDX: `OFL-1.1`

### Patrick Hand SC — SIL Open Font License 1.1

- Files: `anki_note_tooling/web/static/fonts/patrick-hand-sc-v17-*.woff2`
- Copyright 2012 The Patrick Hand Authors (mail@patrickwagesreiter.at)
- Upstream: <https://fonts.google.com/specimen/Patrick+Hand+SC> — <https://github.com/google/fonts/tree/main/ofl/patrickhandsc>
- Licence text: [licenses/patrick-hand-sc.OFL.txt](licenses/patrick-hand-sc.OFL.txt)
- SPDX: `OFL-1.1`

Both are the woff2 subsets the Google Fonts API serves, vendored unmodified. The OFL permits
redistribution as part of a larger work; it forbids selling the fonts on their own and
requires that a *modified* version not use the Reserved Font Name. Neither applies here — the
files are bundled and unaltered.

## No runtime network dependency

The web UI loads nothing from the internet. PaperCSS ships an `@import` of the Google Fonts
API; that import has been removed from the vendored stylesheet — the one modification made to
it, noted in the file itself — and the two typefaces are vendored under
`anki_note_tooling/web/static/fonts/` and declared by `static/fonts.css`, which the base
template loads first.

`anki_note_tooling/web/static/no_image.png` is the author's own work and is covered by this
project's own [LICENSE](LICENSE).
