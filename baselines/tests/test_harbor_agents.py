from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "fixed_adapters"),
    str(ROOT),
    str(ROOT / "trajectory_reduction" / "harbor_agent"),
    str(ROOT / "zipact"),
    str(ROOT / "zipact" / "harbor_agent"),
    str(ROOT / "eet" / "harbor_agent"),
]

from agentdiet_harbor.agent import AgentDietHarborAgent  # noqa: E402
from eet_harbor.agent import EETHarborAgent  # noqa: E402
from zipact_harbor.agent import (  # noqa: E402
    ZipActHarborAgent,
    _initializer_prompt,
)


BASE_CONFIG = {
    "system_template": "MINI SYSTEM",
    "instance_template": "MINI TASK: {{task}}",
    "step_limit": 0,
    "cost_limit": 0.0,
}


class FakeModel:
    def __init__(self) -> None:
        self.config = SimpleNamespace(
            model_name="openai/fake",
            model_kwargs={},
        )
        self.queries: list[list[dict]] = []
        self.counter = 0

    def query(self, messages: list[dict]) -> dict:
        self.queries.append(messages)
        self.counter += 1
        return {
            "role": "assistant",
            "content": f"reasoning-{self.counter}",
            "tool_calls": [
                {
                    "id": f"call-{self.counter}",
                    "type": "function",
                    "function": {
                        "name": "bash",
                        "arguments": json.dumps({"command": "printf observation"}),
                    },
                }
            ],
            "extra": {
                "actions": [
                    {
                        "command": "printf observation",
                        "tool_call_id": f"call-{self.counter}",
                    }
                ],
                "cost": 0.0,
                "response": {
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                    }
                },
            },
        }

    @staticmethod
    def format_message(**kwargs) -> dict:
        return kwargs

    @staticmethod
    def format_observation_messages(
        message: dict, outputs: list[dict], template_vars: dict | None = None
    ) -> list[dict]:
        actions = message["extra"]["actions"]
        return [
            {
                "role": "tool",
                "tool_call_id": action["tool_call_id"],
                "content": output["output"],
                "extra": {
                    "raw_output": output["output"],
                    "returncode": output["returncode"],
                },
            }
            for action, output in zip(actions, outputs)
        ]

    @staticmethod
    def get_template_vars() -> dict:
        return {}

    @staticmethod
    def serialize() -> dict:
        return {"info": {"config": {"model": {}}}}


class FakeEnvironment:
    def __init__(self, output: str = "observation") -> None:
        self.output = output

    def execute(self, action: dict) -> dict:
        return {
            "output": self.output,
            "returncode": 0,
            "exception_info": "",
        }

    @staticmethod
    def get_template_vars() -> dict:
        return {}

    @staticmethod
    def serialize() -> dict:
        return {"info": {"config": {"environment": {}}}}


def internal_record(
    content: str,
    *,
    finish_reason: str = "stop",
) -> dict:
    return {
        "role": "assistant",
        "content": content,
        "extra": {
            "response": {
                "choices": [
                    {
                        "finish_reason": finish_reason,
                        "message": {"content": content},
                    }
                ],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 3,
                }
            },
            "cost": 0.0,
        },
    }


