# -*- coding: utf-8 -*-
"""python -m pydiag.vendor fetch <driver> — 下载 Linux/macOS 厂商库到 vendor 目录。

Windows 的 DLL 已随 wheel 预置, 无需 fetch。用法:

    python -m pydiag.vendor fetch pcan      # macOS: 下载 PEAK PCAN-Basic
    python -m pydiag.vendor fetch zlg       # Linux: 下载 ZLG Linux SDK (libzlgcan.so)
    python -m pydiag.vendor status          # 查看各驱动当前状态
"""
from __future__ import annotations

import os
import platform
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

from . import VENDOR_DIR, find_vendor_lib, LIB_NAMES

_OS = ("windows" if sys.platform.startswith("win")
       else "macos" if sys.platform.startswith("darwin") else "linux")

# 官方 SDK 下载地址 (厂商页面/直链; 厂商改版时以官方页为准)
OFFICIAL_PAGES = {
    ("zlg", "linux"): "https://www.zlg.cn/index.php/Portals/Module/Download/list/moduleid/19.html",
    ("pcan", "macos"): "https://www.peak-system.com/PCAN-Basic.199.0.html",
}

# fetch 能自动化的包 (官方提供稳定直链或包内 README 给出手动步骤)
FETCH_STEPS = {
    "zlg": {
        "linux": [
            "1. 打开 ZLG 官网资料下载页: {url}",
            "2. 搜索你的设备型号 (如 USBCANFD-200U) → Linux 驱动, 下载 SDK 包",
            "3. 解压得到 libzlgcan.so",
            "4. python -m pydiag.vendor install zlg <path-to-libzlgcan.so>",
        ],
        "macos": [
            "ZLG 未提供 macOS 库, zlg 驱动在 macOS 不可用。",
            "替代: Linux 虚拟机 + USB 透传, 或换用 PEAK PCAN-USB。",
        ],
    },
    "pcan": {
        "windows": ["PCANBasic.dll 已预置于 vendor/pcan/, 无需操作。"],
        "macos": [
            "1. 打开 PEAK 官网下载页: {url}",
            "2. 下载 PCAN-Basic for macOS (.pkg)",
            "3. 双击安装 (装到 /Library/Frameworks/PCANBasic.framework)",
            "   或: python -m pydiag.vendor install pcan <libpcanbasic.dylib>",
        ],
    },
    "vector": {
        "windows": ["vxlapi64.dll 已预置于 vendor/vector/, 无需操作。"],
        "linux": ["Vector 未发布 Linux 版 XL Driver Library, vector 驱动仅 Windows。"],
        "macos": ["Vector 未发布 macOS 版 XL Driver Library, vector 驱动仅 Windows。"],
    },
}


def _status() -> int:
    from ..platform import OS_NAME
    print(f"platform: {_OS}  (vendor dir: {VENDOR_DIR})")
    print()
    print(f"{'driver':<10}{'found':<8}library")
    for drv in ("zlg", "pcan", "vector"):
        p = find_vendor_lib(drv, _OS)
        mark = "YES" if p else "-"
        print(f"{drv:<10}{mark:<8}{p or '(not vendored; system paths will be tried)'}")
    return 0


def _install(args) -> int:
    if len(args) < 2:
        print("usage: python -m pydiag.vendor install <driver> <libfile>")
        return 2
    drv, src = args[0].lower(), args[1]
    if drv not in LIB_NAMES:
        print(f"unknown driver: {drv} (zlg/pcan/vector)")
        return 2
    if not os.path.isfile(src):
        print(f"not a file: {src}")
        return 2
    dst_dir = os.path.join(VENDOR_DIR, drv)
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, os.path.basename(src))
    shutil.copy2(src, dst)
    os.chmod(dst, 0o755)
    print(f"installed: {dst}")
    return 0


def _fetch(args) -> int:
    if not args:
        print("usage: python -m pydiag.vendor fetch <driver>  (zlg/pcan/vector)")
        return 2
    drv = args[0].lower()
    steps = FETCH_STEPS.get(drv, {}).get(_OS)
    if steps is None:
        print(f"{drv} 在 {_OS} 已预置或不受支持, 运行: python -m pydiag.vendor status")
        return 1
    url = OFFICIAL_PAGES.get((drv, _OS), "")
    for s in steps:
        print(s.format(url=url))
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd = argv[0].lower()
    if cmd == "status":
        return _status()
    if cmd == "install":
        return _install(argv[1:])
    if cmd == "fetch":
        return _fetch(argv[1:])
    print(f"unknown command: {cmd} (status/install/fetch)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
