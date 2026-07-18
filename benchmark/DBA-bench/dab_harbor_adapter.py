#!/usr/bin/env python3
"""Generate Harbor task directories for DataAgentBench (DAB).

The adapter emits one Harbor task per DAB query. Each generated task contains
the original query, a verifier-private validator, dataset description,
db_config, and the relevant dataset files.  Ground truth and validator source
are never copied into the agent image. Database files are hard-linked when
possible to avoid consuming extra disk space in the generated task tree.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from pathlib import Path

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required: python -m pip install pyyaml") from exc


ROOT = Path(__file__).resolve().parent
DEFAULT_DAB_ROOT = ROOT / "DataAgentBench"
DEFAULT_OUTPUT = ROOT / "harbor" / "datasets" / "dab"
SCHEMA_VERSION = "dab-harbor.v2-blind"


def sanitize(value: str) -> str:
    value = value.replace("query_", "")
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value)
    return value.strip("-").lower()


def link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def copy_tree_linked(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    for path in src.rglob("*"):
        rel = path.relative_to(src)
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            link_or_copy(path, target)


def read_query(query_path: Path) -> str:
    raw = query_path.read_text(encoding="utf-8").strip()
    try:
        data = json.loads(raw)
        if isinstance(data, str):
            return data
        return json.dumps(data, ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        return raw


def db_types(db_config_path: Path) -> set[str]:
    data = yaml.safe_load(db_config_path.read_text(encoding="utf-8"))
    return {
        str(client.get("db_type", "")).lower()
        for client in data.get("db_clients", {}).values()
    }


def write(path: Path, content: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)


def task_toml(task_name: str, dataset: str, query_id: str, types: set[str]) -> str:
    tags = ["dab", "data-agent", "database"] + sorted(t for t in types if t)
    tags_toml = "[" + ", ".join(json.dumps(t) for t in tags) + "]"
    needs_pg = "postgres" in types
    needs_mongo = "mongo" in types
    memory = 12288 if dataset in {"imdb", "PATENTS"} else 4096
    storage = 32768 if dataset in {"imdb", "PATENTS"} else 12288
    return f'''version = "1.0"

[metadata]
author_name = "UC Berkeley EPIC / Hasura PromptQL"
author_email = "unknown"
difficulty = "hard"
category = "data-agent"
tags = {tags_toml}
custom_docker_compose = true

[metadata.extra]
benchmark = "DataAgentBench"
dataset = "{dataset}"
query_id = "{query_id}"
needs_postgres = {str(needs_pg).lower()}
needs_mongo = {str(needs_mongo).lower()}

[verifier]
timeout_sec = 900.0

[agent]
timeout_sec = 3600.0
network_mode = "public"

[environment]
build_timeout_sec = 1800.0
cpus = 2
memory_mb = {memory}
storage_mb = {storage}
'''


def dockerfile() -> str:
    return r'''FROM python:3.12-slim

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG http_proxy
ARG https_proxy

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/dab \
    PG_HOST=postgres \
    PG_PORT=5432 \
    PG_USER=postgres \
    PG_PASSWORD=postgres \
    PG_DB=postgres \
    PG_CLIENT=psql \
    MONGO_URI=mongodb://mongo:27017/

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    pandas==2.3.0 \
    numpy==2.2.6 \
    duckdb==1.3.1 \
    psycopg2-binary==2.9.9 \
    pymongo==4.13.2 \
    sqlalchemy==2.0.41 \
    openpyxl==3.1.5 \
    xlrd==2.0.1 \
    python-dotenv==1.1.1 \
    pyyaml==6.0.2

WORKDIR /app
COPY dab/ /app/dab/
'''


def docker_compose(types: set[str]) -> str:
    depends: list[str] = []
    if "postgres" in types:
        depends.append(
            """      postgres:
        condition: service_healthy"""
        )
    if "mongo" in types:
        depends.append(
            """      mongo:
        condition: service_healthy"""
        )
    depends_block = ""
    if depends:
        depends_block = "\n    depends_on:\n" + "\n".join(depends)
    services = [
        r'''  main:
    build:
      context: ${CONTEXT_DIR}
      dockerfile: Dockerfile
      args:
        HTTP_PROXY: ${HTTP_PROXY:-}
        HTTPS_PROXY: ${HTTPS_PROXY:-}
        http_proxy: ${http_proxy:-}
        https_proxy: ${https_proxy:-}
    image: ${MAIN_IMAGE_NAME}
    command: ["bash", "-lc", "/app/dab/setup_databases.py && sleep infinity"]
    environment:
      - TEST_DIR=${TEST_DIR}
      - PYTHONPATH=/app/dab
      - PG_HOST=postgres
      - PG_PORT=5432
      - PG_USER=postgres
      - PG_PASSWORD=postgres
      - PG_DB=postgres
      - PG_CLIENT=psql
      - MONGO_URI=mongodb://mongo:27017/
      - HTTP_PROXY=${HTTP_PROXY:-}
      - HTTPS_PROXY=${HTTPS_PROXY:-}
      - http_proxy=${http_proxy:-}
      - https_proxy=${https_proxy:-}
      - NO_PROXY=postgres,mongo,localhost,127.0.0.1,::1
      - no_proxy=postgres,mongo,localhost,127.0.0.1,::1
    volumes:
      - ${HOST_VERIFIER_LOGS_PATH}:${ENV_VERIFIER_LOGS_PATH}
      - ${HOST_AGENT_LOGS_PATH}:${ENV_AGENT_LOGS_PATH}
''' + depends_block + r'''
    deploy:
      resources:
        limits:
          cpus: ${CPUS}
          memory: ${MEMORY}'''
    ]
    if "postgres" in types:
        services.append(
            r'''  postgres:
    image: postgres:17
    environment:
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=postgres
      - POSTGRES_DB=postgres
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d postgres"]
      interval: 5s
      timeout: 5s
      retries: 30'''
        )
    if "mongo" in types:
        services.append(
            r'''  mongo:
    image: mongo:8
    command: ["mongod", "--quiet", "--bind_ip_all"]
    healthcheck:
      test: ["CMD-SHELL", "mongosh --quiet --eval 'db.adminCommand({ ping: 1 }).ok' | grep 1"]
      interval: 5s
      timeout: 5s
      retries: 30'''
        )
    return "services:\n" + "\n\n".join(services) + "\n"


def setup_databases_py() -> str:
    return r'''#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import psycopg2
import pymongo
import yaml
from bson import decode_file_iter

ROOT = Path("/app/dab")
CONFIG = ROOT / "db_config.yaml"


def load_config():
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8")).get("db_clients", {})


def wait_postgres(timeout=180):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            conn = psycopg2.connect(
                dbname="postgres",
                user=os.environ.get("PG_USER", "postgres"),
                password=os.environ.get("PG_PASSWORD", "postgres"),
                host=os.environ.get("PG_HOST", "postgres"),
                port=int(os.environ.get("PG_PORT", "5432")),
            )
            conn.close()
            return
        except Exception as exc:
            last = exc
            time.sleep(2)
    raise RuntimeError(f"PostgreSQL did not become ready: {last}")


def wait_mongo(timeout=180):
    deadline = time.time() + timeout
    uri = os.environ.get("MONGO_URI", "mongodb://mongo:27017/")
    last = None
    while time.time() < deadline:
        try:
            client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=2000)
            client.admin.command("ping")
            client.close()
            return
        except Exception as exc:
            last = exc
            time.sleep(2)
    raise RuntimeError(f"MongoDB did not become ready: {last}")


def pg_quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def load_postgres(db_name: str, sql_file: str):
    wait_postgres()
    user = os.environ.get("PG_USER", "postgres")
    password = os.environ.get("PG_PASSWORD", "postgres")
    host = os.environ.get("PG_HOST", "postgres")
    port = str(os.environ.get("PG_PORT", "5432"))
    conn = psycopg2.connect(dbname="postgres", user=user, password=password, host=host, port=port)
    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid <> pg_backend_pid();", (db_name,))
    cur.execute(f"DROP DATABASE IF EXISTS {pg_quote(db_name)};")
    cur.execute(f"CREATE DATABASE {pg_quote(db_name)} WITH ENCODING='UTF8' LC_COLLATE='C' LC_CTYPE='C' TEMPLATE=template0;")
    cur.close()
    conn.close()
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    env["PGCLIENTENCODING"] = "UTF8"
    cmd = ["psql", f"-h{host}", f"-p{port}", f"-U{user}", "-d", db_name, "-v", "ON_ERROR_STOP=1", "-f", str(ROOT / sql_file)]
    subprocess.run(cmd, check=True, env=env)


def load_mongo(db_name: str, dump_folder: str):
    wait_mongo()
    uri = os.environ.get("MONGO_URI", "mongodb://mongo:27017/")
    client = pymongo.MongoClient(uri)
    client.drop_database(db_name)
    db = client[db_name]
    folder = ROOT / dump_folder
    bson_files = sorted(folder.rglob("*.bson"))
    if not bson_files:
        raise FileNotFoundError(f"No .bson files found under {folder}")
    for bson_path in bson_files:
        collection = bson_path.stem
        batch = []
        with bson_path.open("rb") as fh:
            for doc in decode_file_iter(fh):
                batch.append(doc)
                if len(batch) >= 1000:
                    db[collection].insert_many(batch)
                    batch.clear()
        if batch:
            db[collection].insert_many(batch)
    client.close()


def main():
    clients = load_config()
    for logical, cfg in clients.items():
        typ = str(cfg.get("db_type", "")).lower()
        print(f"[setup] {logical}: {typ}", flush=True)
        if typ == "postgres":
            load_postgres(cfg["db_name"], cfg["sql_file"])
        elif typ == "mongo":
            load_mongo(cfg["db_name"], cfg["dump_folder"])
    print("[setup] done", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[setup] failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        raise
'''


def query_db_py() -> str:
    return r'''#!/usr/bin/env python3
"""Convenience database helper for DAB Harbor tasks."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

