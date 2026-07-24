from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from run_swebench16 import (  # noqa: E402
    DEFAULT_CASES_FILE,
    DEFAULT_LLM_CONFIG,
    DEFAULT_OUTPUT_ROOT,
    EXPERIMENT_CONFIGS,
    build_batch_command,
    load_cases,
)


class SweBench16RunnerTests(unittest.TestCase):
    def test_fixed_case_list_has_16_unique_existing_tasks(self) -> None:
        cases = load_cases(DEFAULT_CASES_FILE, 16)

        self.assertEqual(len(cases), 16)
        self.assertEqual(len({case_id for case_id, _ in cases}), 16)
        self.assertTrue(
            all(name.startswith("swe-bench/") for _, name in cases)
        )

    def test_formal_configs_keep_prompts_and_upstream_method_horizons(
        self,
    ) -> None:
        for method, path in EXPERIMENT_CONFIGS.items():
            with self.subTest(method=method, path=path):
                config = yaml.safe_load(path.read_text(encoding="utf-8"))
                agent = config.get("agent", {})
                model = config.get("model", {})
                if method == "agentdiet":
                    self.assertEqual(agent.get("step_limit"), 50)
                else:
                    self.assertNotIn("step_limit", agent)
                self.assertNotIn("system_template", agent)
                self.assertNotIn("instance_template", agent)
                self.assertNotIn("observation_template", model)

    def test_command_selects_16_tasks_and_concurrency_8(self) -> None:
        run_id = "runner-unit-test-do-not-run"
        command, _, cases, job_dir = build_batch_command(
            "agentdiet",
            run_id=run_id,
            n_tasks=16,
            n_concurrent=8,
            cases_file=DEFAULT_CASES_FILE,
            llm_config=DEFAULT_LLM_CONFIG,
            output_root=DEFAULT_OUTPUT_ROOT,
        )

        self.assertEqual(command[command.index("-n") + 1], "8")
        self.assertEqual(
            command[command.index("--n-tasks") + 1],
            "16",
        )
        self.assertEqual(command.count("--include-task-name"), 16)
        self.assertEqual(len(cases), 16)
        selected = [
            command[index + 1]
            for index, value in enumerate(command)
            if value == "--include-task-name"
        ]
        self.assertEqual(selected, [case_id for case_id, _ in cases])
        self.assertEqual(job_dir.parent, DEFAULT_OUTPUT_ROOT / "agentdiet")
        self.assertIn(
            "config_file="
            + str(EXPERIMENT_CONFIGS["agentdiet"]),
            command,
        )


if __name__ == "__main__":
    unittest.main()
