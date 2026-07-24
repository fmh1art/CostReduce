"""ZipAct's G-W-C loop adapted safely to long Harbor coding tasks."""

from __future__ import annotations

import copy
import json
import time
from typing import Any

from harbor_common_fixed.internal_llm import (
    plain_completion,
    response_finish_reason,
)
from minisweagent.agents.default import AgentConfig, DefaultAgent
from minisweagent.exceptions import LimitsExceeded, TimeExceeded
from zipact.parsing import extract_json_object
from zipact.state import AgentState


class ZipActConfig(AgentConfig):
    # The original ZipAct runner caps an episode at 50 actor steps.
    zipact_max_steps: int = 50
    # Keep upstream's 512-token state-call budget; the Harbor serializer below
    # omits the duplicated immutable task and bounds every state component.
    zipact_initializer_max_tokens: int = 512
    zipact_updater_max_tokens: int = 512


def _bounded_state_payload(state: AgentState) -> dict[str, Any]:
    """Serialize bounded G-W-C state without duplicating the full task."""

    goal = state.goal_state
    world = state.world_state
    constraints = state.constraint_state
    entity_items = list(world.entity_map.items())[-20:]
    return {
        "goal_state": {
            "sub_goal_queue": [
                str(item)[:300] for item in list(goal.sub_goal_queue)[:8]
            ],
            "current_objective": str(goal.current_objective)[:500],
        },
        "world_state": {
            "location": str(world.location)[:500],
            "inventory": [
                str(item)[:300] for item in list(world.inventory)[-20:]
            ],
            "entity_map": {
                str(key)[:200]: str(value)[:500]
                for key, value in entity_items
            },
        },
        "constraint_state": {
            "negative_constraints": [
                str(item)[:500]
                for item in list(constraints.negative_constraints)[-12:]
            ],
            "visited_locations": [
                str(item)[:300]
                for item in list(constraints.visited_locations)[-12:]
            ],
        },
    }


def _fallback_state(instruction: str) -> AgentState:
    """Keep the task immutable while avoiding a full-task state duplicate."""

    state = AgentState.empty(instruction)
    objective = "Inspect the workspace, identify the required change, and plan it."
    state.goal_state.current_objective = objective
    state.goal_state.sub_goal_queue = [objective]
    return state


