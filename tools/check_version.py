#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_version.py — 校验版本号在 pyproject / __init__ / 标签之间一致。

CI 在打标签后调用，防止「标签写 v1.0.3、包里还是 1.0.2」这种发布事故。

用法:
    python tools/check_version.py 1.0.2      # 一致则 exit 0
    python tools/check_version.py v1.0.2     # 前导 v 会自动去掉
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def read_pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        raise SystemExit("pyproject.toml 里找不到 version")
    return m.group(1)


def read_package_version() -> str:
    text = (ROOT / "src" / "pydiag" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not m:
        raise SystemExit("__init__.py 里找不到 __version__")
    return m.group(1)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("用法: python tools/check_version.py <版本号>", file=sys.stderr)
        return 2
    expected = argv[1].strip().lstrip("v")
    got_pyproject = read_pyproject_version()
    got_pkg = read_package_version()

    print(f"标签版本   : {expected}")
    print(f"pyproject  : {got_pyproject}")
    print(f"__init__.py: {got_pkg}")

    bad = []
    if got_pyproject != expected:
        bad.append(f"pyproject.toml 是 {got_pyproject}")
    if got_pkg != expected:
        bad.append(f"__init__.py 是 {got_pkg}")
    if bad:
        print("\n[FAIL] 版本号不一致: " + "; ".join(bad), file=sys.stderr)
        print(f"       标签 {expected} 要求两处都是 {expected}", file=sys.stderr)
        return 1
    print("\n[OK] 三处版本号一致")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
