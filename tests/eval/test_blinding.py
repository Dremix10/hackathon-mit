"""Blinding grep and banned-term lint."""

from coop.eval.blinding import blinding_problems
from coop.eval.fixtures import write_catalog
from coop.eval.lint import lint_events


def test_catalog_directories_are_blind(tmp_path):
    write_catalog(tmp_path)
    for child in sorted(path for path in tmp_path.iterdir() if path.is_dir()):
        assert blinding_problems(child) == [], child.name


def test_arm_substrings_and_quoted_null(tmp_path):
    leaked = tmp_path / "loyalty"
    leaked.mkdir()
    (leaked / "meta.json").write_text("{}\n", encoding="utf-8")
    problems = blinding_problems(leaked)
    assert any("directory name" in item for item in problems)

    clean = tmp_path / "c-99"
    clean.mkdir()
    (clean / "meta.json").write_text(
        '{"temperature": null, "insider_id": null}\n',
        encoding="utf-8",
    )
    assert blinding_problems(clean) == []
    (clean / "events.jsonl").write_text('{"note": "null"}\n', encoding="utf-8")
    assert any('"null"' in item or "null" in item for item in blinding_problems(clean))


def test_template_id_is_allowed_only_as_a_value(tmp_path):
    run = tmp_path / "c-20"
    run.mkdir()
    template_id = "abcdef0123"
    (run / "sealed.json").write_text(
        '{"template_map": {"abcdef0123": {"step": "S3.1"}}}\n',
        encoding="utf-8",
    )
    digest = "00" + template_id + ("ab" * 26)
    (run / "events.jsonl").write_text(
        '{"payload": {"template_id": "abcdef0123", "hash": "sha256:%s"}}\n' % digest,
        encoding="utf-8",
    )
    assert blinding_problems(run) == []
    (run / "notes.txt").write_text("see abcdef0123 in the log\n", encoding="utf-8")
    assert any("template_map key" in item for item in blinding_problems(run))


def test_banned_term_lint_is_case_insensitive_and_skips_structural_fields():
    events = [
        {"payload": {"text": "This is an Experiment."}},
        {"payload": {"slot": "insider", "kind": "loyalty", "task_id": "T3-07"}},
        {"payload": {"text": "The unit tests passed on T3-07."}},
    ]
    hits = lint_events(events)
    assert any("experiment" in item.casefold() for item in hits)
    assert len(hits) == 1
