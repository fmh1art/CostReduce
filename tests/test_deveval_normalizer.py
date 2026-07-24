from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NORMALIZER = PROJECT_ROOT / "scripts" / "normalize_deveval_tasks.sh"


def test_deveval_normalizer_is_idempotent(tmp_path: Path) -> None:
    tests_dir = tmp_path / "case" / "tests"
    tests_dir.mkdir(parents=True)
    helper = tests_dir / "setup-uv-pytest.sh"
    helper.write_text(
        """#!/bin/bash

# Install curl
apt-get update
apt-get install -y curl

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

source $HOME/.local/bin/env

uv init
uv add pytest
""",
        encoding="utf-8",
    )

    subprocess.run(["bash", str(NORMALIZER), str(tmp_path)], check=True)
    first = helper.read_text(encoding="utf-8")
    subprocess.run(["bash", str(NORMALIZER), str(tmp_path)], check=True)

    assert helper.read_text(encoding="utf-8") == first
    assert first.startswith("#!/bin/bash\n")
    assert first.count("prefer the mounted, pinned uv runtime") == 1
    assert first.count("curl -LsSf https://astral.sh/uv/install.sh | sh") == 1
    assert "/opt/optiharness_toolchain/uv" in first
    assert "/opt/baseline_toolchain/uv" in first