def _initializer_prompt(
    instruction: str,
) -> list[dict[str, str]]:
    # AgentState.empty uses the full instruction as current_objective.  That is
    # useful as a fallback in the original short-task domains but duplicates a
    # long Harbor issue statement in the requested output schema.
    schema = _bounded_state_payload(_fallback_state(instruction))
    system = (
        "You initialize a compact ZipAct Goal-World-Constraint state for an "
        "LLM agent. Return only valid JSON matching the schema. The global "
        "instruction is immutable and already retained outside this JSON; do "
        "not repeat it. Create a short sub-goal queue and set "
        "current_objective to the first active sub-goal. Keep every string "
        "concise so the complete JSON fits in the output budget."
    )
    user = (
        "Environment: A Harbor coding/data task sandbox. Files, commands, "
        "test outcomes, generated artifacts, and the working directory form "
        "the world state.\n\n"
        f"Instruction:\n{instruction}\n\n"
        "Required JSON schema (global_instruction intentionally omitted):\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _updater_prompt(
    previous_state: AgentState,
    action: str,
    observation: str,
) -> list[dict[str, str]]:
    schema = _bounded_state_payload(previous_state)
    system = (
        "You are the State Updater module of ZipAct. Synthesize the previous "
        "Goal-World-Constraint state, executed action, and latest observation "
        "into refreshed state. Return only valid JSON matching the schema. "
        "The immutable global instruction is retained outside the JSON; do "
        "not repeat it.\n\n"
        "Update protocol:\n"
        "1. If current_objective is complete, remove it from the queue and "
        "advance to the next objective.\n"
        "2. Keep only durable facts needed for future actions: relevant files "
        "or tables, symbols, changes, test results, and the next verification.\n"
        "3. Record failed or repeated actions as negative constraints.\n"
        "4. Preserve useful facts unless contradicted.\n"
        "5. Keep at most 20 concise entity_map entries, 12 constraints, and "
        "8 sub-goals; each value should be under 200 characters."
    )
    user = (
        "Environment: Harbor coding/data task sandbox.\n\n"
        "Previous State JSON:\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        f"Executed Action:\n{action}\n\n"
        "Latest Observation:\n"
        f"{observation}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


class ZipActHarborAgent(DefaultAgent):
    """Memory-less Actor over a validated, bounded G-W-C state."""

    def __init__(self, model: Any, env: Any, **kwargs: Any) -> None:
        super().__init__(model, env, config_class=ZipActConfig, **kwargs)
        self.config: ZipActConfig
        self._state: AgentState | None = None
        self._latest_observation = (
            "No shell command has been executed yet. Inspect the task workspace."
        )
        self._internal_records: list[dict[str, Any]] = []
        self._internal_cost = 0.0
        self._actor_steps = 0
        self._valid_state_updates = 0
        self._invalid_state_updates = 0

    def _record_internal(
        self,
        record: dict[str, Any],
        cost: float,
        phase: str,
    ) -> None:
        record["extra"]["baseline_phase"] = phase
        record["extra"]["finish_reason"] = response_finish_reason(record)
        self._internal_records.append(record)
        self._internal_cost += cost

    @staticmethod
    def _restore_instruction(
        state: AgentState,
        instruction: str,
    ) -> AgentState:
        state.goal_state.global_instruction = instruction
        state.goal_state.sub_goal_queue = list(
            state.goal_state.sub_goal_queue
        )[:8]
        entity_items = list(state.world_state.entity_map.items())[-20:]
        state.world_state.entity_map = dict(entity_items)
        state.world_state.inventory = list(state.world_state.inventory)[-20:]
        state.constraint_state.negative_constraints = list(
            state.constraint_state.negative_constraints
        )[-12:]
        state.constraint_state.visited_locations = list(
            state.constraint_state.visited_locations
        )[-12:]
        return state

    def _initialize_state(self) -> None:
        task = str(self.extra_template_vars.get("task") or "")
        try:
            text, record, cost = plain_completion(
                self.model,
                _initializer_prompt(task),
                max_tokens=self.config.zipact_initializer_max_tokens,
            )
            self._record_internal(record, cost, "zipact_initializer")
            parsed = extract_json_object(text)
            valid = parsed is not None
            record["extra"]["state_update_valid"] = valid
            if valid:
                self._state = self._restore_instruction(
                    AgentState.from_dict(parsed, task),
                    task,
                )
                self._valid_state_updates += 1
            else:
                self._state = _fallback_state(task)
                self._invalid_state_updates += 1
        except Exception as exc:
            self.logger.warning("ZipAct state initialization failed: %s", exc)
            self._state = _fallback_state(task)
            self._invalid_state_updates += 1

    def _compact_state(self) -> str:
        if self._state is None:
            return "{}"
        return json.dumps(
            _bounded_state_payload(self._state),
            ensure_ascii=False,
            indent=2,
        )

    def _actor_messages(self) -> list[dict[str, Any]]:
        if self._state is None:
            self._initialize_state()
        if self._state is None:
            raise RuntimeError("ZipAct state initialization did not produce state")

        base_system = str(self.messages[0].get("content") or "")
        base_instance = (
            str(self.messages[1].get("content") or "")
            if len(self.messages) > 1
            else ""
        )
        system = (
            base_system
            + "\n\n"
            + "You are the Actor module of ZipAct. Operate in memory-less "
            + "mode: decide only from the compact State Table and Latest "
            + "Observation. Check negative constraints before acting, advance "
            + "current_objective directly, and use the bash tool exactly as "
            + "required by the task prompt."
        )
        user = (
            f"{base_instance}\n\n"
            "## ZipAct State Table\n"
            f"{self._compact_state()}\n\n"
            "## Latest Observation\n"
            f"{self._latest_observation}"
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def query(self) -> dict[str, Any]:
        effective_cost = self.cost + self._internal_cost
        if (
            0 < self.config.step_limit <= self.n_calls
            or 0 < self.config.cost_limit <= effective_cost
            or self._actor_steps >= self.config.zipact_max_steps
        ):
            raise LimitsExceeded(
                {
                    "role": "exit",
                    "content": "LimitsExceeded",
                    "extra": {
                        "exit_status": "LimitsExceeded",
                        "submission": "",
                    },
                }
            )
        if (
            0 < self.config.wall_time_limit_seconds
            <= int(time.time() - self._start_time)
        ):
            raise TimeExceeded(
                {
                    "role": "exit",
                    "content": "TimeExceeded",
                    "extra": {
                        "exit_status": "TimeExceeded",
                        "submission": "",
                    },
                }
            )

        self.n_calls += 1
        self._actor_steps += 1
        message = self.model.query(self._actor_messages())
        self.cost += message.get("extra", {}).get("cost", 0.0)
        message.setdefault("extra", {})[
            "zipact_actor_step"
        ] = self._actor_steps
        self.add_messages(message)
        return message

    def execute_actions(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        actions = message.get("extra", {}).get("actions", [])
        observations = super().execute_actions(message)
        action_text = "\n".join(
            str(action.get("command") or "") for action in actions
        )
        observation_parts = []
        for observation in observations:
            extra = observation.get("extra", {})
            observation_parts.append(
                f"returncode={extra.get('returncode')}\n"
                f"{extra.get('raw_output') or observation.get('content') or ''}"
            )
        observation_text = "\n\n".join(observation_parts)
        self._latest_observation = observation_text
        self._update_state(action_text, observation_text)
        return observations

    def _update_state(self, action: str, observation: str) -> None:
        if self._state is None:
            return
        previous = self._state
        instruction = previous.goal_state.global_instruction
        try:
            text, record, cost = plain_completion(
                self.model,
                _updater_prompt(previous, action, observation),
                max_tokens=self.config.zipact_updater_max_tokens,
            )
            self._record_internal(record, cost, "zipact_state_updater")
            parsed = extract_json_object(text)
            valid = parsed is not None
            record["extra"]["state_update_valid"] = valid
            if valid:
                self._state = self._restore_instruction(
                    AgentState.from_dict(parsed, instruction),
                    instruction,
                )
                self._valid_state_updates += 1
            else:
                self._invalid_state_updates += 1
        except Exception as exc:
            self.logger.warning("ZipAct state update failed: %s", exc)
            self._invalid_state_updates += 1

    def serialize(self, *extra_dicts: dict[str, Any]) -> dict[str, Any]:
        data = super().serialize(*extra_dicts)
        data["messages"] = copy.deepcopy(data["messages"])
        data["messages"].extend(copy.deepcopy(self._internal_records))
        model_stats = data["info"]["model_stats"]
        model_stats["instance_cost"] = float(
            model_stats.get("instance_cost") or 0.0
        ) + self._internal_cost
        model_stats["api_calls"] = int(
            model_stats.get("api_calls") or 0
        ) + len(self._internal_records)
        data["info"]["zipact"] = {
            "source_commit": "f0258044c3be203d1e2edcb8d2559cbdf3c5de00",
            "adapter_revision": "harbor-fixed-v2-20260724",
            "environment_adapter": "harbor",
            "actor_steps": self._actor_steps,
            "internal_calls": len(self._internal_records),
            "valid_state_updates": self._valid_state_updates,
            "invalid_state_updates": self._invalid_state_updates,
            "max_steps": self.config.zipact_max_steps,
            "final_state": self._state.to_dict() if self._state else None,
        }
        return data
