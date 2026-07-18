from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PATH = ROOT / "benchmark" / "DBA-bench" / "dab_harbor_adapter.py"


def _load_adapter():
    spec = importlib.util.spec_from_file_location("dab_harbor_adapter", ADAPTER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_agent_image_excludes_answers_and_validator(tmp_path: Path) -> None:
    adapter = _load_adapter()
    dab_root = tmp_path / "dab"
    dataset = dab_root / "query_demo"
    query = dataset / "query1"
    common = dab_root / "common_scaffold"
    data = dataset / "query_dataset"
    common.mkdir(parents=True)
    data.mkdir(parents=True)
    query.mkdir(parents=True)

    (common / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (data / "sample.db").write_bytes(b"not-a-real-db")
    (dataset / "db_description.txt").write_text("demo schema\n", encoding="utf-8")
    (dataset / "db_config.yaml").write_text(
        yaml.safe_dump({"db_clients": {"demo": {"db_type": "sqlite", "db_path": "query_dataset/sample.db"}}}),
        encoding="utf-8",
    )
    (query / "query.json").write_text(json.dumps("What is the answer?"), encoding="utf-8")
    (query / "ground_truth.csv").write_text("42\n", encoding="utf-8")
    (query / "validate.py").write_text(
        "from pathlib import Path\n"
        "def validate(answer):\n"
        "    gt = Path(__file__).with_name('ground_truth.csv').read_text().strip()\n"
        "    return answer.strip() == gt, 'checked'\n",
        encoding="utf-8",
    )

    output = tmp_path / "tasks"
    task_id = adapter.generate_task(dab_root, output, dataset, query, use_hints=False)
    task = output / task_id
    public_query = task / "environment" / "dab" / "query"
    private_query = task / "tests" / "dab_query"

    assert sorted(path.name for path in public_query.iterdir()) == ["query.json"]
    assert not list((task / "environment").rglob("ground_truth.csv"))
    assert not list((task / "environment").rglob("validate.py"))
    assert (private_query / "ground_truth.csv").read_text(encoding="utf-8") == "42\n"
    assert (private_query / "validate.py").is_file()
    instruction = (task / "instruction.md").read_text(encoding="utf-8")
    assert "ground_truth" not in instruction
    assert "/tests" not in instruction


def test_manifest_marks_blind_schema(tmp_path: Path, monkeypatch) -> None:
    adapter = _load_adapter()
    assert adapter.SCHEMA_VERSION == "dab-harbor.v2-blind"
