# -*- coding: utf-8 -*-
"""pydiag.devices — 多厂商 CAN/LIN 设备适配层。

对外暴露:
    DeviceManager          设备管理器（枚举/独占打开/生命周期）
    DeviceInfo             设备描述
    BaseChannel            统一通道接口（zuds.VirtualBus 兼容）
    BusType                总线类型常量（can/lin）
    LinChannelBase         LIN 通道基类（zuds.LinBus 兼容）
    DualBusVirtualDevice   双总线虚拟设备（CAN+LIN 演示/测试）
"""
from __future__ import annotations

from typing import Optional

from .core import (DeviceInfo, BaseChannel, BaseCanDevice, BaseDriver,
                   DeviceManager)
from .zlg import ZlgDriver, ZlgDevice, ZlgChannel
from .pcan import PcanDriver, PcanDevice, PcanChannel
from .vector import VectorDriver, VectorDevice, VectorChannel
from .virtual import VirtualChannel, VirtualCanDevice
from .socketcan import SocketCanDriver, SocketCanDevice, SocketCanChannel
from .lin import (BusType, LinChannelBase, VirtualLinChannel,
                  DualBusVirtualDevice)

__all__ = [
    "DeviceInfo", "BaseChannel", "BaseCanDevice", "BaseDriver",
    "DeviceManager",
    "ZlgDriver", "ZlgDevice", "ZlgChannel",
    "PcanDriver", "PcanDevice", "PcanChannel",
    "VectorDriver", "VectorDevice", "VectorChannel",
    "SocketCanDriver", "SocketCanDevice", "SocketCanChannel",
    "VirtualChannel", "VirtualCanDevice",
    "BusType", "LinChannelBase", "VirtualLinChannel", "DualBusVirtualDevice",
]


def default_manager(include_virtual: bool = False,
                    only_drivers: Optional[str] = None) -> DeviceManager:
    """构建带内置驱动的 DeviceManager（注册顺序即枚举顺序）。

    only_drivers: 只注册指定驱动（如 "pcan"）——不加载无关厂商的 DLL。
    """
    if only_drivers:
        # 延迟导入目标驱动, 未指定的完全不加载
        registry = {"zlg": ".zlg:ZlgDriver",
                    "pcan": ".pcan:PcanDriver",
                    "vector": ".vector:VectorDriver",
                    "socketcan": ".socketcan:SocketCanDriver"}
        mod_attr = registry.get(only_drivers)
        if mod_attr is None:
            raise RuntimeError(f"未知驱动: {only_drivers}")
        mod_name, attr = mod_attr.lstrip(".").split(":")
        module = __import__(f"pydiag.devices.{mod_name}", fromlist=[attr])
        mgr = DeviceManager(include_virtual=include_virtual, register_builtin=False)
        mgr.register_driver(getattr(module, attr)())
    else:
        mgr = DeviceManager(include_virtual=include_virtual)
    return mgr
