#!/usr/bin/env python3
"""一次性 patch 脚本：把 SWE-Atlas 每 task 的 evaluate_tests.py 替换为支持
responses / chat 双路径的统一版本。

90 份 evaluate_tests.py 改前 md5 完全相同；以 benchmark/SWE-Atlas/data/tw/
task-6902ef3ab97fe23e2ad27255/tests/evaluate_tests.py（已手工改好）为标准版，
整体覆盖其余 89 份。幂等：重跑只把内容归一，不重复叠加。

用法：
    python scripts/_patch_sweatlas_evaluator.py
    python scripts/_patch_sweatlas_evaluator.py --check   # 只校验是否全部一致，不改

SWE-Atlas 数据集若更新会把 evaluate_tests.py 覆盖回原版，届时重跑本脚本即可。
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STANDARD = ROOT / "benchmark" / "SWE-Atlas" / "data" / "tw" / "task-6902ef3ab97fe23e2ad27255" / "tests" / "evaluate_tests.py"
GLOB = "benchmark/SWE-Atlas/data/*/task-*/tests/evaluate_tests.py"


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="只校验一致性，不写盘")
    args = ap.parse_args()

    if not STANDARD.exists():
        raise SystemExit(f"标准版不存在：{STANDARD}（先手工改好这一份）")
    standard_bytes = STANDARD.read_bytes()
    standard_md5 = md5(STANDARD)
    print(f"[patch] 标准版：{STANDARD}  md5={standard_md5}  {len(standard_bytes)} bytes")

    targets = sorted(ROOT.glob(GLOB))
    if not targets:
        raise SystemExit(f"未找到目标文件：{GLOB}")
    # 标准版自身也在列表里，跳过
    targets = [t for t in targets if t.resolve() != STANDARD.resolve()]
    print(f"[patch] 目标 {len(targets)} 份（不含标准版）")

    changed, already, failed = 0, 0, 0
    for t in targets:
        try:
            if md5(t) == standard_md5:
                already += 1
                continue
            if args.check:
                changed += 1
                print(f"  [diff] {t.relative_to(ROOT)}")
                continue
            t.write_bytes(standard_bytes)
            changed += 1
        except OSError as exc:
            failed += 1
            print(f"  [FAIL] {t}: {exc}")

    action = "checked" if args.check else "patched"
    print(f"[patch] {action}: {changed} changed, {already} already-ok, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
