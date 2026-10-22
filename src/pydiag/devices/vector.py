# -*- coding: utf-8 -*-
"""多厂商 CAN/LIN 设备适配层。"""
from __future__ import annotations

import ctypes, os, queue, struct, sys, threading, time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

"""Vector XL 适配: 通道掩码模型。"""
from .core import (DeviceInfo, BaseChannel, BaseCanDevice, BaseDriver)


# ============================================================================
class VectorDriver(BaseDriver):
    name = "vector"

    XL_SUCCESS = 0
    XL_BUS_TYPE_CAN = 0x00000001

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
        from ..platform import IS_WINDOWS
        if not IS_WINDOWS:
            raise OSError("Vector XL 驱动仅支持 Windows (vxlapi64.dll)")
        if self._dll is not None:
            return self._dll
        # 库查找顺序: 显式指定 → 系统安装(官方驱动优先) → 包内 vendor 预置
        from ..vendor import find_vendor_lib
        vendor_p = find_vendor_lib("vector", "windows")
        windir = os.environ.get("WINDIR", r"C:\Windows")
        cands = [self.dll_path,
                 os.path.join(windir, "System32", "vxlapi64.dll"),
                 os.path.join(windir, "System32", "vxlapi.dll"),
                 vendor_p]
        path = next((c for c in cands if c and os.path.isfile(c)), None)
        if not path:
            raise OSError(
                "vxlapi DLL 未找到 (请安装 Vector XL Driver Library)")
        self._dll = ctypes.WinDLL(path)
        return self._dll

    def list_devices(self) -> List[DeviceInfo]:
        """xlGetDriverConfig 枚举驱动里的通道（简化: 取应用配置通道名）。"""
        try:
            dll = self._load()
        except Exception:
            return []
        # XLdriverConfig 结构非常大; 简化: 用 xlGetApplConfig 探测 app 通道数,
        # 硬件名通过 xlGetChannelName (部分版本无此导出)。
        out = []
        # 走保守路径: 不解析 XLdriverConfig, 只报告 "vector:app0..3"
        for i in range(4):
            out.append(DeviceInfo(
                driver=self.name, device_id=f"vector:app{i}",
                name=f"Vector XL 通道 {i}（应用映射）", channels=1))
        return out

    def open(self, device_id: str) -> "VectorDevice":
        idx = int(device_id.split(":")[1].replace("app", ""))
        return VectorDevice(
            DeviceInfo(driver=self.name, device_id=device_id,
                       name=f"Vector XL 通道 {idx}", channels=1),
            self._dll, idx)


class VectorDevice(BaseCanDevice):
    def __init__(self, info: DeviceInfo, dll, app_index: int) -> None:
        super().__init__(info)
        self._dll = dll
        self._app_index = app_index
        self._port_handle = ctypes.c_ulong(0xFFFFFFFF)  # XL_INVALID_PORTHANDLE

    def open_channel(self, index: int = 0, bitrate: int = 500_000,
                     data_bitrate: int = 2_000_000, fd: bool = False,
                     **opts) -> BaseChannel:
        def factory():
            return VectorChannel(self, index, bitrate, fd)
        return self._get_or_create_channel(index, factory)

    def _close_impl(self) -> None:
        try:
            if self._port_handle.value != 0xFFFFFFFF:
                self._dll.xlClosePort(self._port_handle)
        except Exception:
            pass