import duckdb
import pandas as pd
import pymongo
import sqlalchemy
import yaml

ROOT = Path("/app/dab")
CONFIG = ROOT / "db_config.yaml"


def load_clients():
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    clients = data.get("db_clients", {})
    for cfg in clients.values():
        for key in ("db_path", "sql_file", "dump_folder"):
            if key in cfg:
                cfg[key] = str(ROOT / cfg[key])
    return clients


def records(df: pd.DataFrame):
    return json.dumps(df.to_dict(orient="records"), ensure_ascii=False, default=str)


def list_items(name, cfg):
    typ = cfg["db_type"]
    if typ == "sqlite":
        conn = sqlite3.connect(cfg["db_path"])
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        conn.close()
        return [r[0] for r in rows]
    if typ == "duckdb":
        conn = duckdb.connect(cfg["db_path"], read_only=True)
        rows = conn.execute("SHOW TABLES").fetchall()
        conn.close()
        return [r[0] for r in rows]
    if typ == "postgres":
        uri = f"postgresql+psycopg2://{os.environ.get('PG_USER','postgres')}:{os.environ.get('PG_PASSWORD','postgres')}@{os.environ.get('PG_HOST','postgres')}:{os.environ.get('PG_PORT','5432')}/{cfg['db_name']}"
        with sqlalchemy.create_engine(uri).connect() as conn:
            df = pd.read_sql("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name", conn)
        return df.iloc[:, 0].tolist()
    if typ == "mongo":
        client = pymongo.MongoClient(os.environ.get("MONGO_URI", "mongodb://mongo:27017/"))
        out = client[cfg["db_name"]].list_collection_names()
        client.close()
        return out
    raise ValueError(f"Unsupported db_type for {name}: {typ}")


