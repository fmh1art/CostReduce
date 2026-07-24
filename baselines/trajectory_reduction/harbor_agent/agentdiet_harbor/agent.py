"""AgentDiet trajectory reduction on top of mini-swe-agent 2.x.

The reduction schedule, XML trajectory representation, compression prompt,
threshold, context window, and acceptance rule follow the artifact's
``agents/traj_analyzer.py``.  Only the surrounding agent interface is adapted
from Trae Agent to Harbor's mini-swe-agent so all baselines share the same code
agent prompt and bash tool.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any

from minisweagent.agents.default import AgentConfig, DefaultAgent
from pydantic import Field

from harbor_common.internal_llm import plain_completion, response_usage


COMPRESSION_SYSTEM_PROMPT = """
You will analyze and compress a given step in a trajectory of an AI agent solving a software bug.

In the trajectory, each step is marked in <step id="..."></step>.
The agent will think in <think>, call external tools as marked in <call tool="..."></call>. Its result is marked in <result></result> within the <step> tag.

Your job is to compress the text within the given id to avoid harming efficiency, typically shortening it to 20%-50% of the original length.
Meanwhile, keep the compressed text useful such that you are able to continue the trajectory as close as the original path.

- You should ONLY remove redundant texts, which are either irrelevant to future steps or duplicated by other texts in the trajectory.
- Replace the text to remove to "..." and a short takeaway, e.g. "... (same as the content below)".
- You should keep the original structure unchanged, e.g., XML tags, Python indentation and line numbers.
- Again, keep useful details in the original content unchanged, e.g., XML tags, Python indentation and line numbers.

Typical examples:
- If the step opens a huge file but only one part is necessary for future steps, replace other parts to "... (unrelated function XXX, YYY)".
- If the step runs a verbose test script and everything goes fine, replace the verbose part to "... (expected output)".
- If the step uses str_replace_editor to modify a file and the content can be inferred by the content after it, replace the tool call argument to "... (see results below)".

