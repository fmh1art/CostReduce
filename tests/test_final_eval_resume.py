import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.final_eval_resume import (
    MANIFEST_NAME,
    STATE_DIR_NAME,
    ResumeStateError,
    build_identity,
    cleanup_incomplete_trials,
    prepare_resume,
)


def _inputs(tmp_path: Path, *, case_ids=("case-a", "case-b")) -> dict:
    work_dir = tmp_path / "work"
    results_parent = tmp_path / "results" / "eval" / "swebench"
    cases_file = work_dir / "final_eval_cases.txt"
    llm_config = tmp_path / "llm.yaml"
    scripts_dir = work_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    cases_file.write_text("\n".join(case_ids) + "\n", encoding="utf-8")
    llm_config.write_text(
        "llm_name: test-model\napi_key: secret-must-not-enter-manifest\n",
        encoding="utf-8",
    )
    (scripts_dir / "tools.json").write_text("{}\n", encoding="utf-8")
    (scripts_dir / "executor.py").write_text("print('ok')\n", encoding="utf-8")
    (scripts_dir / "instruction.md").write_text("Use the tools.\n", encoding="utf-8")
    (scripts_dir / ".evolve_tools_v6_config.yaml").write_text(
        "model:\n  model_name: test-model\n", encoding="utf-8"
    )
    identity = build_identity(
        benchmark="swebench",
        eval_scope="all_including_evolve",
        cases_file=cases_file,
        expected_case_count=len(case_ids),
        llm_config=llm_config,
        scripts_dir=scripts_dir,
        results_parent=results_parent,
        n_concurrent=4,
        runner="run_swe_bench.sh",
        extra_identity={"task_env": "SWEBENCH_TASK_PATH"},
    )
    return {
        "work_dir": work_dir,
        "results_parent": results_parent,
        "cases_file": cases_file,
        "llm_config": llm_config,
        "scripts_dir": scripts_dir,
        "identity": identity,
    }


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def test_resume_reuses_dated_run_id_and_reruns_only_incomplete_trials(tmp_path):
    inputs = _inputs(tmp_path)
    first_run_id = "evolve-coat-swebench-0719-201500"
    first = prepare_resume(
        work_dir=inputs["work_dir"],
        results_parent=inputs["results_parent"],
        proposed_run_id=first_run_id,
        identity=inputs["identity"],
    )

    assert first["resumed"] is False
    assert first["run_id"] == first_run_id
    job_dir = Path(first["final_eval_dir"])

    completed = job_dir / "case-a__agent"
    _write_json(completed / "config.json", {"task": {"name": "case-a"}})
    _write_json(completed / "result.json", {"trial_name": "case-a__agent"})

    interrupted = job_dir / "case-b__agent"
    _write_json(interrupted / "config.json", {"task": {"name": "case-b"}})
    (interrupted / "agent").mkdir()
    (interrupted / "trial.log").write_text("partial\n", encoding="utf-8")

    truncated = job_dir / "case-c__agent"
    _write_json(truncated / "config.json", {"task": {"name": "case-c"}})
    (truncated / "result.json").write_text('{"trial_name":', encoding="utf-8")

    metadata = job_dir / ".critiques"
    metadata.mkdir(parents=True)
    (metadata / "notes.json").write_text("{}\n", encoding="utf-8")

    second = prepare_resume(
        work_dir=inputs["work_dir"],
        results_parent=inputs["results_parent"],
        proposed_run_id="evolve-coat-swebench-0719-211111",
        identity=inputs["identity"],
    )

    assert second["resumed"] is True
    assert second["run_id"] == first_run_id
    assert second["completed_trials"] == 1
    assert second["removed_incomplete_trials"] == [
        "case-b__agent",
        "case-c__agent",
    ]
    assert completed.is_dir()
    assert not interrupted.exists()
    assert not truncated.exists()
    assert metadata.is_dir()

    manifest_path = inputs["work_dir"] / STATE_DIR_NAME / MANIFEST_NAME
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert manifest["run_id"] == first_run_id
    assert manifest["resume_count"] == 1
    assert "secret-must-not-enter-manifest" not in manifest_text


def test_changed_llm_config_refuses_resume_before_cleanup(tmp_path):
    inputs = _inputs(tmp_path)
    prepare_resume(
        work_dir=inputs["work_dir"],
        results_parent=inputs["results_parent"],
        proposed_run_id="evolve-coat-swebench-0719-201500",
        identity=inputs["identity"],
    )
    job_dir = inputs["results_parent"] / "evolve-coat-swebench-0719-201500"
    interrupted = job_dir / "case-b__agent"
    _write_json(interrupted / "config.json", {"task": {"name": "case-b"}})

    inputs["llm_config"].write_text(
        "llm_name: a-different-model\napi_key: replacement\n", encoding="utf-8"
    )
    changed_identity = build_identity(
        benchmark="swebench",
        eval_scope="all_including_evolve",
        cases_file=inputs["cases_file"],
        expected_case_count=2,
        llm_config=inputs["llm_config"],
        scripts_dir=inputs["scripts_dir"],
        results_parent=inputs["results_parent"],
        n_concurrent=4,
        runner="run_swe_bench.sh",
        extra_identity={"task_env": "SWEBENCH_TASK_PATH"},
    )

    with pytest.raises(ResumeStateError, match="llm_config_sha256"):
        prepare_resume(
            work_dir=inputs["work_dir"],
            results_parent=inputs["results_parent"],
            proposed_run_id="evolve-coat-swebench-0719-211111",
            identity=changed_identity,
        )
    assert interrupted.is_dir()


