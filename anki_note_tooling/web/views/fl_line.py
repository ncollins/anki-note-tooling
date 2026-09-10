from enum import StrEnum
from pathlib import Path
from typing import Any

import flask
import sqlalchemy
import werkzeug.datastructures
import werkzeug.utils

import anki_note_tooling.config
import anki_note_tooling.data.inserts
import anki_note_tooling.data.queries
import anki_note_tooling.data.types
import anki_note_tooling.images.pool
import anki_note_tooling.lib.media
import anki_note_tooling.lib.types
import anki_note_tooling.lib.utils
import anki_note_tooling.notes.annotation
import anki_note_tooling.notes.config


def toggle_starred(fl_source_line_id: int, *, db: sqlalchemy.Engine):
    with db.connect() as conn:
        is_starred = anki_note_tooling.data.queries.toggle_starred(conn, fl_source_line_id)
        conn.commit()
        return flask.render_template("fl_line_toggle_starred.html.j2", is_starred=is_starred)


#: The form field the annotation editor writes into, and the id its htmx swaps target.
ANNOTATION_FORM_PARAM = "annotated"


def _details_context(
    conn: sqlalchemy.Connection,
    line: anki_note_tooling.data.types.StoredFlLine,
    *,
    config: anki_note_tooling.config.Config,
) -> dict[str, Any]:
    """
    Everything `fl_line_details.html.j2` needs to render one line.

    Separate from the view so that a rejected save can re-render the same page with the
    submitted values in place of the stored ones.

    `image` and `audio` are the paths the form posts back, so absent media is `""` rather
    than `None`: an empty string is what the update path already reads as "no media", and
    what a hidden input round-trips unchanged.
    """
    row = dict(line)
    row["image"] = str(line.image_path) if line.image_path is not None else ""
    row["audio"] = str(line.audio.path) if line.audio is not None else ""

    source = config.notes.sources.get(line.source_type)

    return {
        **row,
        "is_starred": anki_note_tooling.data.queries.get_is_starred(conn, line.source_id),
        "notes": anki_note_tooling.data.queries.get_stored_anki_notes(conn, line.source_id),
        "has_image_picker": source is not None and source.images is not None,
        "unconfigured_source": source is None,
        "form_param_name": ANNOTATION_FORM_PARAM,
        "annotation_error": None,
        "editing_annotation": False,
    }


def details(
    fl_source_line_id: int, *, config: anki_note_tooling.config.Config, db: sqlalchemy.Engine
):
    with db.connect() as conn:
        line = anki_note_tooling.data.queries.get_single_fl_line(conn, fl_source_line_id)
        context = _details_context(conn, line, config=config)

    return flask.render_template("fl_line_details.html.j2", **context)


def _annotation_seed(line: anki_note_tooling.data.types.StoredFlLine) -> str:
    """
    What the annotation editor starts from: the stored annotation, or the raw line when
    there is not one yet.
    """
    return line.annotated_text or line.text


def _annotation_problem(
    annotated_text: str, *, source: anki_note_tooling.notes.config.Source | None, source_type: str
) -> str | None:
    """
    Why this annotation cannot be stored, or None if it can.

    Parsing *is* the validation — it checks the annotation against the slots the source
    declares — so both the save path and the editor's live check ask this one question
    and can never disagree about the answer.

    >>> source = anki_note_tooling.notes.config.Source(
    ...     name="podcast", language="ja", slots=("kanji", "kana", "romanji")
    ... )
    >>> _annotation_problem("[[好::す::su]]きです", source=source, source_type="podcast") is None
    True
    >>> _annotation_problem("[[好::す]]", source=source, source_type="podcast")
    "'[[好::す]]' has 2 slot(s), but this source declares 3: [kanji, kana, romanji]"
    >>> _annotation_problem("[[好::す::su]]", source=None, source_type="podcast")
    "no source definition for 'podcast', so there is nothing to validate this annotation's slots against"
    """
    if source is None:
        return (
            f"no source definition for {source_type!r}, so there is nothing to validate "
            "this annotation's slots against"
        )
    try:
        anki_note_tooling.notes.annotation.parse(annotated_text, slots=source.slots)
    except ValueError as e:
        return str(e)
    return None


# The two views below are the halves of one toggle. Both take the line id and read the text
# from the database rather than carrying it in the URL: round-tripping it through a query
# string truncated any annotation containing `#` — the grouping tag delimiter, so every
# tagged line broke on its second edit — and `&` would have done the same. Passing the id
# leaves nothing to encode wrongly.
def text_uneditable(fl_source_line_id: int, *, db: sqlalchemy.Engine):
    with db.connect() as conn:
        line = anki_note_tooling.data.queries.get_single_fl_line(conn, fl_source_line_id)

    return flask.render_template(
        "fl_line/text_uneditable.html.j2",
        text=_annotation_seed(line),
        source_id=fl_source_line_id,
        form_param_name=ANNOTATION_FORM_PARAM,
    )


