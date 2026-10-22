#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""changelog_notes.py — 从 CHANGELOG.md 提取指定版本的发布说明。

CI 用它生成 GitHub Release 的正文，避免把整个 CHANGELOG 贴上去。

用法:
    python tools/changelog_notes.py 1.0.2 notes.md
    python tools/changelog_notes.py 1.0.2            # 直接打印到 stdout
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"


def extract(version: str) -> str:
    text = CHANGELOG.read_text(encoding="utf-8")
    pattern = rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## |\Z)"
    m = re.search(pattern, text, re.S | re.M)
    if not m or not m.group(1).strip():
        return f"Release {version}"
    return m.group(1).strip()


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print("用法: python tools/changelog_notes.py <版本号> [输出文件]",
              file=sys.stderr)
        return 2
    version = argv[1].strip().lstrip("v")
    body = extract(version) + "\n"
    if len(argv) == 3:
        pathlib.Path(argv[2]).write_text(body, encoding="utf-8")
        print(f"已写入 {argv[2]}（{len(body)} 字符）")
    else:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
