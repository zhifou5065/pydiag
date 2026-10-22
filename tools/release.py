#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""release.py — 一条命令完成「升版本 → 同步 GitHub → 打标签」。

用法:
    python tools/release.py 1.0.3 "修复 XXX 问题"
    python tools/release.py 1.1.0 "新增 YYY 能力" --dry-run

做的事（按顺序，任何一步失败立即中止，不留半成品）:
    1. 安全检查：工作区必须干净、当前分支必须是 main、标签不能已存在
    2. 改 pyproject.toml 与 src/pydiag/__init__.py 的版本号
    3. 在 CHANGELOG.md 的 [Unreleased] 下面插入 [新版本] - 日期 一节
    4. git commit → git tag -a vX.Y.Z → 推送分支与标签

推送标签后，GitHub Actions 的 .github/workflows/release.yml 会接手：
校验标签与版本号一致 → 构建 sdist/wheel → twine check → 创建 GitHub Release。
"""
from __future__ import annotations

import argparse
import datetime
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
INIT_PY = ROOT / "src" / "pydiag" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True,
                          check=check)


def die(msg: str) -> None:
    print(f"[中止] {msg}", file=sys.stderr)
    sys.exit(1)


def ensure_clean_state(version: str) -> None:
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    if branch != "main":
        die(f"当前分支是 {branch}，发布请在 main 上进行")

    if run(["git", "status", "--porcelain"]).stdout.strip():
        die("工作区有未提交的改动，先提交或 stash 再来")

    if run(["git", "tag", "-l", f"v{version}"]).stdout.strip():
        die(f"标签 v{version} 已存在")


def bump_version(version: str) -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    new, n = re.subn(r'^version\s*=\s*"[^"]+"',
                     f'version = "{version}"', text, count=1, flags=re.M)
    if n != 1:
        die("pyproject.toml 里没找到 version 行")
    PYPROJECT.write_text(new, encoding="utf-8")

    text = INIT_PY.read_text(encoding="utf-8")
    new, n = re.subn(r'__version__\s*=\s*"[^"]+"',
                     f'__version__ = "{version}"', text, count=1)
    if n != 1:
        die("__init__.py 里没找到 __version__ 行")
    INIT_PY.write_text(new, encoding="utf-8")


def update_changelog(version: str, notes: str) -> None:
    text = CHANGELOG.read_text(encoding="utf-8")
    m = re.search(r"^## \[Unreleased\]\s*$", text, re.M)
    if not m:
        die("CHANGELOG.md 缺少 '## [Unreleased]' 标题")
    today = datetime.date.today().isoformat()
    section = f"\n\n## [{version}] - {today}\n\n{notes.strip()}\n"
    CHANGELOG.write_text(text[:m.end()] + section + text[m.end():],
                         encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="升版本 + 打标签 + 推送")
    ap.add_argument("version", help="新版本号，形如 1.0.3")
    ap.add_argument("notes", help="一句话说明这个版本改了什么（写进 CHANGELOG）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不执行")
    args = ap.parse_args()

    version = args.version.strip().lstrip("v")
    if not SEMVER.match(version):
        die(f"版本号格式不对: {args.version}（要 X.Y.Z）")

    ensure_clean_state(version)

    print(f"[1/4] 版本号 -> {version}")
    print(f"[2/4] CHANGELOG 插入 [{version}] 一节")
    print(f"[3/4] git commit + tag v{version}")
    print(f"[4/4] git push（分支 + 标签）")
    if args.dry_run:
        print("\n--dry-run: 以上动作均未执行")
        return 0

    bump_version(version)
    update_changelog(version, args.notes)

    run(["git", "add", "pyproject.toml", "src/pydiag/__init__.py", "CHANGELOG.md"])
    run(["git", "commit", "-m", f"chore(release): v{version}\n\n{args.notes}"])
    run(["git", "tag", "-a", f"v{version}", "-m", f"pydiag {version}\n\n{args.notes}"])
    run(["git", "push", "origin", "main"])
    run(["git", "push", "origin", f"v{version}"])

    print(f"\n[完成] v{version} 已推送。GitHub Actions 会自动创建 Release，"
          f"PyPI 上传请另行执行: twine upload dist/*")
    return 0


if __name__ == "__main__":
    sys.exit(main())