def text_editable(fl_source_line_id: int, *, db: sqlalchemy.Engine):
    with db.connect() as conn:
        line = anki_note_tooling.data.queries.get_single_fl_line(conn, fl_source_line_id)

    return flask.render_template(
        "fl_line/text_editable.html.j2",
        text=_annotation_seed(line),
        source_id=fl_source_line_id,
        form_param_name=ANNOTATION_FORM_PARAM,
    )


def validate_annotation(
    fl_source_line_id: int, *, config: anki_note_tooling.config.Config, db: sqlalchemy.Engine
):
    """
    What the annotation currently in the editor would do if saved, without saving it.

    POST rather than GET for the same reason the toggle views take an id: an annotation in
    a query string truncates at its first `#`.

    Text matching what is already stored is reported valid even when it would not parse
    today, because `update_line` only validates an annotation it is about to write. Were
    that not mirrored here, a line stored under an earlier `slots` definition would have
    its save button disabled for good, blocking unrelated image and audio edits.
    """
    annotated_text = flask.request.form.get(ANNOTATION_FORM_PARAM, "").strip()

    with db.connect() as conn:
        line = anki_note_tooling.data.queries.get_single_fl_line(conn, fl_source_line_id)

    if annotated_text and annotated_text != line.annotated_text:
        problem = _annotation_problem(
            annotated_text,
            source=config.notes.sources.get(line.source_type),
            source_type=line.source_type,
        )
    else:
        problem = None

    return flask.render_template("fl_line/validate_annotation.html.j2", annotation_error=problem)


def alternative_image_selection(
    fl_source_line_id: int, *, config: anki_note_tooling.config.Config, db: sqlalchemy.Engine
):
    """
    The images offered for one line, chosen by its source's `[source.images]` block.

    A source with no such block gets an empty list rather than a `NotImplementedError`,
    and the details page hides the button for it.
    """
    with db.connect() as conn:
        line = anki_note_tooling.data.queries.get_single_fl_line(conn, fl_source_line_id)
        source = config.notes.sources.get(line.source_type)
        if source is None:
            images: list[anki_note_tooling.data.types.ImagePoolEntry] = []
        else:
            source_type_id = anki_note_tooling.data.queries.get_or_create_source_type(
                conn, line.source_type
            )
            images = anki_note_tooling.images.pool.select(
                conn,
                source=source,
                source_type_id=source_type_id,
                line_variables={
                    "character_name": line.misc.character_name,
                    "source_tag": line.misc.source_tag,
                    "language": source.language,
                },
                current_image=line.image_path,
            )

        # A user pointing anki_note_tooling at thousands of images should not get all of them at
        # once; the filter box in the template narrows what this returns.
        name_filter = (flask.request.args.get("filter") or "").strip().lower()
        if name_filter:
            images = [
                image
                for image in images
                if name_filter in str(image.image).lower()
                or name_filter in (image.label or "").lower()
                or name_filter in (image.key or "").lower()
            ]
        limit = int(flask.request.args.get("limit") or IMAGE_PICKER_LIMIT)

        return flask.render_template(
            "fl_line_alternative_image_selection.html.j2",
            images=images[:limit],
            total=len(images),
            limit=limit,
            name_filter=name_filter,
            source_id=fl_source_line_id,
        )


def form_image_override(*, config: anki_note_tooling.config.Config, db: sqlalchemy.Engine):
    image_file_override = _posted_media(flask.request.args.get("image-file-override"))
    source_id = flask.request.args.get("source_id")
    return flask.render_template(
        "fl_line_form_image_override.html.j2",
        image_file_override=image_file_override,
        source_id=source_id,
    )


#: How many thumbnails the picker shows before asking the user to narrow the search.
IMAGE_PICKER_LIMIT = 60


class FileCategory(StrEnum):
    AudioFile = "audio-file"
    ImageFile = "image-file"


def upload_tmp_file(fl_source_line_id: str, *, config: anki_note_tooling.config.Config):
    file_category = FileCategory(flask.request.args["file_category"])

    if "tmp-file" not in flask.request.files:
        raise Exception

    tmp_file: werkzeug.datastructures.FileStorage | None = flask.request.files["tmp-file"]
    # file_data = image_file.read()
    assert tmp_file is not None
    assert tmp_file.filename is not None
    secure_filename = werkzeug.utils.secure_filename(tmp_file.filename)
    if secure_filename.find(".") == -1:
        # handles case where everything prior to the suffix is deemed unsafe
        secure_filename = f"a.{secure_filename}"
    print(f"tmp_file.filename = {tmp_file.filename}, secure_filename = {secure_filename}")
    # _dummy_path = config.fs.managed_language_learning_files / secure_filename
    # assert _dummy_path.suffix in (".png", ".jpeg", ".jpg")
    new_path = anki_note_tooling.lib.utils.get_unique_path(
        dir=config.fs.tmp_files, filename=secure_filename
    )  # _dummy_path.parent / f"{_dummy_path.stem}-{uuid.uuid4()}{_dummy_path.suffix}"
    if file_category == FileCategory.ImageFile and new_path.suffix not in (".png", ".jpeg", ".jpg"):
        raise ValueError(f"Invalid suffix for {new_path.name}")
    elif file_category == FileCategory.AudioFile and new_path.suffix not in (".wav", ".mp3"):
        raise ValueError(f"Invalid suffix for {new_path.name}")

    # Need to:
    # (1) save file
    tmp_file.save(new_path)
    tmp_file.close()
    # (2) put entry in fl_image table with matching id

    return flask.render_template(
        "fl_line/upload_tmp_file.html.j2",
        file_category=file_category,
        file_path=config.fs.media.store(new_path),
        source_id=fl_source_line_id,
    )