def test_cleanup_preserves_non_trial_directories_and_complete_trials(tmp_path):
    job_dir = tmp_path / "job"
    complete = job_dir / "complete"
    _write_json(complete / "config.json", {})
    _write_json(complete / "result.json", {})
    unrelated = job_dir / "reports"
    unrelated.mkdir(parents=True)
    (unrelated / "summary.txt").write_text("keep\n", encoding="utf-8")
    partial = job_dir / "partial"
    (partial / "artifacts").mkdir(parents=True)

    assert cleanup_incomplete_trials(job_dir) == ["partial"]
    assert complete.exists()
    assert unrelated.exists()
    assert not partial.exists()


def test_prepare_cli_emits_machine_readable_resume_contract(tmp_path):
    inputs = _inputs(tmp_path, case_ids=("only-case",))
    helper = Path(__file__).parents[1] / "scripts" / "final_eval_resume.py"
    command = [
        sys.executable,
        str(helper),
        "prepare",
        "--work-dir",
        str(inputs["work_dir"]),
        "--results-parent",
        str(inputs["results_parent"]),
        "--proposed-run-id",
        "evolve-coat-swebench-0719-201500",
        "--benchmark",
        "swebench",
        "--eval-scope",
        "all_including_evolve",
        "--cases-file",
        str(inputs["cases_file"]),
        "--expected-case-count",
        "1",
        "--llm-config",
        str(inputs["llm_config"]),
        "--scripts-dir",
        str(inputs["scripts_dir"]),
        "--n-concurrent",
        "4",
        "--runner",
        "run_swe_bench.sh",
        "--identity",
        "task_env=SWEBENCH_TASK_PATH",
    ]

    first = json.loads(subprocess.check_output(command, text=True))
    command[command.index("evolve-coat-swebench-0719-201500")] = (
        "evolve-coat-swebench-0719-211111"
    )
    second = json.loads(subprocess.check_output(command, text=True))

    assert first["resumed"] is False
    assert second["resumed"] is True
    assert second["run_id"] == first["run_id"]
    assert second["prompt_template_path"] == first["prompt_template_path"]


def test_can_adopt_matching_pre_feature_job_and_its_legacy_prompt_path(tmp_path):
    inputs = _inputs(tmp_path)
    (inputs["work_dir"] / "final_eval_taskdir").mkdir()
    legacy_job = inputs["results_parent"] / "evolve-coat-swebench-0719-185859"
    legacy_prompt = "/tmp/evolve_prompt.OptiHarnessResumeTest987654321"
    _write_json(
        legacy_job / "config.json",
        {
            "job_name": legacy_job.name,
            "jobs_dir": str(inputs["results_parent"].resolve()),
            "n_concurrent_trials": 4,
            "datasets": [
                {
                    "path": str(
                        (inputs["work_dir"] / "final_eval_taskdir").resolve()
                    ),
                    "n_tasks": 2,
                }
            ],
            "agents": [
                {
                    "kwargs": {
                        "config_file": str(
                            (
                                inputs["scripts_dir"]
                                / ".evolve_tools_v6_config.yaml"
                            ).resolve()
                        ),
                        "prompt_template_path": legacy_prompt,
                    }
                }
            ],
        },
    )
    completed = legacy_job / "case-a__agent"
    _write_json(completed / "config.json", {})
    _write_json(completed / "result.json", {})
    interrupted = legacy_job / "case-b__agent"
    _write_json(interrupted / "config.json", {})

    adopted = prepare_resume(
        work_dir=inputs["work_dir"],
        results_parent=inputs["results_parent"],
        proposed_run_id="evolve-coat-swebench-0719-220000",
        identity=inputs["identity"],
        adopt_job_dir=legacy_job,
    )

    assert adopted["resumed"] is False
    assert adopted["run_id"] == legacy_job.name
    assert adopted["final_eval_dir"] == str(legacy_job.resolve())
    assert adopted["prompt_template_path"] == legacy_prompt
    assert adopted["completed_trials"] == 1
    assert adopted["removed_incomplete_trials"] == ["case-b__agent"]
    assert completed.exists()
    assert not interrupted.exists()

    resumed = prepare_resume(
        work_dir=inputs["work_dir"],
        results_parent=inputs["results_parent"],
        proposed_run_id="evolve-coat-swebench-0719-230000",
        identity=inputs["identity"],
    )
    assert resumed["resumed"] is True
    assert resumed["run_id"] == legacy_job.name
    assert resumed["prompt_template_path"] == legacy_prompt
