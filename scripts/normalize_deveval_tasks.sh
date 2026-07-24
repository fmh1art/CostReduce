#!/usr/bin/env bash
set -euo pipefail

# Harbor Hub's DevEval export currently assumes TEST_DIR is provided and its
# shared verifier helper downloads uv on every trial.  Keep the benchmark's
# tests intact, but make the environment bootstrap prefer the uv binary that
# OptiHarness/baseline runners already mount into the container.

TASK_ROOT="${1:?usage: normalize_deveval_tasks.sh TASK_ROOT}"
MARKER="# OptiHarness: prefer the mounted, pinned uv runtime."
normalized=0

while IFS= read -r -d '' setup_script; do
  # Repair files produced by the first integration prototype, where the
  # compatibility block was accidentally inserted before the shebang.
  if [[ "$(head -n 1 "$setup_script")" != '#!/bin/bash' ]] \
      && grep -Fq '#!/bin/bash' "$setup_script"; then
    perl -0pi -e 's{\A.*?(?=#!/bin/bash\n)}{}s' "$setup_script"
  fi
  if grep -Fq "$MARKER" "$setup_script" \
      && [[ "$(grep -Fc 'curl -LsSf https://astral.sh/uv/install.sh | sh' "$setup_script")" -eq 1 ]]; then
    continue
  fi
  if ! grep -Fq 'curl -LsSf https://astral.sh/uv/install.sh | sh' "$setup_script"; then
    printf '[normalize-deveval] unsupported setup helper: %s\n' "$setup_script" >&2
    exit 1
  fi

  perl -0pi -e '
    s{# Install curl\napt-get update\napt-get install -y curl\n\n# Install uv\ncurl -LsSf https://astral\.sh/uv/install\.sh \| sh\n\nsource \$HOME/\.local/bin/env}{
# OptiHarness: prefer the mounted, pinned uv runtime.
if [[ -x /opt/optiharness_toolchain/uv ]]; then
    export PATH="/opt/optiharness_toolchain:\$PATH"
elif [[ -x /opt/baseline_toolchain/uv ]]; then
    export PATH="/opt/baseline_toolchain:\$PATH"
elif ! command -v uv >/dev/null 2>\&1; then
    apt-get update
    apt-get install -y curl
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source "\$HOME/.local/bin/env"
fi
}' "$setup_script"

  grep -Fq "$MARKER" "$setup_script" \
    || { printf '[normalize-deveval] failed to normalize: %s\n' "$setup_script" >&2; exit 1; }
  [[ "$(grep -Fc 'curl -LsSf https://astral.sh/uv/install.sh | sh' "$setup_script")" -eq 1 ]] \
    || { printf '[normalize-deveval] duplicate fallback installer: %s\n' "$setup_script" >&2; exit 1; }
  normalized=$((normalized + 1))
done < <(find -L "$TASK_ROOT" -path '*/tests/setup-uv-pytest.sh' -type f -print0)

printf '[normalize-deveval] normalized=%s root=%s\n' "$normalized" "$TASK_ROOT" >&2