def _rejected_update(
    conn: sqlalchemy.Connection,
    line: anki_note_tooling.data.types.StoredFlLine,
    *,
    config: anki_note_tooling.config.Config,
    annotated_text: str,
    image: str,
    audio: str,
    problem: str,
) -> tuple[str, int]:
    """
    The details page as the user left it, with the error beside the annotation.

    Everything they submitted comes back — the annotation in an open editor, and the image
    and audio they picked still selected — so one corrected save applies the whole change
    they were making, rather than only the part that survived a round trip.
    """
    context = _details_context(conn, line, config=config)
    context |= {
        "annotated_text": annotated_text,
        "image": image,
        "audio": audio,
        "annotation_error": problem,
        "editing_annotation": True,
    }
    return flask.render_template("fl_line_details.html.j2", **context), 400


def _posted_media(value: str | None) -> anki_note_tooling.lib.media.MediaRef | None:
    """
    The media location a request field holds, or None when the field is empty.

    A value that is absolute, or that climbs out of the media root with `..`, is a 400 rather
    than a re-rendered form: these fields are filled in by the image picker and the upload
    view, so anything else is a malformed request and not a mistake the user can correct in
    the page.
    """
    if value is None:
        return None
    try:
        return anki_note_tooling.lib.media.MediaRef.parse(value)
    except anki_note_tooling.lib.media.MediaPathError as e:
        flask.abort(400, description=str(e))


def _moved_out_of_tmp(
    path: Path,
    *,
    config: anki_note_tooling.config.Config,
    moves: list[tuple[Path, Path]],
) -> Path:
    """
    Where a picked file should live, moving it out of `tmp_files` if that is where it sits.

    Every move is recorded in `moves` so that a later failure can put the file back.
    """
    if not path.is_relative_to(config.fs.tmp_files):
        return path
    destination = config.fs.managed_language_learning_files / path.name
    path.rename(destination)
    moves.append((path, destination))
    return destination


def update_line(
    fl_source_line_id: int, *, config: anki_note_tooling.config.Config, db: sqlalchemy.Engine
):
    form = flask.request.form
    annotation_entry = form.get(ANNOTATION_FORM_PARAM, "").strip()
    form_image_file = form.get("update-form-image-file", "").strip() or None
    form_audio_file = form.get("update-form-audio-file", "").strip() or None
    # Parsing the posted locations up front is what confines them to the media root: one that
    # is absolute, or that climbs out with `..`, is rejected before it is joined onto anything.
    form_image = _posted_media(form_image_file)
    form_audio = _posted_media(form_audio_file)

    successful_file_moves: list[tuple[Path, Path]] = []
    try:
        with db.connect() as conn:
            line = anki_note_tooling.data.queries.get_single_fl_line(conn, fl_source_line_id)
            # Annotation, image and audio all live on the fl_line row now, so the three
            # separate upserts collapse into one update built up across the branches below.
            changes: dict[str, Any] = {}

            if annotation_entry and annotation_entry != line.annotated_text:
                # Checked before anything is moved or written, so a rejected annotation
                # leaves nothing behind to undo.
                problem = _annotation_problem(
                    annotation_entry,
                    source=config.notes.sources.get(line.source_type),
                    source_type=line.source_type,
                )
                if problem is not None:
                    return _rejected_update(
                        conn,
                        line,
                        config=config,
                        annotated_text=annotation_entry,
                        image=form_image_file or "",
                        audio=form_audio_file or "",
                        problem=problem,
                    )

                changes |= anki_note_tooling.data.inserts._annotation_record(annotation_entry)

            if form_image is not None and form_image != line.image_path:
                final_image_path = _moved_out_of_tmp(
                    config.fs.media.locate(form_image), config=config, moves=successful_file_moves
                )
                changes |= anki_note_tooling.data.inserts._image_record(
                    config.fs.media.store(final_image_path)
                )

            existing_audio = line.audio.path if line.audio is not None else None
            if form_audio is not None and form_audio != existing_audio:
                final_audio_path = _moved_out_of_tmp(
                    config.fs.media.locate(form_audio), config=config, moves=successful_file_moves
                )
                changes |= anki_note_tooling.data.inserts._audio_record(
                    anki_note_tooling.lib.types.AudioFile(
                        path=config.fs.media.store(final_audio_path)
                    )
                )

            if changes:
                anki_note_tooling.data.inserts.update_fl_line(
                    conn, fl_source_line_id, changes=changes
                )

            anki_note_tooling.data.queries.mark_not_up_to_date_anki_notes(conn, [fl_source_line_id])

            conn.commit()
    except Exception as e:
        for original, new in successful_file_moves:
            new.rename(original)
        raise e

    return flask.redirect(f"/fl_line_details/{fl_source_line_id}")
