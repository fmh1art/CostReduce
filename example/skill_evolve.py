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


def clip_observation(obj, max_chars):
    if isinstance(obj, dict):
        return {k: clip_text(v, max_chars) if k == "observation" else clip_observation(v, max_chars) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clip_observation(x, max_chars) for x in obj]
    return obj


def clip_text(value, max_chars):
    text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    return text if len(text) <= max_chars else text[:max_chars] + "...<truncated>"


def action_of(step):
    return step.get("tool_calls") or step.get("action") or step.get("message") or ""


def serialize_trajectory(trajectory, max_observation_chars):
    steps = []
    for i, step in enumerate(trajectory.get("steps", [])):
        if step.get("tool_calls") or "observation" in step or step.get("action"):
            steps.append({
                "index": len(steps) + 1,
                "source_step_id": step.get("step_id", i),
                "action": action_of(step),
                "observation": clip_text(step.get("observation", ""), max_observation_chars),
            })
    return json.dumps(
        {
            "dependencies": trajectory.get("dependencies", {}),
            "minimal_step_indices": trajectory.get("minimal_step_indices", []),
            "steps": steps,
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )


def load_sample(path, max_observation_chars):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return (
        serialize_trajectory(data["negative_sample"], max_observation_chars),
        serialize_trajectory(data["positive_sample"], max_observation_chars),
    )


def build_prompt(sample_paths, max_observation_chars):
    parts = [
        "Here are contrastive execution histories. The original trajectory is high-cost, while the minimal trajectory keeps only dependency-critical steps.",
        "Evolve the tools in this working directory to help future agents solve similar tasks with fewer steps/tokens while preserving correctness.",
        "The current working directory is .evolve_skills; each tool should live under ./<tool_name>/ and may contain Python files plus a main.sh entrypoint.",
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
        "\nYour task is to modify, add, or remove tools in the current directory. Keep the implementation minimal. "
        "Do not edit the contrastive sample files. Finish after the evolved tools are saved."
    )
    return "\n".join(parts)


def load_llm_env(config_path):
    cfg = LLM._load_config(config_path)
    return {
        "OPENAI_API_KEY": cfg.get("key", ""),
        "OPENAI_BASE_URL": cfg.get("openai_base_url", ""),
        "OPENAI_API_BASE": cfg.get("openai_base_url", ""),
    }, f"openai/{cfg.get('llm_name') or cfg.get('model')}"


def run_agent(prompt, skills_dir, output, llm_config, dry_run=False):
    env, model = load_llm_env(llm_config)
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
        prompt,
    ]
    print("+", " ".join(shlex.quote(x) for x in cmd))
    if not dry_run:
        subprocess.run(cmd, cwd=skills_dir, env={**os.environ, **env}, check=True)


def main():
    parser = argparse.ArgumentParser(description="Evolve tools from contrastive trajectory samples.")
    parser.add_argument("result_dir", help="result/run directory containing */agent/contrastive_sample.json")
    parser.add_argument("--task", help="optional task id/name substring filter")
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--max-observation-chars", type=int, default=500)
    parser.add_argument("--skills-dir", default=str(ROOT / ".evolve_skills"))
    parser.add_argument("--config", default=str(ROOT / "_config" / "deepseekv4_flash.yaml"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    skills_dir = Path(args.skills_dir).resolve()
    skills_dir.mkdir(parents=True, exist_ok=True)
    samples = find_samples(args.result_dir, args.task)
    print(f"found {len(samples)} contrastive samples")
    for batch_id, sample_paths in enumerate(batched(samples, args.batch_size), start=1):
        run_agent(
            build_prompt(sample_paths, args.max_observation_chars),
            skills_dir,
            skills_dir / f"evolve_batch_{batch_id}.traj.json",
            args.config,
            args.dry_run,
        )


if __name__ == "__main__":
    main()
