# -*- coding: utf-8 -*-
"""设备核心: DeviceInfo / BaseChannel / BaseCanDevice / BaseDriver / DeviceManager。

适配 zuds.VirtualBus 同名接口（subscribe/send/close），使 UDS 会话直接跑在
Channel 之上而不感知具体设备（ZLG / PCAN / Vector / virtual）。
"""
from __future__ import annotations

import atexit
import os
import queue
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from ..logutil import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# ZLG 运行时引导: 使 zlg.py 能 import zlgcan（zlgcan.py 绑定在 zlg_runtime;
# 二进制库 zlgcan.dll 预置在 vendor/zlg, 由 zlgcan.py 内部按系统优先加载）
# ---------------------------------------------------------------------------
_ZLG_HINTS = [
    os.environ.get("PYDIAG_ZLG_RUNTIME", ""),
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "zlg_runtime"),
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "zlg_runtime"),
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))), "zlg_runtime"),
]


def zlg_runtime_dir() -> Optional[str]:
    """定位 zlg_runtime 目录（含 zlgcan.py 绑定），找到则注册 DLL 搜索路径。"""
    for d in _ZLG_HINTS:
        if d and os.path.isfile(os.path.join(d, "zlgcan.py")):
            try:
                os.add_dll_directory(d)
                os.add_dll_directory(os.path.join(d, "kerneldlls"))
            except (OSError, AttributeError):
                pass        # 非 Windows 平台无 add_dll_directory
            if d not in sys.path:
                sys.path.insert(0, d)
            return d
    return None


import sys  # noqa: E402  (sys 在 zlg_runtime_dir 内也用到)

@dataclass
class DeviceInfo:
    """设备描述（枚举结果）。"""
    driver: str            # "zlg" / "pcan" / "vector" / "virtual"
    device_id: str         # 唯一标识: "zlg:<serial>" / "pcan:<handle>" / "vector:<name>:<idx>"
    name: str              # 展示名
    serial: str = ""       # 序列号（如有）
    channels: int = 1      # 通道数
    extra: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.driver}] {self.name} ({self.device_id})"


# ============================================================================
# Channel 抽象 — 使用者唯一接触的接口
# ============================================================================
class BaseChannel(ABC):
    """逻辑通道：send/subscribe/close + 基本属性。

    与 zuds.VirtualBus 同构，因此可直接:
        ses = zuds.create_docan_session(channel, tx_id, rx_id)
    """

    def __init__(self, device: "BaseCanDevice", index: int,
                 bitrate: int = 500_000, data_bitrate: int = 2_000_000,
                 fd: bool = False, name: str = "") -> None:
        self.device = device
        self.index = int(index)
        self.bitrate = int(bitrate)
        self.data_bitrate = int(data_bitrate)
        self.fd = bool(fd)
        self.name = name or f"{device.info.driver}:ch{index}"
        self.bus_type = "can"           # 总线类型: "can" / "lin"（LIN 子类覆盖）
        self._subs: Dict[int, List[queue.Queue]] = {}
        self._lock = threading.Lock()
        self._closed = False
        # 帧统计（供 UI/调试）
        self.tx_count = 0
        self.rx_count = 0

    # ---- 对上（VirtualBus 兼容） ----
    def subscribe(self, can_id: int) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subs.setdefault(int(can_id), []).append(q)
        return q

    def unsubscribe(self, can_id: int, q: queue.Queue) -> None:
        with self._lock:
            qs = self._subs.get(int(can_id), [])
            if q in qs:
                qs.remove(q)

    def send(self, can_id: int, data: bytes) -> int:
        """发送一帧。返回 0=成功, 非0=失败（与 VirtualBus 约定一致）。"""
        if self._closed:
            return 1
        r = self._send_impl(int(can_id), bytes(data))
        if r == 0:
            self.tx_count += 1
        return r

    @abstractmethod
    def _send_impl(self, can_id: int, data: bytes) -> int:
        ...

    # ---- 对下（设备驱动帧分发进来） ----
    def _dispatch(self, can_id: int, data: bytes) -> None:
        if self._closed:
            return
        self.rx_count += 1
        with self._lock:
            qs = list(self._subs.get(int(can_id), ()))
        for q in qs:
            q.put(bytes(data))

    def close(self) -> None:
        self._closed = True
        with self._lock:
            self._subs.clear()

    def __repr__(self) -> str:
        return (f"<{self.__class__.__name__} {self.name} "
                f"bitrate={self.bitrate} fd={self.fd} "
                f"tx={self.tx_count} rx={self.rx_count}>")


# ============================================================================
# 物理设备抽象
# ============================================================================
class BaseCanDevice(ABC):
    """已打开的物理设备：负责通道生命周期 + 后台收帧分发。"""

    def __init__(self, info: DeviceInfo) -> None:
        self.info = info
        self._channels: Dict[int, BaseChannel] = {}
        self._lock = threading.Lock()
        self._closed = False

    @abstractmethod
    def open_channel(self, index: int = 0, bitrate: int = 500_000,
                     data_bitrate: int = 2_000_000, fd: bool = False,
                     **opts) -> BaseChannel:
        """打开（或复用已打开的）逻辑通道。"""

    def channels(self) -> List[BaseChannel]:
        return list(self._channels.values())

    def _get_or_create_channel(self, index: int, factory: Callable[[], BaseChannel]) -> BaseChannel:
        with self._lock:
            if self._closed:
                raise RuntimeError("设备已关闭")
            ch = self._channels.get(index)
            if ch is None:
                ch = factory()
                self._channels[index] = ch
            return ch

    def _remove_channel(self, index: int) -> None:
        with self._lock:
            self._channels.pop(index, None)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        for ch in list(self._channels.values()):
            ch.close()
        self._channels.clear()
        self._close_impl()

    @abstractmethod
    def _close_impl(self) -> None:
        ...

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


