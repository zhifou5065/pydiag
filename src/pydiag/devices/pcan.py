# -*- coding: utf-8 -*-
"""多厂商 CAN/LIN 设备适配层。"""
from __future__ import annotations

import ctypes, os, queue, struct, sys, threading, time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

"""PEAK PCAN 适配: USB UID 多盒区分 / pad_to8 / 4.10 试初始化探测。"""
from .core import (DeviceInfo, BaseChannel, BaseCanDevice, BaseDriver)
from ..logutil import get_logger

log = get_logger(__name__)


# ============================================================================
class PcanDriver(BaseDriver):
    name = "pcan"

    #: PCANBasic 常量
    PCAN_NONEBUS   = 0x00
    PCAN_DEVICE    = 0x50          # PCAN_USBBUS1 = 0x151 (type 0x50 | ch 0x01)
    PCAN_TYPE_ISABUS = 0x10
    PCAN_PARAMETER_ON = 0x02
    PCAN_BAUD_500K = 0x001C        # PCAN_BAUD_500K @ 8MHz? 实际 0x001C = 500k/8Mhz
    PCAN_BAUD_1M   = 0x0014
    PCAN_BAUD_250K = 0x011C
    PCAN_BAUD_125K = 0x031C
    PCAN_ACCEPTANCE_FILTER = 0x29  # PCAN_MESSAGE_FILTER...

    def __init__(self, dll_path: Optional[str] = None) -> None:
        self.dll_path = dll_path
        self._dll = None

    def is_available(self) -> bool:
        try:
            self._load()
            return True
        except Exception:
            return False

    def _load(self):
        from ..platform import IS_WINDOWS, IS_MACOS
        if not (IS_WINDOWS or IS_MACOS):
            raise OSError("PCAN 驱动支持 Windows/macOS; Linux 请用 socketcan")
        if self._dll is not None:
            return self._dll
        # 库查找顺序: 显式指定 → 系统安装(官方驱动优先) → 包内 vendor 预置
        from ..vendor import find_vendor_lib
        vendor_p = find_vendor_lib("pcan", "windows" if IS_WINDOWS else "macos")
        if IS_WINDOWS:
            windir = os.environ.get("WINDIR", r"C:\Windows")
            cands = [self.dll_path,
                     os.path.join(windir, "System32", "PCANBasic.dll"),
                     os.path.join(windir, "SysWOW64", "PCANBasic.dll"),
                     vendor_p]
            path = next((c for c in cands if c and os.path.isfile(c)), None)
            if not path:
                raise OSError(
                    "PCANBasic.dll 未找到 (系统未安装且 vendor 预置缺失; "
                    "可运行 python -m pydiag.vendor fetch pcan)")
            self._dll = ctypes.WinDLL(path)
        else:
            # macOS: 官方 framework → Homebrew → vendor 预置 dylib
            cands = [self.dll_path,
                     "/Library/Frameworks/PCANBasic.framework/PCANBasic",
                     "/Library/Frameworks/PCANBasic.framework/Versions/Current/PCANBasic",
                     "/usr/local/lib/libpcanbasic.dylib",
                     "/opt/homebrew/lib/libpcanbasic.dylib",
                     vendor_p]
            path = next((c for c in cands if c and os.path.isfile(c)), None)
            if not path:
                raise OSError(
                    "PCANBasic 未找到 (macOS 安装: PEAK 官方 PCAN-Basic .pkg, "
                    "或 python -m pydiag.vendor fetch pcan)")
            self._dll = ctypes.CDLL(path)
            # framework 内部依赖加载
            if os.path.isfile(path):
                try:
                    ctypes.CDLL(os.path.dirname(path), mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass
        return self._dll

    @staticmethod
    def _usb_handle(ch: int) -> int:
        """PCAN_USBBUS1 = 0x151: type 0x50 | channel bit."""
        return 0x0100 | (ch + 1) & 0xFF | 0x50 << 0  # 保持简单: 直接表

    #: 参数码（PCAN-Basic 4.x 官方定义, 4.10 真机验证）
    PCAN_DEVICE_NUMBER = 0x0101       # 设备号（用户可设置）
    PCAN_API_VERSION = 0x0106
    PCAN_CHANNEL_VERSION = 0x0107
    PCAN_CHANNEL_NAME = 0x010E        # "PCAN-USB FD" 等硬件型号
    PCAN_CONTROLLER_NUMBER = 0x010F
    PCAN_UNIQUE_ID = 0x0130           # ★ USB 唯一序列号 (GUID 形态字符串)

    def list_devices(self) -> List[DeviceInfo]:
        """PCAN-USB 探测（4.10 实测策略）。

        GetStatus/LookUpChannel 在 4.10 x64 上对未初始化通道返回非标准码
        （0x4000000/0x8000），不可用于探测；改为逐通道试初始化：
          0x00   = 在位且空闲 → 读取标识后 Uninitialize 还原
          0x1400 = PCAN_ERROR_ILLHW（无硬件）
          其它   = 在位但异常/占用 → 记录并标注

        多盒区分: 初始化成功后读 PCAN_UNIQUE_ID (0x130) 拿 USB 序列号,
        device_id 用序列号（稳定, 不随 USB 口/顺序变化）;
        读不到序列号的回退 "pcan:usbN"。
        """
        try:
            dll = self._load()
        except Exception:
            return []
        dll.CAN_Initialize.restype = ctypes.c_uint
        dll.CAN_Initialize.argtypes = [ctypes.c_ushort, ctypes.c_ushort,
                                       ctypes.c_ubyte, ctypes.c_ubyte,
                                       ctypes.c_ubyte]
        dll.CAN_Uninitialize.restype = ctypes.c_uint
        dll.CAN_Uninitialize.argtypes = [ctypes.c_ushort]
        dll.CAN_GetValue.restype = ctypes.c_uint
        dll.CAN_GetValue.argtypes = [ctypes.c_ushort, ctypes.c_ubyte,
                                     ctypes.c_void_p, ctypes.c_uint]
        out = []
        for ch in range(8):
            h = 0x151 + ch
            r = dll.CAN_Initialize(h, 0x001C, 0, 0, 0)
            if r == 0x00:
                # 读硬件标识
                uid, hw_name, dev_no = "", "", 0
                buf = ctypes.create_string_buffer(256)
                if dll.CAN_GetValue(h, self.PCAN_UNIQUE_ID, buf, 256) == 0:
                    uid = buf.value.decode(errors="replace")
                if dll.CAN_GetValue(h, self.PCAN_CHANNEL_NAME, buf, 256) == 0:
                    hw_name = buf.value.decode(errors="replace")
                v = ctypes.c_uint(0)
                if dll.CAN_GetValue(h, self.PCAN_DEVICE_NUMBER,
                                    ctypes.byref(v), 4) == 0:
                    dev_no = v.value
                dll.CAN_Uninitialize(h)
                # device_id 优先序列号（插拔/换口后不变）; 冲突时回退 usbN
                dev_id = f"pcan:{uid}" if uid else f"pcan:usb{ch+1}"
                out.append(DeviceInfo(
                    driver=self.name, device_id=dev_id,
                    name=hw_name or f"PCAN-USB {ch+1}", channels=1,
                    serial=uid,
                    extra={"status": "free", "handle": h,
                           "usb_slot": ch + 1, "device_number": dev_no}))
            elif r != 0x1400:                # 非无硬件 → 在位但不可初始化
                out.append(DeviceInfo(
                    driver=self.name, device_id=f"pcan:usb{ch+1}",
                    name=f"PCAN-USB {ch+1}", channels=1,
                    extra={"status": f"busy(0x{r:X})"}))
        return out

    def open(self, device_id: str) -> "PcanDevice":
        """按 device_id 打开。支持三种形式:
          "pcan:<uid>"   USB 序列号（推荐, 稳定）
          "pcan:usbN"    枚举槽位 N (1..8, 受 USB 插口顺序影响)
          "pcan:dev<num>" 设备号（PCAN_DEVICE_NUMBER, 用户可用 PCANView 设置）
        序列号/设备号形式: 枚举匹配后定位到实际 handle。
        """
        payload = device_id.split(":", 1)[1] if ":" in device_id else ""
        dll = self._load()
        # 直接槽位形式
        if payload.startswith("usb"):
            ch = int(payload.replace("usb", "")) - 1
            return PcanDevice(
                DeviceInfo(driver=self.name, device_id=device_id,
                           name=f"PCAN-USB {ch+1}", channels=1,
                           extra={"handle": 0x151 + ch}),
                dll, ch)
        # 序列号/设备号: 枚举匹配
        by_uid = {}
        by_devno = {}
        for info in self.list_devices():
            if "uid" not in info.extra and info.serial:
                info.extra["uid"] = info.serial
            if info.serial:
                by_uid[info.serial] = info
            if info.extra.get("device_number"):
                by_devno[info.extra["device_number"]] = info
        info = None
        if payload in by_uid:
            info = by_uid[payload]
        elif payload.startswith("dev"):
            info = by_devno.get(int(payload.replace("dev", "")))
        if info is None or info.extra.get("status") != "free":
            raise RuntimeError(
                f"PCAN 设备未找到或不可用: {device_id}（在位列表: "
                f"{[d.device_id for d in self.list_devices()]}）")
        ch = info.extra["usb_slot"] - 1
        return PcanDevice(info, dll, ch)


class PcanDevice(BaseCanDevice):
    def __init__(self, info: DeviceInfo, dll, usb_index: int) -> None:
        super().__init__(info)
        self._dll = dll
        self._usb_index = usb_index

    def open_channel(self, index: int = 0, bitrate: int = 500_000,
                     data_bitrate: int = 2_000_000, fd: bool = False,
                     pad_to8: bool = True, **opts) -> BaseChannel:
        def factory():
            return PcanChannel(self, index, bitrate, fd, pad_to8=pad_to8)
        return self._get_or_create_channel(index, factory)

    def _close_impl(self) -> None:
        try:
            h = 0x151 + self._usb_index
            self._dll.CAN_Uninitialize(h)
        except Exception:
            pass


class PcanChannel(BaseChannel):
    """PCAN 通道：PCAN_Initialize 一个 handle 对应一个 USB 通道。"""

    BAUD = {1_000_000: 0x0014, 500_000: 0x001C,
            250_000: 0x011C, 125_000: 0x031C}

    def __init__(self, device: PcanDevice, index: int,
                 bitrate: int, fd: bool, pad_to8: bool = True) -> None:
        super().__init__(device, index, bitrate, fd=fd)
        self.pad_to8 = pad_to8     # DLC=8 帧填充（与 ZlgChannel 保持一致）
        dll = device._dll
        self._handle = 0x151 + device._usb_index
        baud = self.BAUD.get(bitrate, 0x001C)
        # TPCANStatus CAN_Initialize(TPCANHandle, TPCANBaudrate, ...)
        dll.CAN_Initialize.restype = ctypes.c_uint
        log.info("pcan open handle=0x%03X bitrate=0x%X fd=%s", self._handle, baud, fd)
        r = dll.CAN_Initialize(self._handle, baud, 0, 0, 0)
        if r not in (0x00, 0x01):
            log.error("pcan CAN_Initialize failed handle=0x%03X status=0x%X",
                      self._handle, r)
            raise RuntimeError(f"PCAN_Initialize 失败: 0x{r:X}")
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._rx_loop, daemon=True,
                                        name=f"PcanCh{index}-Rx")
        self._thread.start()

    def _send_impl(self, can_id: int, data: bytes) -> int:
        dll = self.device._dll
        # TPCANMsg: ID(4) MSGTYPE(1) LEN(1) DATA[8] = 14 字节
        msg = (ctypes.c_ubyte * 14)()
        struct.pack_into("<IB", msg, 0, can_id & 0x1FFFFFFF, 0)  # ID + MSGTYPE=标准
        if self.pad_to8 and len(data) < 8:
            # 经典 CAN 帧 DLC=8, 不足补 0xAA —— 与 ZlgChannel 行为一致
            d = (list(data) + [0xAA] * 8)[:8]
            msg[5] = 8
            for i, b in enumerate(d):
                msg[6 + i] = b
        else:
            n = min(len(data), 8)
            msg[5] = n
            for i, b in enumerate(data[:n]):
                msg[6 + i] = b
        r = dll.CAN_Write(self._handle, msg)
        if r != 0:
            log.error("pcan CAN_Write failed handle=0x%03X id=0x%03X status=0x%X",
                      self._handle, can_id, r)
        log.debug("pcan TX id=0x%03X dlc=%d data=%s r=0x%X",
                  can_id, len(data), data.hex(), r)
        return 0 if r == 0 else 1

    def _rx_loop(self) -> None:
        dll = self.device._dll
        dll.CAN_Read.restype = ctypes.c_uint
        dll.CAN_Read.argtypes = [ctypes.c_ushort,
                                 ctypes.POINTER(ctypes.c_ubyte * 14),
                                 ctypes.POINTER(ctypes.c_ubyte * 8)]
        while not self._stop.is_set():
            msg = (ctypes.c_ubyte * 14)()
            ts = (ctypes.c_ubyte * 8)()
            r = dll.CAN_Read(self._handle, msg, ts)
            if r == 0:
                can_id = struct.unpack_from("<I", msg, 0)[0] & 0x1FFFFFFF
                dlc = msg[5]                          # LEN 在 offset 5
                data = bytes(msg[6:6 + min(dlc, 8)])
                self._dispatch(can_id, data)
            else:
                time.sleep(0.001)

    def close(self) -> None:
        if self._closed:
            return
        self._stop.set()
        try:
            self._thread.join(timeout=1.0)
        except Exception:
            pass
        try:
            self.device._dll.CAN_Uninitialize(self._handle)
        except Exception:
            pass
        super().close()
        self.device._remove_channel(self.index)

