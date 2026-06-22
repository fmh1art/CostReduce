import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MINI_SWE_AGENT = ROOT / "agent" / "mini-swe-agent"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tools.llm import LLM


def find_samples(result_dir, task=None):
    files = sorted(Path(result_dir).glob("**/agent/contrastive_sample.json"))
    return [p for p in files if not task or task in str(p)]


def batched(items, batch_size):
    for i in range(0, len(items), batch_size):
        yield items[i : i + batch_size]


def clip_text(value, max_chars):
    text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    return text if len(text) <= max_chars else text[:max_chars] + "...<truncated>"


def action_of(step):
    return step.get("tool_calls") or step.get("action") or step.get("message") or ""


def serialize_action(action):
    if isinstance(action, list):
        lines = []
        for call in action:
            lines.append(f"- tool: {call.get('function_name', '')}")
            for key, value in (call.get("arguments") or {}).items():
                lines.append(f"  {key}: {clip_text(value, 1000)}")
        return "\n".join(lines)
    return clip_text(action, 1000)


def serialize_observation(observation, max_chars):
    if isinstance(observation, dict) and isinstance(observation.get("results"), list):
        texts = []
        for item in observation["results"]:
            content = item.get("content", item) if isinstance(item, dict) else item
            try:
                parsed = json.loads(content) if isinstance(content, str) else content
            except json.JSONDecodeError:
                parsed = content
            if isinstance(parsed, dict):
                texts.append(
                    "\n".join(
                        part
                        for part in [
                            f"returncode: {parsed.get('returncode')}" if "returncode" in parsed else "",
                            f"output:\n{parsed.get('output')}" if parsed.get("output") else "",
                            f"exception_info: {parsed.get('exception_info')}" if parsed.get("exception_info") else "",
                        ]
                        if part
                    )
                )
            else:
                texts.append(clip_text(parsed, max_chars))
        text = "\n".join(texts)
    else:
        text = clip_text(observation, max_chars)
    return clip_text(text, max_chars)


def serialize_trajectory(trajectory, max_observation_chars):
    lines = []
    if trajectory.get("minimal_step_indices"):
        lines.append("Minimal step indices: " + ", ".join(map(str, trajectory["minimal_step_indices"])))
    dependencies = trajectory.get("dependencies", {})
    step_index = 0
    for i, step in enumerate(trajectory.get("steps", [])):
        if step.get("tool_calls") or "observation" in step or step.get("action"):
            step_index += 1
            lines += [
                f"\n### Step {step_index} (source_step_id={step.get('step_id', i)})",
                f"Depends on: {', '.join(map(str, dependencies.get(str(step_index), []))) or 'none'}",
                "Action:",
                serialize_action(action_of(step)),
                "Observation:",
                serialize_observation(step.get("observation", ""), max_observation_chars),
            ]
    return "\n".join(lines)


def load_sample(path, max_observation_chars):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return (
        serialize_trajectory(data["negative_sample"], max_observation_chars),
        serialize_trajectory(data["positive_sample"], max_observation_chars),
    )


def build_prompt(sample_paths, max_observation_chars):
    parts = [
        "Here are contrastive execution histories. The original trajectory is high-cost, while the minimal trajectory keeps only dependency-critical steps.",
        "Evolve the tools and instruction.md in this working directory to help future agents solve similar tasks with fewer steps/tokens while preserving correctness.",
        "The current working directory is .evolve_tools; each tool should live under ./<tool_name>/ and may contain Python files plus a main.sh entrypoint.",
        "Also maintain ./instruction.md with high-level cost-saving guidance: operations to avoid, when to combine multiple tool calls, how to inspect files/logs efficiently, and when an evolved tool should be used.",
    ]
    for i, path in enumerate(sample_paths, start=1):
        negative, positive = load_sample(path, max_observation_chars)
        parts += [
            f"\n# Executional History {i}",
            f"Source: {path}",
            "\n## Original Trajectory",
            negative,
            "\n## Minimal Trajectory",
            positive,
        ]
    parts.append(
        "\nYour task is to modify, add, or remove tools in the current directory, and update instruction.md together with those tools. "
        "Keep the implementation minimal. Do not edit the prompt file or contrastive sample files. "
        "Finish after the evolved tools and instruction.md are saved."
    )
    return "\n".join(parts)


def ensure_instruction_file(tools_dir):
    path = Path(tools_dir) / "instruction.md"
    if not path.exists():
        path.write_text(
            "# Cost-saving instructions\n\n"
            "Record reusable high-level guidance for reducing future agent cost here.\n",
            encoding="utf-8",
        )


def load_llm_env(config_path):
    cfg = LLM._load_config(config_path)
    return {
        "OPENAI_API_KEY": cfg.get("key", ""),
        "OPENAI_BASE_URL": cfg.get("openai_base_url", ""),
        "OPENAI_API_BASE": cfg.get("openai_base_url", ""),
        "MSWEA_COST_TRACKING": "ignore_errors",
    }, f"openai/{cfg.get('llm_name') or cfg.get('model')}"


def run_agent(prompt, tools_dir, output, llm_config, dry_run=False):
    env, model = load_llm_env(llm_config)
    prompt_path = output.with_suffix(".prompt.md")
    task = (
        f"Read the full evolution instruction from {prompt_path}. "
        "Then modify, add, or remove tools and update instruction.md in the current working directory as requested. "
        "Do not edit the prompt file or contrastive sample files."
    )
    cmd = [
        "uv",
        "run",
        "--directory",
        str(MINI_SWE_AGENT),
        "mini",
        "-m",
        model,
        "--model-class",
        "litellm",
        "--environment-class",
        "local",
        "-y",
        "--exit-immediately",
        "--cost-limit",
        "0",
        "-o",
        str(output),
        "-t",
        task,
    ]
    print("+", " ".join(shlex.quote(x) for x in cmd))
    print(f"prompt {'would be saved' if dry_run else 'saved'} to {prompt_path}")
    if not dry_run:
        output.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")
        subprocess.run(cmd, cwd=tools_dir, env={**os.environ, **env}, check=True)


def main():
    parser = argparse.ArgumentParser(description="Evolve tools from contrastive trajectory samples.")
    parser.add_argument("result_dir", help="result/run directory containing */agent/contrastive_sample.json")
    parser.add_argument("--task", help="optional task id/name substring filter")
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--max-observation-chars", type=int, default=500)
    parser.add_argument("--tools-dir", default=str(ROOT / ".evolve_tools"))
    parser.add_argument("--output-dir", help="where to save evolve prompts and mini-swe-agent trajectories")
    parser.add_argument("--config", default=str(ROOT / "_config" / "deepseekv4_flash.yaml"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    tools_dir = Path(args.tools_dir).resolve()
    tools_dir.mkdir(parents=True, exist_ok=True)
    ensure_instruction_file(tools_dir)
    output_dir = Path(args.output_dir).resolve() if args.output_dir else Path(args.result_dir).resolve() / "evolve_logs"
    samples = find_samples(args.result_dir, args.task)
    print(f"found {len(samples)} contrastive samples")
    for batch_id, sample_paths in enumerate(batched(samples, args.batch_size), start=1):
        run_agent(
            build_prompt(sample_paths, args.max_observation_chars),
            tools_dir,
            output_dir / f"evolve_batch_{batch_id}.traj.json",
            args.config,
            args.dry_run,
        )


if __name__ == "__main__":
    main()