# ============================================================================
# 驱动抽象
# ============================================================================
class BaseDriver(ABC):
    """设备驱动：负责 DLL 加载 + 硬件枚举 + 打开物理设备。"""

    name: str = "base"

    @abstractmethod
    def is_available(self) -> bool:
        """DLL/驱动是否可加载（不可用时枚举静默跳过）。"""

    @abstractmethod
    def list_devices(self) -> List[DeviceInfo]:
        ...

    @abstractmethod
    def open(self, device_id: str) -> BaseCanDevice:
        ...



# ============================================================================
# 设备管理器
# ============================================================================
class DeviceManager:
    """多厂商 CAN 设备统一管理入口。

    用法:
        mgr = DeviceManager()
        mgr.list_devices()                      # 扫描所有驱动
        dev = mgr.open("zlg:0")                 # 打开物理设备
        ch  = dev.open_channel(0, bitrate=500_000)   # 拿 Channel
        ses = zuds.create_docan_session(ch, 0x721, 0x7A1)
        ...
        ch.close(); dev.close()

    特性:
      * 驱动注册表: register_driver() 可插拔（默认 zlg/pcan/vector/virtual）
      * 枚举容错:  某驱动 DLL 缺失时静默跳过
      * 独占保护:  同一 device_id 同时只允许打开一次（ZLG 硬性独占）
      * 统一关闭:  atexit 注册, 进程退出自动释放
    """

    def __init__(self, include_virtual: bool = False,
                 register_builtin: bool = True) -> None:
        self._drivers: Dict[str, BaseDriver] = {}
        self._open_devices: Dict[str, BaseCanDevice] = {}
        self._lock = threading.Lock()
        if register_builtin:
            # 按当前平台注册内置驱动（不支持当前平台的驱动不加载, 厂商 DLL 缺失时 is_available()=False 静默跳过）
            from ..platform import platform_supported_drivers, OS_NAME
            from .zlg import ZlgDriver
            from .pcan import PcanDriver
            from .vector import VectorDriver
            from .socketcan import SocketCanDriver
            factories = {
                "zlg": ZlgDriver,
                "pcan": PcanDriver,
                "vector": VectorDriver,
                "socketcan": SocketCanDriver,
            }
            for name in platform_supported_drivers():
                if name == "virtual":
                    continue
                cls = factories.get(name)
                if cls is not None:
                    self.register_driver(cls())
            if OS_NAME not in ("windows", "linux", "macos"):  # pragma: no cover
                pass
        if include_virtual:
            from .virtual import VirtualCanDevice as _VCD
            self._virtual_factory = _VCD
        else:
            self._virtual_factory = None
        atexit.register(self.close_all)

    # ---- 驱动 ----
    def register_driver(self, driver: BaseDriver) -> None:
        self._drivers[driver.name] = driver

    def drivers(self) -> List[str]:
        return [n for n, d in self._drivers.items() if d.is_available()]

    # ---- 枚举 ----
    def list_devices(self, driver: Optional[str] = None) -> List[DeviceInfo]:
        """扫描（或指定）驱动的硬件。"""
        out: List[DeviceInfo] = []
        names = [driver] if driver else list(self._drivers)
        for n in names:
            d = self._drivers.get(n)
            if d is None:
                continue
            try:
                if d.is_available():
                    out += d.list_devices()
            except Exception:
                continue
        if self._virtual_factory is not None:
            from .virtual import VirtualCanDevice
            out.append(VirtualCanDevice().info)
        # 已打开的设备标注状态
        for dev in self._open_devices.values():
            for i, di in enumerate(out):
                if di.device_id == dev.info.device_id:
                    di.extra["opened"] = True
        return out

    # ---- 打开/关闭 ----
    def open(self, device_id: str, **opts) -> BaseCanDevice:
        """按 device_id 打开设备（独占: 已打开直接返回同一实例）。"""
        with self._lock:
            existing = self._open_devices.get(device_id)
            if existing is not None:
                return existing
            if device_id.startswith("virtual"):
                if self._virtual_factory is None:
                    raise RuntimeError("虚拟设备未启用 (include_virtual=True)")
                dev = self._virtual_factory()
            else:
                drv_name = device_id.split(":", 1)[0]
                drv = self._drivers.get(drv_name)
                if drv is None:
                    raise RuntimeError(f"未知驱动: {drv_name}")
                if not drv.is_available():
                    raise RuntimeError(
                        f"驱动 {drv_name} 不可用（驱动库缺失/平台不支持; "
                        f"详情见日志）")
                dev = drv.open(device_id)
                log.info("opened device %s (driver=%s)", device_id, drv_name)
            self._open_devices[device_id] = dev
            return dev

    def close(self, device_id: str) -> None:
        with self._lock:
            dev = self._open_devices.pop(device_id, None)
        if dev:
            dev.close()

    def close_all(self) -> None:
        with self._lock:
            devs = list(self._open_devices.values())
            self._open_devices.clear()
        for d in devs:
            try:
                d.close()
            except Exception:
                pass

    # ---- 状态 ----
    def opened_devices(self) -> List[str]:
        return list(self._open_devices)

    def __repr__(self) -> str:
        return (f"<DeviceManager drivers={self.drivers()} "
                f"opened={list(self._open_devices)}>")