class ZipActTests(unittest.TestCase):
    def test_initializer_does_not_duplicate_long_task_in_json_schema(self) -> None:
        instruction = "unique-long-task-marker " * 200
        rendered = "\n".join(
            str(message["content"])
            for message in _initializer_prompt(instruction)
        )

        # The instruction appears once as input. It is not copied into the
        # requested state schema through global_instruction/current_objective.
        self.assertEqual(rendered.count(instruction), 1)
        self.assertIn("Inspect the workspace", rendered)

    def test_actor_uses_state_and_latest_observation_not_full_history(self) -> None:
        model = FakeModel()
        agent = ZipActHarborAgent(model, FakeEnvironment(), **BASE_CONFIG)
        agent.extra_template_vars = {"task": "fix the issue"}
        agent.messages = [
            {"role": "system", "content": "MINI SYSTEM"},
            {"role": "user", "content": "MINI TASK: fix the issue"},
        ]
        initial = json.dumps(
            {
                "goal_state": {
                    "global_instruction": "fix the issue",
                    "sub_goal_queue": ["inspect"],
                    "current_objective": "inspect",
                },
                "world_state": {
                    "location": "/app",
                    "inventory": [],
                    "entity_map": {},
                },
                "constraint_state": {
                    "negative_constraints": [],
                    "visited_locations": [],
                },
            }
        )
        updated = json.dumps(
            {
                "goal_state": {
                    "global_instruction": "fix the issue",
                    "sub_goal_queue": ["edit"],
                    "current_objective": "edit",
                },
                "world_state": {
                    "location": "/app",
                    "inventory": [],
                    "entity_map": {"README": "inspected"},
                },
                "constraint_state": {
                    "negative_constraints": [],
                    "visited_locations": ["/app"],
                },
            }
        )
        with patch(
            "zipact_harbor.agent.plain_completion",
            side_effect=[
                (initial, internal_record(initial), 0.0),
                (updated, internal_record(updated), 0.0),
            ],
        ):
            first = agent.query()
            agent.execute_actions(first)
            agent.query()

        second_context = model.queries[-1]
        rendered = "\n".join(str(message["content"]) for message in second_context)
        self.assertIn("ZipAct State Table", rendered)
        self.assertIn("README", rendered)
        self.assertIn("observation", rendered)
        self.assertNotIn("reasoning-1", rendered)
        self.assertNotIn("Recent Actions", rendered)
        # The full immutable task is provided by mini-swe's instance prompt,
        # not duplicated inside every state JSON.
        self.assertEqual(rendered.count("fix the issue"), 1)

    def test_original_fifty_step_episode_cap_is_enforced(self) -> None:
        model = FakeModel()
        agent = ZipActHarborAgent(
            model,
            FakeEnvironment(),
            **BASE_CONFIG,
            zipact_max_steps=1,
        )
        agent.extra_template_vars = {"task": "fix the issue"}
        agent.messages = [
            {"role": "system", "content": "MINI SYSTEM"},
            {"role": "user", "content": "MINI TASK: fix the issue"},
        ]
        initial = json.dumps(
            {
                "goal_state": {
                    "sub_goal_queue": ["inspect"],
                    "current_objective": "inspect",
                }
            }
        )
        with patch(
            "zipact_harbor.agent.plain_completion",
            return_value=(initial, internal_record(initial), 0.0),
        ):
            agent.query()
            with self.assertRaises(Exception) as context:
                agent.query()
        self.assertEqual(
            context.exception.__class__.__name__,
            "LimitsExceeded",
        )

    def test_serialization_does_not_pollute_live_actor_messages(self) -> None:
        agent = ZipActHarborAgent(FakeModel(), FakeEnvironment(), **BASE_CONFIG)
        agent.messages = [
            {"role": "system", "content": "MINI SYSTEM"},
            {"role": "exit", "content": "done", "extra": {}},
        ]
        agent._internal_records = [internal_record("updater")]
        before = json.loads(json.dumps(agent.messages))

        serialized = agent.serialize()

        self.assertEqual(agent.messages, before)
        self.assertEqual(len(serialized["messages"]), len(before) + 1)