You should only process the text within the <step> tag with the given id. STOP OUTPUT IMMEDIATELY AFTER </step>.
""".strip()


class AgentDietConfig(AgentConfig):
    """Artifact defaults exposed as mini-swe-agent config fields."""

    reduction_mode: str = "ours"
    reduction_threshold_tokens: int = 500
    reduction_context_before: int = 1
    reduction_context_after: int = 2
    reduction_show_context: bool = True
    reduction_max_tokens: int = 2048


@dataclass
class _Step:
    messages: list[dict[str, Any]]
    compressed: bool = False
    original_xml: str = ""


class AgentDietHarborAgent(DefaultAgent):
    """mini-swe-agent with AgentDiet's delayed per-step compression."""

    def __init__(self, model: Any, env: Any, **kwargs: Any) -> None:
        super().__init__(model, env, config_class=AgentDietConfig, **kwargs)
        self.config: AgentDietConfig
        self._steps: list[_Step] = []
        self._internal_records: list[dict[str, Any]] = []
        self._erased_usage_records: list[dict[str, Any]] = []
        self._internal_cost = 0.0
        self._metrics: dict[str, int | float] = {
            "analysis_count": 0,
            "analysis_prompt_tokens": 0,
            "analysis_completion_tokens": 0,
            "erase_tot_count": 0,
            "erase_in_tokens": 0,
            "erase_out_tokens": 0,
            "seen_tokens": 0,
        }
        self._encoding = None

    def execute_actions(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        observations = super().execute_actions(message)
        self._steps.append(_Step(messages=[message, *observations]))
        self._maybe_reduce()
        return observations

    def _count_tokens(self, text: str) -> int:
        try:
            if self._encoding is None:
                import tiktoken

                self._encoding = tiktoken.encoding_for_model("gpt-4o")
            return len(self._encoding.encode(text))
        except Exception:
            # Only used if a minimal task image omitted tiktoken.
            return max(1, len(text) // 4)

    @staticmethod
    def _tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
        raw = message.get("tool_calls") or []
        return [item for item in raw if isinstance(item, dict)]

    def _step_to_xml(self, index: int, *, archived: bool = False) -> str:
        if index == -1:
            if len(self.messages) >= 2:
                return str(self.messages[1].get("content") or "")
            return ""

        step = self._steps[index]
        if archived and step.original_xml:
            return step.original_xml

        lines = [f'<step id="{index}">']
        for message in step.messages:
            role = message.get("role")
            content = str(message.get("content") or "")
            if role == "assistant":
                if content.strip():
                    lines.append(f"<think>{content}</think>")
                for call in self._tool_calls(message):
                    function = call.get("function") or {}
                    name = function.get("name", "bash")
                    arguments = function.get("arguments", "")
                    lines.append(f'<call tool="{name}">{arguments}</call>')
            elif role in {"tool", "user"} and content.strip():
                lines.append(f"<result>{content}</result>")
        lines.append("</step>")
        return "\n".join(lines)

    def _maybe_reduce(self) -> None:
        if self.config.reduction_mode == "skip":
            return
        if self.config.reduction_mode != "ours":
            raise ValueError(
                f"Harbor adapter supports reduction_mode='ours' or 'skip', "
                f"got {self.config.reduction_mode!r}"
            )

        turn = len(self._steps)
        before = self.config.reduction_context_before
        after = self.config.reduction_context_after
        if turn < before + after:
            return

        index = turn - 1 - after
        if index < 0 or self._steps[index].compressed:
            return

        target = self._step_to_xml(index, archived=True)
        old_tokens = self._count_tokens(target)
        self._metrics["seen_tokens"] += old_tokens
        if old_tokens < self.config.reduction_threshold_tokens:
            return

        context: list[str] = []
        for position in range(index - before, index + after + 1):
            if position == -1:
                chunk = self._step_to_xml(-1)
            elif 0 <= position < len(self._steps):
                chunk = self._step_to_xml(position, archived=True)
            else:
                chunk = ""
            if not self.config.reduction_show_context and position != index:
                chunk = ""
            context.append(chunk)

        user_prompt = (
            "\n".join(context) + f"\n\nNow, compress the step {index}."
        )
        prefix = (
            f"Sure. Here is the compressed content of step {index}: "
            f'<step id="{index}">'
        )
        prompt = [
            {"role": "system", "content": COMPRESSION_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": prefix},
        ]

        try:
            answer, record, cost = plain_completion(
                self.model,
                prompt,
                max_tokens=self.config.reduction_max_tokens,
                stop="</step>",
            )
        except Exception as exc:
            self.logger.warning("AgentDiet compression call failed: %s", exc)
            return

        self._metrics["analysis_count"] += 1
        prompt_tokens, completion_tokens = response_usage(record)
        self._metrics["analysis_prompt_tokens"] += prompt_tokens
        self._metrics["analysis_completion_tokens"] += completion_tokens
        record["extra"]["baseline_phase"] = "agentdiet_compression"
        record["extra"]["target_step"] = index
        self._internal_records.append(record)
        self._internal_cost += cost

        content = answer.partition("</step>")[0]
        if "<step" in content[:200] and ">" in content[:220]:
            content = content.partition("<step")[2].partition(">")[2]
        content = content.strip()
        new_tokens = self._count_tokens(content)
        if not (
            old_tokens - new_tokens >= 400 or new_tokens < 0.8 * old_tokens
        ):
            return

        self._metrics["erase_tot_count"] += 1
        self._metrics["erase_in_tokens"] += old_tokens
        self._metrics["erase_out_tokens"] += new_tokens
        self._compress_active_messages(index, content, target)

    def _compress_active_messages(
        self, index: int, compressed: str, original_xml: str
    ) -> None:
        step = self._steps[index]
        positions = [
            i
            for i, active in enumerate(self.messages)
            if any(active is original for original in step.messages)
        ]
        if not positions:
            return

        # Preserve usage for removed coding calls in the serialized trajectory.
        for message in step.messages:
            if message.get("role") == "assistant" and message.get("extra", {}).get(
                "response"
            ):
                archived = copy.deepcopy(message)
                archived.pop("tool_calls", None)
                archived.setdefault("extra", {})[
                    "baseline_archived_erased_call"
                ] = True
                self._erased_usage_records.append(archived)

        insertion = min(positions)
        remove_ids = {id(message) for message in step.messages}
        self.messages[:] = [
            message for message in self.messages if id(message) not in remove_ids
        ]
        replacement = {
            "role": "assistant",
            "content": (
                "(System reminder: compressed for better efficiency) "
                + compressed
            ),
            "extra": {
                "agentdiet_compressed": True,
                "agentdiet_step": index,
            },
        }
        self.messages.insert(insertion, replacement)
        step.compressed = True
        step.original_xml = original_xml
        step.messages = [replacement]

    def serialize(self, *extra_dicts: dict[str, Any]) -> dict[str, Any]:
        data = super().serialize(*extra_dicts)
        # DefaultAgent returns the live ``self.messages`` list.  Harbor saves
        # after every turn, so extending it in place would feed archived
        # compressor records (and even a terminal ``exit``) back to the model.
        data["messages"] = copy.deepcopy(data["messages"])
        data["messages"].extend(
            copy.deepcopy(self._erased_usage_records + self._internal_records)
        )
        model_stats = data["info"]["model_stats"]
        model_stats["instance_cost"] = float(
            model_stats.get("instance_cost") or 0.0
        ) + self._internal_cost
        model_stats["api_calls"] = int(model_stats.get("api_calls") or 0) + len(
            self._internal_records
        )
        data["info"]["agentdiet"] = {
            "artifact": "AgentDiet FSE26, arXiv:2509.23586",
            "source": "artifact/code/trae_agent/agents/traj_analyzer.py",
            "config": {
                "mode": self.config.reduction_mode,
                "threshold": self.config.reduction_threshold_tokens,
                "ctx_before": self.config.reduction_context_before,
                "ctx_after": self.config.reduction_context_after,
                "show_ctx": self.config.reduction_show_context,
            },
            "metrics": copy.deepcopy(self._metrics),
            "compressed_steps": [
                index
                for index, step in enumerate(self._steps)
                if step.compressed
            ],
        }
        return data
