# -*- coding: utf-8 -*-
"""pydiag.platform — 跨平台支持层。

职责:
    1. 识别当前操作系统 (windows / linux / macos);
    2. 给出各设备驱动在当前平台的可用性结论 (驱动未移植时返回 False 而非崩溃);
    3. 提供平台相关的 DLL 加载辅助 (WinDLL vs CDLL)。

平台支持矩阵:
    ┌─────────┬───────┬──────┬────────┬───────────┬──────────┐
    │ 驱动     │ Windows│ Linux│ macOS  │ 传输方式   │ 说明      │
    ├─────────┼───────┼──────┼────────┼───────────┼──────────┤
    │ virtual │ ✅    │ ✅   │ ✅     │ 进程内回环 │ 无硬件    │
    │ socketcan│ ❌    │ ✅   │ ❌     │ AF_CAN    │ 内核内置  │
    │ zlg     │ ✅    │ ✅*  │ ❌     │ 厂商库     │ *Linux 需 libzlgcan.so │
    │ pcan    │ ✅    │ ❌   │ ✅*    │ PCANBasic │ *macOS 需官方 PCANBasic.framework │
    │ vector  │ ✅    │ ❌   │ ❌     │ vxlapi    │ 厂商 DLL  │
    └─────────┴───────┴──────┴────────┴───────────┴──────────┘
"""
from __future__ import annotations

import ctypes
import os
import sys
from typing import List, Optional

__all__ = [
    "OS_NAME", "IS_WINDOWS", "IS_LINUX", "IS_MACOS",
    "DRIVER_PLATFORMS", "driver_supported_on_platform",
    "platform_supported_drivers", "load_dll",
]

#: 当前系统名: "windows" / "linux" / "macos"
if sys.platform.startswith("win"):
    OS_NAME = "windows"
elif sys.platform.startswith("darwin"):
    OS_NAME = "macos"
else:
    OS_NAME = "linux"

IS_WINDOWS = OS_NAME == "windows"
IS_LINUX = OS_NAME == "linux"
IS_MACOS = OS_NAME == "macos"

#: 各驱动支持的操作系统 (驱动未列入 = 任何平台都不支持)
DRIVER_PLATFORMS = {
    "virtual": ("windows", "linux", "macos"),
    "socketcan": ("linux",),
    "zlg": ("windows", "linux"),
    "pcan": ("windows", "macos"),
    "vector": ("windows",),
}


def driver_supported_on_platform(driver: str, os_name: Optional[str] = None) -> bool:
    """判断驱动在指定平台是否受支持（不含驱动文件是否存在）。"""
    os_name = os_name or OS_NAME
    return os_name in DRIVER_PLATFORMS.get(driver, ())


def platform_supported_drivers(os_name: Optional[str] = None) -> List[str]:
    """当前平台受支持的驱动名列表（注册顺序）。"""
    os_name = os_name or OS_NAME
    return [d for d, plats in DRIVER_PLATFORMS.items() if os_name in plats]


def load_dll(path: str):
    """按平台加载动态库：Windows 用 WinDLL(stdcall)，其它用 CDLL。"""
    if IS_WINDOWS:
        return ctypes.WinDLL(path)
    return ctypes.CDLL(path)