class VectorChannel(BaseChannel):
    """Vector XL 通道：xlOpenPort + xlSetApplConfig + xlActivateChannel。"""

    def __init__(self, device: VectorDevice, index: int,
                 bitrate: int, fd: bool) -> None:
        super().__init__(device, index, bitrate, fd=fd)
        dll = device._dll
        app = device._app_index
        # xlSetApplConfig(appName, appChan, busType, custChan, phyChan)
        dll.xlSetApplConfig.restype = ctypes.c_short
        dll.xlSetApplConfig("zuds", app, self.XL_BUS_TYPE_CAN, app, app)
        # xlGetChannelMask(custChan, phyChan, busType)
        dll.xlGetChannelMask.restype = ctypes.c_ulonglong
        mask = dll.xlGetChannelMask(app, app, self.XL_BUS_TYPE_CAN)
        if not mask:
            raise RuntimeError("Vector: 通道掩码为 0（无硬件或未映射）")
        dll.xlOpenPort.restype = ctypes.c_short
        ph = ctypes.c_ulong(0xFFFFFFFF)
        r = dll.xlOpenPort(ctypes.byref(ph), b"zuds", mask,
                           ctypes.byref(ctypes.c_ulong(0)),  # permissionMask
                           1024, self.XL_BUS_TYPE_CAN)
        if r != self.XL_SUCCESS:
            raise RuntimeError(f"xlOpenPort 失败: {r}")
        device._port_handle = ph
        self._ph = ph
        self._mask = mask
        # 波特率（经典 CAN）: xlCanSetChannelBitrate
        try:
            dll.xlCanSetChannelBitrate.restype = ctypes.c_short
            dll.xlCanSetChannelBitrate(ph, mask, bitrate * 1000)
        except Exception:
            pass
        dll.xlActivateChannel.restype = ctypes.c_short
        dll.xlActivateChannel(ph, mask, self.XL_BUS_TYPE_CAN, 0x08)  # XL_ACTIVATE_NONE
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._rx_loop, daemon=True,
                                        name=f"VecCh{index}-Rx")
        self._thread.start()

    def _send_impl(self, can_id: int, data: bytes) -> int:
        dll = self.device._dll
        # XLevent: tag(2)+tag_data(2+8)+... 简化打包 CAN 事件
        # XL_CAN_EV_TAG_TX_OK 结构较复杂; 用 xlCanTransmit 简化调用:
        #   xlCanTransmit(port, mask, &msgCnt, &event)
        # XLevent size = 48 字节（对齐）, tag=10 (XL_TRANSMIT_MSG)
        ev = (ctypes.c_ubyte * 48)()
        struct.pack_into("<H", ev, 0, 10)            # tag = XL_TRANSMIT_MSG
        struct.pack_into("<H", ev, 2, 0)             # transId
        struct.pack_into("<I", ev, 4, 0)             # size high
        # XL_CANF_Data: id(4)+flags(2)+dlc(2)+data(8)
        struct.pack_into("<I", ev, 8, can_id & 0x1FFFFFFF)
        struct.pack_into("<H", ev, 12, 0)            # flags
        msg_len = min(len(data), 8)
        struct.pack_into("<H", ev, 14, msg_len)
        for i, b in enumerate(data[:8]):
            ev[16 + i] = b
        cnt = ctypes.c_uint(1)
        dll.xlCanTransmit.restype = ctypes.c_short
        r = dll.xlCanTransmit(self._ph, self._mask, ctypes.byref(cnt), ev)
        return 0 if r == self.XL_SUCCESS else 1

    def _rx_loop(self) -> None:
        dll = self.device._dll
        dll.xlReceive.restype = ctypes.c_short
        while not self._stop.is_set():
            ev = (ctypes.c_ubyte * 48)()
            cnt = ctypes.c_uint(1)
            r = dll.xlReceive(self._ph, ctypes.byref(cnt), ev)
            if r == self.XL_SUCCESS:
                tag = struct.unpack_from("<H", ev, 0)[0]
                if tag in (10, 12):                  # TRANSMIT_MSG / RX_MSG
                    can_id = struct.unpack_from("<I", ev, 8)[0]
                    dlc = struct.unpack_from("<H", ev, 14)[0] & 0x0F
                    data = bytes(ev[16:16 + max(dlc, 1)])[:dlc or 8]
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
            self.device._dll.xlClosePort(self._ph)
        except Exception:
            pass
        self.device._port_handle = ctypes.c_ulong(0xFFFFFFFF)
        super().close()
        self.device._remove_channel(self.index)

