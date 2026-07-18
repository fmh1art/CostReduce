from pathlib import Path

from scripts.select_evolve_cases import codebase_key, select_cases
from scripts.select_eval_cases_from_baseline import extract_case_ids


def _task(root: Path, name: str, toml: str = "") -> Path:
    path = root / name
    path.mkdir()
    (path / "task.toml").write_text(toml or "[task]\nname='x'\n", encoding="utf-8")
    return path


def test_swebench_selection_covers_codebases_before_repeating(tmp_path: Path):
    for repo in ("astropy__astropy", "django__django", "sympy__sympy"):
        for number in range(4):
            _task(tmp_path, f"{repo}-{number}")
    selected, membership = select_cases(tmp_path, "swebench", 5)
    selected_groups = [membership[item] for item in selected]
    assert len(set(selected_groups[:3])) == 3
    assert set(selected_groups) == {"astropy/astropy", "django/django", "sympy/sympy"}
    assert selected == select_cases(tmp_path, "swebench", 5)[0]


def test_repository_url_takes_precedence_for_deep_swe(tmp_path: Path):
    task = _task(tmp_path, "opaque-task", """
[task]
name = "opaque"
[metadata]
repository_url = "https://github.com/example/project.git"
""")
    assert codebase_key("deep-swe", task) == "example/project"


def test_dab_groups_by_dataset(tmp_path: Path):
    for name in ("dab__a__query1", "dab__a__query2", "dab__b__query1"):
        _task(tmp_path, name)
    selected, membership = select_cases(tmp_path, "dab", 2)
    assert {membership[item] for item in selected} == {"dataset:a", "dataset:b"}


def test_eval_selection_excludes_every_evolve_case(tmp_path: Path):
    for repo in ("astropy__astropy", "django__django", "sympy__sympy"):
        for number in range(30):
            _task(tmp_path, f"{repo}-{number}")
    evolve, _ = select_cases(tmp_path, "swebench", 16)
    evaluate, _ = select_cases(tmp_path, "swebench", 64, set(evolve))
    assert len(evaluate) == 64
    assert set(evolve).isdisjoint(evaluate)


def test_final_eval_cases_are_extracted_from_no_evolve_configs(tmp_path: Path):
    run = tmp_path / "results" / "no_evolve" / "swebench-verified" / "noevolve-swebench-run"
    expected = ["django__django-100", "sympy__sympy-200"]
    for index, case_id in enumerate(reversed(expected)):
        trial = run / f"trial-{index}"
        trial.mkdir(parents=True)
        (trial / "config.json").write_text(
            '{"task":{"path":"/tasks/' + case_id + '"}}\n', encoding="utf-8"
        )
    assert set(extract_case_ids(run)) == set(expected)