class AgentDietTests(unittest.TestCase):
    def test_long_step_is_replaced_after_accepted_compression(self) -> None:
        model = FakeModel()
        agent = AgentDietHarborAgent(
            model,
            FakeEnvironment("verbose " * 200),
            **BASE_CONFIG,
            reduction_context_before=0,
            reduction_context_after=0,
            reduction_threshold_tokens=1,
        )
        agent.messages = [
            {"role": "system", "content": "MINI SYSTEM"},
            {"role": "user", "content": "MINI TASK"},
        ]
        message = model.query(agent.messages)
        agent.add_messages(message)
        with patch(
            "agentdiet_harbor.agent.plain_completion",
            return_value=(
                "short",
                internal_record("short"),
                0.0,
            ),
        ):
            agent.execute_actions(message)

        self.assertTrue(agent._steps[0].compressed)
        self.assertTrue(
            any(
                item.get("extra", {}).get("agentdiet_compressed")
                for item in agent.messages
            )
        )

    def test_empty_length_truncated_compression_never_erases_step(self) -> None:
        model = FakeModel()
        agent = AgentDietHarborAgent(
            model,
            FakeEnvironment("verbose " * 200),
            **BASE_CONFIG,
            reduction_context_before=0,
            reduction_context_after=0,
            reduction_threshold_tokens=1,
        )
        agent.messages = [
            {"role": "system", "content": "MINI SYSTEM"},
            {"role": "user", "content": "MINI TASK"},
        ]
        message = model.query(agent.messages)
        agent.add_messages(message)
        with patch(
            "agentdiet_harbor.agent.plain_completion",
            return_value=(
                "",
                internal_record("", finish_reason="length"),
                0.0,
            ),
        ):
            agent.execute_actions(message)

        self.assertFalse(agent._steps[0].compressed)
        self.assertEqual(agent._metrics["invalid_analysis_count"], 1)
        self.assertEqual(agent._metrics["empty_analysis_count"], 1)
        self.assertEqual(
            agent._metrics["length_truncated_analysis_count"],
            1,
        )

    def test_serialization_does_not_pollute_live_agent_messages(self) -> None:
        agent = AgentDietHarborAgent(
            FakeModel(), FakeEnvironment(), **BASE_CONFIG
        )
        agent.messages = [
            {"role": "system", "content": "MINI SYSTEM"},
            {"role": "exit", "content": "done", "extra": {}},
        ]
        agent._internal_records = [internal_record("compressor")]
        before = json.loads(json.dumps(agent.messages))

        serialized = agent.serialize()

        self.assertEqual(agent.messages, before)
        self.assertEqual(len(serialized["messages"]), len(before) + 1)


class EETTests(unittest.TestCase):
    def test_loads_and_queries_official_experience_store(self) -> None:
        store = (
            ROOT
            / "eet"
            / "mini-swe-agent"
            / "src"
            / "minisweagent"
            / "experience"
            / "extracted_experiences_summarized_gpt_5_mini.jsonl"
        )
        agent = EETHarborAgent(
            FakeModel(),
            FakeEnvironment(),
            **BASE_CONFIG,
            experience_store_path=str(store),
            experience_min_similarity=0.0,
        )
        retrieved = agent._retrieve(
            "Fix a Django ORM query compiler bug and run regression tests."
        )
        self.assertEqual(len(retrieved), 1)
        self.assertGreaterEqual(retrieved[0][1], 0.0)

    def test_threshold_score_adds_standard_submission_control(self) -> None:
        model = FakeModel()
        agent = EETHarborAgent(model, FakeEnvironment(), **BASE_CONFIG)
        agent._uses_experience = True
        message = model.query([])
        message["content"] = "CONFIDENCE_SCORE: 81"
        message["extra"]["actions"][0]["command"] = "git diff"

        observations = agent.execute_actions(message)

        self.assertEqual(agent._progress["confidence_score"], 81)
        self.assertEqual(agent._progress["last_confidence_check"], 1)
        self.assertIn(
            "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
            observations[-1]["content"],
        )
        self.assertTrue(observations[-1]["extra"]["eet_control_prompt"])

    def test_method_configs_do_not_override_mini_prompt_templates(self) -> None:
        import yaml

        configs = [
            ROOT / "trajectory_reduction" / "harbor_config.yaml",
            ROOT / "zipact" / "harbor_config.yaml",
            ROOT / "eet" / "harbor_config.yaml",
        ]
        for path in configs:
            with self.subTest(path=path):
                config = yaml.safe_load(path.read_text())
                self.assertNotIn("system_template", config.get("agent", {}))
                self.assertNotIn("instance_template", config.get("agent", {}))
                self.assertNotIn("observation_template", config.get("model", {}))
                self.assertEqual(config["agent"]["step_limit"], 12)


if __name__ == "__main__":
    unittest.main()
