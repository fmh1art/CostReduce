import os
import json



deepswe_with_path = "results/deep-swe/evolve-v2_chunk-deep-swe-0628-024710"
deepswe_without_path = "results/deep-swe/deepseek-flash-with-evolve-tools-baseline-evolve"

# def load_json
def load_json(file_path):
    """
    Load a JSON file and return its content as a Python object.

    :param file_path: Path to the JSON file.
    :return: Python object representing the JSON data.
    """
    with open(file_path, 'r') as file:
        data = json.load(file)
    return data

def all_filepaths_in_dir(root, endswith=None):
    file_paths = []
    for subdir, dirs, files in os.walk(root):
        for file in files:
            if endswith is None or file.endswith(endswith):
                file_paths.append(os.path.join(subdir, file))
    return file_paths

price = {}
price["uncached_input_token"] = 1 / 1_000_000
price["output_token"] = 2 / 1_000_000
price["cached_token"] = 0.02 / 1_000_000

def cal_api_cost(data):
    total_inp_tokens = data["final_metrics"]["total_prompt_tokens"]
    total_cache_tokens = data["final_metrics"]["total_cached_tokens"]
    total_out_tokens = data["final_metrics"]["total_completion_tokens"]

    total_uncached_input_tokens = total_inp_tokens - total_cache_tokens
    total_cost = (total_uncached_input_tokens * price["uncached_input_token"] +
                  total_cache_tokens * price["cached_token"] +
                  total_out_tokens * price["output_token"])
    return total_cost

def cal_step_count(data):
    # 一个 step = trajectory 里 steps 列表中的一条记录（含 system/user 的 setup 步）。
    # 若只想统计 agent 自身动作步数，可改为 len([s for s in data["steps"] if s.get("source") == "agent"])。
    return len(data["steps"])

def load_metrics(root):
    """遍历 root 下所有 trajectory.json，返回 {case_id: (cost, steps)}。"""
    metrics = {}
    for fn in all_filepaths_in_dir(root, endswith="trajectory.json"):
        file_name = os.path.basename(fn)
        if file_name != "trajectory.json":
            continue
        # 从fn中提取case id
        case_id = os.path.basename(os.path.dirname(os.path.dirname(fn))).split('__')[0]

        data = load_json(fn)
        metrics[case_id] = (cal_api_cost(data), cal_step_count(data))
    return metrics

with_metrics = load_metrics(deepswe_with_path)
without_metrics = load_metrics(deepswe_without_path)

print(with_metrics.keys())
print(without_metrics.keys())

joint_case_id = [id for id in with_metrics if id in without_metrics]

# ---- API cost reduction ----
avg_cost_with = sum(with_metrics[id][0] for id in joint_case_id) / len(joint_case_id)
avg_cost_without = sum(without_metrics[id][0] for id in joint_case_id) / len(joint_case_id)
print(f"The average cost for with tools: {avg_cost_with}")
print(f"The average cost for without tools: {avg_cost_without}")
print(f"The cost reduction is: {(avg_cost_without-avg_cost_with)/avg_cost_without * 100} %")

# ---- Step reduction ----
avg_steps_with = sum(with_metrics[id][1] for id in joint_case_id) / len(joint_case_id)
avg_steps_without = sum(without_metrics[id][1] for id in joint_case_id) / len(joint_case_id)
print(f"The average steps for with tools: {avg_steps_with}")
print(f"The average steps for without tools: {avg_steps_without}")
print(f"The step reduction is: {(avg_steps_without-avg_steps_with)/avg_steps_without * 100} %")

print(f"The joint count is: {len(joint_case_id)}")
