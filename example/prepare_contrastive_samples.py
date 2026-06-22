import argparse
import copy
import json
from pathlib import Path


def find_trajectory_files(result_dir, task=None):
    files = sorted(Path(result_dir).glob("**/agent/trajectory.json"))
    return [p for p in files if not task or task in str(p)]


def extract_action_steps(trajectory):
    steps = trajectory.get("steps", [])
    return [s for s in steps if s.get("tool_calls") or "observation" in s or s.get("action")]


def is_action_step(step):
    return bool(step.get("tool_calls") or "observation" in step or step.get("action"))


def trace_minimal_indices(dependencies):
    if not dependencies:
        return set()

    last = max(int(k) for k in dependencies)
    keep = set()
    stack = [last]
    while stack:
        i = stack.pop()
        if i in keep:
            continue
        keep.add(i)
        stack.extend(int(j) for j in dependencies.get(str(i), []))
    return keep


def build_positive_sample(trajectory):
    dependencies = trajectory.get("dependencies")
    if dependencies is None:
        raise ValueError("trajectory has no dependencies field")

    keep = trace_minimal_indices(dependencies)
    positive = copy.deepcopy(trajectory)
    action_i = 0
    positive_steps = []
    for step in trajectory.get("steps", []):
        if is_action_step(step):
            action_i += 1
            if action_i in keep:
                positive_steps.append(step)
        elif 0 in keep:
            positive_steps.append(step)

    positive["steps"] = positive_steps
    positive["dependencies"] = {str(i): dependencies[str(i)] for i in sorted(keep) if str(i) in dependencies}
    positive["minimal_step_indices"] = sorted(keep)
    return positive


def prepare_contrastive_sample(path):
    trajectory = json.loads(Path(path).read_text(encoding="utf-8"))
    sample = {
        "positive_sample": build_positive_sample(trajectory),
        "negative_sample": trajectory,
    }
    out = Path(path).with_name("contrastive_sample.json")
    out.write_text(json.dumps(sample, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def main():
    parser = argparse.ArgumentParser(description="Generate contrastive_sample.json for annotated trajectories.")
    parser.add_argument("result_dir", help="result/run directory containing */agent/trajectory.json")
    parser.add_argument("--task", help="optional task id/name substring filter")
    args = parser.parse_args()

    for path in find_trajectory_files(args.result_dir, args.task):
        print(f"writing {prepare_contrastive_sample(path)}")


if __name__ == "__main__":
    main()