def run_query(name, cfg, query):
    typ = cfg["db_type"]
    if typ == "sqlite":
        conn = sqlite3.connect(cfg["db_path"])
        df = pd.read_sql_query(query, conn)
        conn.close()
        return records(df)
    if typ == "duckdb":
        conn = duckdb.connect(cfg["db_path"], read_only=True)
        df = conn.execute(query).fetchdf()
        conn.close()
        return records(df)
    if typ == "postgres":
        uri = f"postgresql+psycopg2://{os.environ.get('PG_USER','postgres')}:{os.environ.get('PG_PASSWORD','postgres')}@{os.environ.get('PG_HOST','postgres')}:{os.environ.get('PG_PORT','5432')}/{cfg['db_name']}"
        with sqlalchemy.create_engine(uri).connect() as conn:
            df = pd.read_sql(sqlalchemy.text(query), conn)
        return records(df)
    if typ == "mongo":
        payload = json.loads(query)
        client = pymongo.MongoClient(os.environ.get("MONGO_URI", "mongodb://mongo:27017/"))
        db = client[cfg["db_name"]]
        cursor = db[payload["collection"]].find(payload.get("filter", {}), payload.get("projection"))
        if payload.get("limit") is not None:
            cursor = cursor.limit(int(payload.get("limit", 20)))
        out = list(cursor)
        client.close()
        return json.dumps(out, ensure_ascii=False, default=str)
    raise ValueError(f"Unsupported db_type for {name}: {typ}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["dbs", "tables", "query"])
    parser.add_argument("db_name", nargs="?")
    parser.add_argument("query", nargs="?")
    args = parser.parse_args()
    clients = load_clients()
    if args.command == "dbs":
        print(json.dumps({k: v["db_type"] for k, v in clients.items()}, indent=2))
        return
    if args.db_name not in clients:
        raise SystemExit(f"Unknown db_name {args.db_name!r}. Run: query_db.py dbs")
    if args.command == "tables":
        print(json.dumps(list_items(args.db_name, clients[args.db_name]), indent=2, ensure_ascii=False))
    else:
        if args.query is None:
            raise SystemExit("query text is required")
        print(run_query(args.db_name, clients[args.db_name], args.query))


if __name__ == "__main__":
    main()
'''


def evaluate_py() -> str:
    return r'''#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

APP = Path("/app")
DAB = APP / "dab"
PRIVATE_QUERY = Path("/tests/dab_query")
ANSWER = APP / "answer.txt"
REWARD = Path("/logs/verifier/reward.txt")
DETAILS = Path("/logs/verifier/dab_result.json")


def load_validator(path: Path):
    sys.path.insert(0, str(DAB))
    spec = importlib.util.spec_from_file_location("dab_validate", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load validator: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate


def main() -> int:
    REWARD.parent.mkdir(parents=True, exist_ok=True)
    if not ANSWER.exists():
        result = {"is_valid": False, "reason": "/app/answer.txt not found"}
    else:
        answer = ANSWER.read_text(encoding="utf-8", errors="replace").strip()
        validate = load_validator(PRIVATE_QUERY / "validate.py")
        ok, reason = validate(answer)
        result = {"is_valid": bool(ok), "reason": str(reason), "llm_answer": answer}
    DETAILS.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    REWARD.write_text("1\n" if result["is_valid"] else "0\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def test_sh() -> str:
    return r'''#!/usr/bin/env bash
set -Eeuo pipefail
mkdir -p /logs/verifier
python3 /tests/evaluate.py
'''


def solve_sh(ground_truth: str) -> str:
    return "#!/usr/bin/env bash\nset -euo pipefail\ncat > /app/answer.txt <<'EOF'\n" + ground_truth.strip() + "\nEOF\n"


def instruction(dataset: str, query_id: str, query_text: str, description: str, hints: str | None, types: set[str]) -> str:
    hint_block = f"\n\nAdditional dataset hints:\n\n{hints.strip()}\n" if hints else ""
    db_note = ", ".join(sorted(types))
    return f'''You are solving a DataAgentBench data question.

Dataset: `{dataset}`
Query: `{query_id}`
Database systems in this task: `{db_note}`

Question:

{query_text.strip()}

Database description:

{description.strip()}
{hint_block}
Working files:

- `/app/dab/query/query.json`: the original question JSON.
- `/app/dab/db_config.yaml`: logical database names and physical database files.
- `/app/dab/query_db.py`: helper CLI for database access.

Use `/app/dab/query_db.py dbs` to list logical databases, `/app/dab/query_db.py tables <logical_db_name>` to inspect tables or collections, and `/app/dab/query_db.py query <logical_db_name> '<SQL-or-Mongo-JSON>'` to run read-only queries. For MongoDB queries, pass a JSON object such as `{{"collection":"items","filter":{{}},"limit":5}}`.

Write only your final answer to `/app/answer.txt`. A private verifier will check that file after the agent exits.
'''


def generate_task(dab_root: Path, output_root: Path, dataset_dir: Path, query_dir: Path, use_hints: bool) -> str:
    dataset = dataset_dir.name.replace("query_", "")
    query_id = query_dir.name.replace("query", "")
    task_id = f"dab__{sanitize(dataset)}__query{query_id}"
    out = output_root / task_id
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    query_text = read_query(query_dir / "query.json")
    description = (dataset_dir / "db_description.txt").read_text(encoding="utf-8")
    hint_path = dataset_dir / "db_description_withhint.txt"
    hints = hint_path.read_text(encoding="utf-8") if use_hints and hint_path.exists() else None
    types = db_types(dataset_dir / "db_config.yaml")
    ground_truth = (query_dir / "ground_truth.csv").read_text(encoding="utf-8", errors="replace")

    write(out / "task.toml", task_toml(task_id, dataset, query_id, types))
    write(out / "instruction.md", instruction(dataset, query_id, query_text, description, hints, types))
    write(out / "environment" / "Dockerfile", dockerfile())
    write(out / "environment" / "docker-compose.yaml", docker_compose(types))
    write(out / "tests" / "evaluate.py", evaluate_py(), 0o755)
    write(out / "tests" / "test.sh", test_sh(), 0o755)
    # Harbor mounts /tests only for verification. Keep both the validator and
    # any answer file it reads next to each other there; neither is baked into
    # the agent-visible /app image.
    link_or_copy(query_dir / "validate.py", out / "tests" / "dab_query" / "validate.py")
    link_or_copy(query_dir / "ground_truth.csv", out / "tests" / "dab_query" / "ground_truth.csv")
    write(out / "solution" / "solve.sh", solve_sh(ground_truth), 0o755)

    dab = out / "environment" / "dab"
    copy_tree_linked(dab_root / "common_scaffold", dab / "common_scaffold")
    copy_tree_linked(dataset_dir / "query_dataset", dab / "query_dataset")
    # The question is public task input.  Never copy the whole original query
    # directory: it also contains ground_truth.csv and validate.py.
    link_or_copy(query_dir / "query.json", dab / "query" / "query.json")
    for name in ("db_config.yaml", "db_description.txt", "db_description_withhint.txt"):
        src = dataset_dir / name
        if src.exists():
            link_or_copy(src, dab / name)
    write(dab / "setup_databases.py", setup_databases_py(), 0o755)
    write(dab / "query_db.py", query_db_py(), 0o755)
    return task_id


def parse_dataset_filter(raw: str | None) -> set[str] | None:
    if not raw:
        return None
    return {x.strip().replace("query_", "") for x in raw.split(",") if x.strip()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dab-root", type=Path, default=DEFAULT_DAB_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--datasets", default="", help="Comma-separated dataset names, without or with query_ prefix.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of tasks to generate. 0 means all.")
    parser.add_argument("--use-hints", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    dab_root = args.dab_root.resolve()
    output = args.output_dir.resolve()
    if not dab_root.exists():
        raise SystemExit(f"DAB root does not exist: {dab_root}")
    if output.exists() and args.overwrite:
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    allowed = parse_dataset_filter(args.datasets)
    generated: list[str] = []
    for dataset_dir in sorted(dab_root.glob("query_*")):
        if not (dataset_dir / "db_config.yaml").exists():
            continue
        dataset = dataset_dir.name.replace("query_", "")
        if allowed is not None and dataset not in allowed:
            continue
        def qkey(path: Path):
            suffix = path.name.replace("query", "")
            return (0, int(suffix)) if suffix.isdigit() else (1, suffix)
        query_dirs = [p for p in dataset_dir.glob("query*") if (p / "query.json").exists()]
        for query_dir in sorted(query_dirs, key=qkey):
            generated.append(generate_task(dab_root, output, dataset_dir, query_dir, args.use_hints))
            if args.limit and len(generated) >= args.limit:
                break
        if args.limit and len(generated) >= args.limit:
            break

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "benchmark": "DataAgentBench",
        "dab_root": str(dab_root),
        "use_hints": args.use_hints,
        "task_count": len(generated),
        "tasks": generated,
    }
    write(output / "manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(f"Generated {len(generated)} DAB Harbor tasks in {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
