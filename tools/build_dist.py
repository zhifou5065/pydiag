# -*- coding: utf-8 -*-
"""build_dist.py — 构建 sdist / wheel 的便捷入口。

实际构建由 PEP 517 后端完成（pyproject.toml 的 build-system），本脚本只做两件事：
  1. 清掉上次的 dist/ 与 build/ 残留
  2. 调用 `python -m build` 产出 dist/pydiag-<ver>-py3-none-any.whl 与 dist/pydiag-<ver>.tar.gz

用法:
    python -m pip install build
    python tools/build_dist.py

历史说明: 早期版本这里是一套纯标准库手写的打包逻辑（离线可用）。手写 wheel 的
entry_points.txt / WHEEL 元数据容易出错，现已改回标准后端，避免产物与 pyproject.toml 漂移。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def clean() -> None:
    for name in ("dist", "build", os.path.join("src", "pydiag.egg-info")):
        path = os.path.join(ROOT, name)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            print(f"[clean] 已删除 {name}/")


def main() -> int:
    clean()
    cmd = [sys.executable, "-m", "build"]
    # --no-isolation 复用当前环境的 setuptools/wheel，避免构建期联网拉依赖
    if os.environ.get("PYDIAG_BUILD_NO_ISOLATION", "1") == "1":
        cmd.append("--no-isolation")
    print("[build]", " ".join(cmd))
    return subprocess.call(cmd, cwd=ROOT)


if __name__ == "__main__":
    try:
        import build  # noqa: F401
    except ImportError:
        sys.exit("缺少 build 模块，先执行: python -m pip install build")
    sys.exit(main())
