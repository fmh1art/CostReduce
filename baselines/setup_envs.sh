#!/usr/bin/env bash
set -euo pipefail

BASELINES_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${BASELINES_DIR}/.." && pwd)"
HARBOR_SOURCE="${PROJECT_DIR}/tmp/harbor"
MINI_VERSION="2.4.5"
PYTHON_VERSION="3.12.11"

CONDA_BIN="${CONDA_EXE:-}"
if [[ -z "${CONDA_BIN}" ]]; then
    CONDA_BIN="$(command -v conda || true)"
fi
UV_BIN="${UV_BIN:-$(command -v uv || true)}"

if [[ -z "${CONDA_BIN}" ]]; then
    echo "conda was not found; activate a conda installation first." >&2
    exit 1
fi
if [[ -z "${UV_BIN}" ]]; then
    echo "uv was not found; install uv or set UV_BIN." >&2
    exit 1
fi
if [[ ! -d "${HARBOR_SOURCE}" ]]; then
    echo "Local Harbor checkout is missing: ${HARBOR_SOURCE}" >&2
    exit 1
fi
if [[ ! -f "${BASELINES_DIR}/trajectory_reduction/original/artifact/code/requirements.txt" ]]; then
    echo "AgentDiet artifact is not extracted under trajectory_reduction/original." >&2
    exit 1
fi
if [[ ! -f "${BASELINES_DIR}/zipact/pyproject.toml" ]]; then
    echo "ZipAct source checkout is missing under baselines/zipact." >&2
    exit 1
fi
if [[ ! -f "${BASELINES_DIR}/eet/mini-swe-agent/src/minisweagent/experience/extracted_experiences_summarized_gpt_5_mini.jsonl" ]]; then
    echo "EET source checkout or official experience store is missing." >&2
    exit 1
fi

mkdir -p \
    "${BASELINES_DIR}/envs" \
    "${BASELINES_DIR}/.cache/conda/pkgs" \
    "${BASELINES_DIR}/.cache/uv-host"
export CONDA_PKGS_DIRS="${BASELINES_DIR}/.cache/conda/pkgs"
export UV_CACHE_DIR="${BASELINES_DIR}/.cache/uv-host"

# Opt in with:
# BASELINE_PROXY=http://sys-proxy-rd-relay.byted.org:8118 ./setup_envs.sh
if [[ -n "${BASELINE_PROXY:-}" ]]; then
    export HTTP_PROXY="${BASELINE_PROXY}"
    export HTTPS_PROXY="${BASELINE_PROXY}"
    export http_proxy="${BASELINE_PROXY}"
    export https_proxy="${BASELINE_PROXY}"
fi

create_prefix() {
    local prefix="$1"
    if [[ ! -x "${prefix}/bin/python" ]]; then
        "${CONDA_BIN}" create \
            --yes \
            --prefix "${prefix}" \
            "python=${PYTHON_VERSION}"
    fi
}

install_common() {
    local prefix="$1"
    "${UV_BIN}" pip install \
        --python "${prefix}/bin/python" \
        "${HARBOR_SOURCE}" \
        "mini-swe-agent==${MINI_VERSION}"
}

AGENTDIET_PREFIX="${BASELINES_DIR}/envs/agentdiet"
ZIPACT_PREFIX="${BASELINES_DIR}/envs/zipact"
EET_PREFIX="${BASELINES_DIR}/envs/eet"

create_prefix "${AGENTDIET_PREFIX}"
create_prefix "${ZIPACT_PREFIX}"
create_prefix "${EET_PREFIX}"

install_common "${AGENTDIET_PREFIX}"
"${UV_BIN}" pip install \
    --python "${AGENTDIET_PREFIX}/bin/python" \
    --requirement \
    "${BASELINES_DIR}/trajectory_reduction/original/artifact/code/requirements.txt" \
    lz4

install_common "${ZIPACT_PREFIX}"
"${UV_BIN}" pip install \
    --python "${ZIPACT_PREFIX}/bin/python" \
    --editable "${BASELINES_DIR}/zipact[openai]"

install_common "${EET_PREFIX}"

PYTHONPATH="${BASELINES_DIR}:${BASELINES_DIR}/trajectory_reduction/harbor_agent" \
    "${AGENTDIET_PREFIX}/bin/python" -c \
    "import harbor, minisweagent, agentdiet_harbor; print('agentdiet OK', minisweagent.__version__)"
PYTHONPATH="${BASELINES_DIR}:${BASELINES_DIR}/zipact/harbor_agent" \
    "${ZIPACT_PREFIX}/bin/python" -c \
    "import harbor, minisweagent, zipact_harbor; print('zipact OK', minisweagent.__version__)"
PYTHONPATH="${BASELINES_DIR}:${BASELINES_DIR}/eet/harbor_agent" \
    "${EET_PREFIX}/bin/python" -c \
    "import harbor, minisweagent, eet_harbor; print('eet OK', minisweagent.__version__)"

echo "All baseline environments are ready under ${BASELINES_DIR}/envs."

