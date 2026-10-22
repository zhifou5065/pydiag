# -*- coding: utf-8 -*-
"""pydiag.vendor — 预置厂商驱动库 + Linux/macOS 库获取工具。

目录结构 (只预置 zlg + pcan; vector 因 EULA 限制不预置):
    vendor/
    ├── zlg/      (Linux: libzlgcan.so 可 install 到此; Windows 库在 zlg_runtime)
    └── pcan/     PCANBasic.dll (Windows, 预置) + libpcanbasic.dylib (macOS, install)

库加载优先级 (所有驱动统一): 系统/官方安装的库优先 → 包内预置回退。
即: 用户装了官方驱动就用官方的; 没装时用包内预置, 保证开箱即用。

Linux/macOS 的共享库由各厂商以独立 SDK 分发:
    python -m pydiag.vendor fetch <driver>     查看获取步骤
    python -m pydiag.vendor install <driver> <libfile>   入位

版权说明:
    本目录内的 DLL 来自各厂商官方安装包, 版权归原厂所有 (PEAK-System /
    周立功 ZLG)。 redistribution 依据各厂商 SDK 许可条款;
    若你所在法域不允许再分发, 请删除对应文件并自行安装官方驱动,
    pydiag 会自动回退到系统路径查找。
"""
from __future__ import annotations

import os
import sys

__all__ = ["VENDOR_DIR", "vendor_lib_dir", "find_vendor_lib", "add_vendor_dir"]

#: 本包 vendor 目录 (site-packages/pydiag/vendor)
VENDOR_DIR = os.path.dirname(os.path.abspath(__file__))

#: 各驱动的库文件名 (按平台)
LIB_NAMES = {
    "zlg": {
        "windows": ["zlgcan.dll"],
        "linux": ["libzlgcan.so", "libzlgcan.so.1", "libzlgcan.so.2"],
        "macos": ["libzlgcan.dylib"],
    },
    "pcan": {
        "windows": ["PCANBasic.dll"],
        "linux": ["libpcanbasic.so"],
        "macos": ["libpcanbasic.dylib", "PCANBasic"],
    },
    "vector": {
        "windows": ["vxlapi64.dll", "vxlapi.dll"],
        "linux": ["vxlapi64.so"],
        "macos": ["vxlapi.dylib"],
    },
}


def vendor_lib_dir(driver: str) -> str:
    """某驱动的 vendor 子目录路径 (目录不一定存在)。"""
    return os.path.join(VENDOR_DIR, driver)


def find_vendor_lib(driver: str, os_name: str) -> str:
    """在 vendor 目录里查找某驱动某平台的库文件, 返回绝对路径或 ""。"""
    names = LIB_NAMES.get(driver, {}).get(os_name, [])
    d = vendor_lib_dir(driver)
    for n in names:
        p = os.path.join(d, n)
        if os.path.isfile(p):
            return p
    # macOS framework 布局: vendor/pcan/PCANBasic.framework/PCANBasic
    fw = os.path.join(d, "PCANBasic.framework", "PCANBasic")
    if os_name == "macos" and os.path.isfile(fw):
        return fw
    return ""


def add_vendor_dir(driver: str, os_name: str = "") -> bool:
    """把驱动的 vendor 目录注册进 DLL/库搜索路径 (Windows add_dll_directory,
    其它平台设置 DYLD/LD 环境变量由启动器处理)。返回是否找到库。"""
    os_name = os_name or ("windows" if sys.platform.startswith("win")
                          else "macos" if sys.platform.startswith("darwin")
                          else "linux")
    if not find_vendor_lib(driver, os_name):
        return False
    d = vendor_lib_dir(driver)
    if os_name == "windows":
        try:
            os.add_dll_directory(d)
        except (OSError, AttributeError):
            pass
    return True
