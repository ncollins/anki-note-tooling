"""Loading source and recipe TOML, and the checks that catch a config that cannot work."""

import pytest

import anki_note_tooling.config
import anki_note_tooling.notes.config

EXAMPLE_CONFIG_DIR = anki_note_tooling.config.EXAMPLE_CONFIG_DIR


def test_the_shipped_example_config_loads_and_binds():
    config = anki_note_tooling.notes.config.load(EXAMPLE_CONFIG_DIR)
    assert sorted(config.sources) == ["drama", "podcast"]
    assert sorted(config.recipes) == ["cloze", "phrase"]
    assert [r.name for r in config.recipes_for("podcast")] == ["cloze", "phrase"]
    assert anki_note_tooling.notes.config.check_bindings(config) == []


def test_a_missing_config_directory_is_empty_rather_than_an_error(tmp_path):
    """Someone will run anki_note_tooling before writing any TOML; that is not a crash."""
    config = anki_note_tooling.notes.config.load(tmp_path)
    assert config.sources == {} and config.recipes == {}


def test_recipes_for_an_undefined_source_is_reportable(tmp_path):
    config = anki_note_tooling.notes.config.load(tmp_path)
    with pytest.raises(
        anki_note_tooling.notes.config.SourceConfigError, match="no source definition"
    ):
        config.recipes_for("cantonese_drama")


def test_a_binding_to_a_recipe_that_does_not_exist_is_caught(tmp_path):
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "s.toml").write_text(
        '[source]\nname = "s"\nlanguage = "ja"\nslots = ["kanji"]\nnotes = ["nope"]\n'
    )
    with pytest.raises(anki_note_tooling.notes.config.SourceConfigError, match="does not exist"):
        anki_note_tooling.notes.config.load(tmp_path)


def test_inactive_is_rejected_for_a_whole_line_recipe():
    with pytest.raises(ValueError, match="every substitution is active"):
        anki_note_tooling.notes.config.Recipe(
            name="c",
            deck="d",
            model="m",
            for_each="line",
            active="$kanji",
            inactive="$kanji",
            fields={"F": "$note_text"},
        )


def test_slots_may_not_shadow_a_built_in_variable():
    with pytest.raises(ValueError, match="shadow built-in variables"):
        anki_note_tooling.notes.config.Source(name="s", language="ja", slots=("kanji", "audio"))


def test_default_group_by_counts_as_a_required_slot():
    """
    Otherwise a recipe grouping on a slot no bound source declares would pass validation
    and then fail at render time.
    """
    recipe = anki_note_tooling.notes.config.Recipe(
        name="p",
        deck="d",
        model="m",
        for_each="substitution",
        active="$kanji",
        default_group_by="romanji",
        fields={"F": "$note_text"},
    )
    assert recipe.required_slots() == {"kanji", "romanji"}


def test_unconfigured_source_names_reports_only_the_ones_with_no_definition():
    config = anki_note_tooling.notes.config.load(EXAMPLE_CONFIG_DIR)
    assert anki_note_tooling.notes.config.unconfigured_source_names(
        config, ["podcast", "new_source"]
    ) == ["new_source"]


def test_source_for_an_unknown_name_reports_the_ones_that_are_defined():
    config = anki_note_tooling.notes.config.load(EXAMPLE_CONFIG_DIR)
    with pytest.raises(anki_note_tooling.notes.config.SourceConfigError, match="podcast"):
        config.source_for("not_a_source")
