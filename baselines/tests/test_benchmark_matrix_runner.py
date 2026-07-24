from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from run_benchmark_matrix import (  # noqa: E402
    BENCHMARK_ROOTS,
    BENCHMARK_SAMPLE_LIMITS,
    EXPERIMENT_CONFIGS,
    MODEL_MAX_CONCURRENCY,
    SAMPLE_ROOT,
    _selected_cases,
    _normalize_benchmarks,
    _normalize_methods,
    _write_driver_status,
    build_batch_command,
    ensure_case_manifests,
    materialize_experiment_config,
)


class BenchmarkMatrixRunnerTests(unittest.TestCase):
    def test_benchmarks_have_fixed_full_and_16_samples(self) -> None:
        records = ensure_case_manifests()
        self.assertEqual(
            set(records),
            {
                "deep-swe",
                "swe-bench",
                "dab",
                "terminal-bench-2.1",
                "deveval",
            },
        )
        for benchmark, record in records.items():
            with self.subTest(benchmark=benchmark):
                expected = BENCHMARK_SAMPLE_LIMITS[benchmark]
                self.assertEqual(len(record["cases_full"]), expected)
                self.assertEqual(len(set(record["cases_full"])), expected)
                self.assertEqual(
                    record["cases16"], record["cases_full"][:16]
                )
                self.assertTrue(record["cases_full_path"].is_file())
                self.assertTrue(record["cases16_path"].is_file())
                self.assertTrue(
                    all(
                        (BENCHMARK_ROOTS[benchmark] / case_id / "task.toml")
                        .is_file()
                        for case_id in record["cases_full"]
                    )
                )

    def test_swebench_zipact_sample_matches_previous_fixed_set(self) -> None:
        selected, _ = _selected_cases("swe-bench", 64)
        old = {
            line.strip()
            for line in (
                ROOT / "experiments" / "swebench16_cases.txt"
            ).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertEqual(set(selected[:16]), old)

    def test_model_concurrency_ceilings_match_request(self) -> None:
        self.assertEqual(
            MODEL_MAX_CONCURRENCY,
            {
                "deepseekv4_flash": 8,
                "deepseekv4_pro": 8,
                "doubao_seed2_lite": 6,
                "gpt5_5": 4,
            },
        )

    def test_runtime_config_uses_selected_llm_controls_without_secrets(
        self,
    ) -> None:
        llm_config = ROOT.parent / "_config" / "deepseekv4_flash.yaml"
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "agentdiet.yaml"
            materialize_experiment_config(
                "agentdiet", llm_config, destination
            )
            text = destination.read_text(encoding="utf-8")
            config = yaml.safe_load(text)
            kwargs = config["model"]["model_kwargs"]
            self.assertEqual(kwargs["temperature"], 0.01)
            self.assertNotIn("extra_body", kwargs)
            self.assertNotIn("key:", text)
            self.assertEqual(
                config["agent"]["agent_class"],
                "agentdiet_harbor.agent.AgentDietHarborAgent",
            )

    def test_checked_in_configs_do_not_override_prompts(self) -> None:
        for path in EXPERIMENT_CONFIGS.values():
            with self.subTest(path=path):
                config = yaml.safe_load(path.read_text(encoding="utf-8"))
                self.assertNotIn("system_template", config.get("agent", {}))
                self.assertNotIn("instance_template", config.get("agent", {}))

    def test_runner_accepts_requested_baseline_and_benchmark_subsets(self) -> None:
        self.assertEqual(
            _normalize_methods("agentdiet,zipact"),
            ["agentdiet", "zipact"],
        )
        self.assertEqual(
            _normalize_benchmarks("swe-bench,deep-swe,dab"),
            ["swe-bench", "deep-swe", "dab"],
        )

    def test_subset_status_reports_only_selected_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            status = Path(temporary) / "status.json"
            _write_driver_status(
                status,
                state="running",
                metadata={
                    "benchmark_order": ["swe-bench", "deep-swe", "dab"],
                    "method_order": ["agentdiet", "zipact"],
                },
                jobs=[],
            )
            payload = json.loads(status.read_text(encoding="utf-8"))
        self.assertEqual(payload["jobs_total"], 6)

    def test_sample_artifacts_stay_under_baselines(self) -> None:
        self.assertTrue(SAMPLE_ROOT.is_relative_to(ROOT))

    def test_deveval_matrix_command_sets_verifier_test_dir(self) -> None:
        samples = ensure_case_manifests()
        llm_config = ROOT.parent / "_config" / "deepseekv4_flash.yaml"
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            runtime_config = materialize_experiment_config(
                "agentdiet", llm_config, temp / "agentdiet.yaml"
            )
            command, _, cases, _ = build_batch_command(
                "agentdiet",
                "deveval",
                run_id="deveval-command-test",
                n_concurrent=1,
                llm_config=llm_config,
                runtime_config=runtime_config,
                output_root=temp / "results",
                samples=samples,
            )
        self.assertEqual(len(cases), 63)
        self.assertIn("TEST_DIR=/tests", command)


if __name__ == "__main__":
    unittest.main()
