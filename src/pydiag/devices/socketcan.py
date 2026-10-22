# -*- coding: utf-8 -*-
"""pydiag.devices.socketcan — Linux SocketCAN 适配 (socket.AF_CAN 原生)。

平台: Linux 专用（内核 CAN 子系统, 无需厂商驱动）。
前置: 接口已配置并 UP:
    sudo ip link set can0 type can bitrate 500000
    sudo ip link set up can0

device_id 形如 "socketcan:can0"。
"""
from __future__ import annotations

import os
import queue
import socket
import struct
import threading
from typing import Dict, List, Optional, Tuple

from .core import (DeviceInfo, BaseChannel, BaseCanDevice, BaseDriver)
from ..logutil import get_logger

log = get_logger(__name__)

# struct can_frame: can_id(4) + dlc/flags(4) + data(8)
CAN_FRAME_FMT = "=IB3x8s"
CAN_FRAME_SIZE = struct.calcsize(CAN_FRAME_FMT)

CAN_EFF_FLAG = 0x80000000    # 扩展帧标志
CAN_RTR_FLAG = 0x40000000
CAN_ERR_FLAG = 0x20000000
CAN_EFF_MASK = 0x1FFFFFFF    # 29 位扩展 ID 掩码
CAN_SFF_MASK = 0x000007FF    # 11 位标准 ID 掩码

# socket options
CAN_RAW = 1
CAN_RAW_FILTER = 1


class SocketCanDriver(BaseDriver):
    name = "socketcan"

    def __init__(self) -> None:
        self._sys_class_net = "/sys/class/net"

    def is_available(self) -> bool:
        try:
            return bool(self.list_devices())
        except Exception:
            return False

    def list_devices(self) -> List[DeviceInfo]:
        """枚举 /sys/class/net 下的 CAN 接口 (type 280 = ARPHRD_CAN)。"""
        out: List[DeviceInfo] = []
        try:
            names = sorted(os.listdir(self._sys_class_net))
        except OSError:
            return out
        for ifname in names:
            type_path = os.path.join(self._sys_class_net, ifname, "type")
            try:
                with open(type_path) as f:
                    if int(f.read().strip()) != 280:
                        continue
            except (OSError, ValueError):
                continue
            out.append(DeviceInfo(
                driver=self.name,
                device_id=f"{self.name}:{ifname}",
                name=f"SocketCAN {ifname}",
                channels=1,
            ))
        return out

    def open(self, device_id: str) -> "SocketCanDevice":
        if not device_id.startswith(f"{self.name}:"):
            raise RuntimeError(f"非法 device_id: {device_id}")
        ifname = device_id.split(":", 1)[1]
        return SocketCanDevice(ifname)


class SocketCanDevice(BaseCanDevice):
    """单接口设备（SocketCAN 接口无多通道概念, 一接口一通道）。"""

    def __init__(self, ifname: str) -> None:
        self.ifname = ifname
        self.info = DeviceInfo(
            driver="socketcan",
            device_id=f"socketcan:{ifname}",
            name=f"SocketCAN {ifname}",
            channels=1,
        )
        self._channels: Dict[int, SocketCanChannel] = {}

    def open_channel(self, index: int = 0, bitrate: int = 500_000,
                     **opts) -> "SocketCanChannel":
        if index != 0:
            raise RuntimeError(
                f"SocketCAN 接口无多通道: {self.ifname} 只有 ch0 "
                f"(多通道需多个接口 can0/can1/...)")
        if index in self._channels and not self._channels[index]._closed:
            return self._channels[index]
        ch = SocketCanChannel(self, 0, bitrate=bitrate, ifname=self.ifname,
                              **opts)
        self._channels[0] = ch
        return ch


class SocketCanChannel(BaseChannel):
    """SocketCAN 通道: AF_CAN RAW socket + 后台收帧线程。"""

    def __init__(self, device: SocketCanDevice, index: int,
                 bitrate: int = 500_000, ifname: str = "can0", **opts):
        super().__init__(device, index, bitrate=bitrate)
        self.ifname = ifname
        self.tx_count = 0
        self.rx_count = 0

        self.sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        try:
            self.sock.bind((ifname,))
            log.info("socketcan bound ifname=%s bitrate=%d", ifname, bitrate)
        except OSError as e:
            log.error("socketcan bind failed ifname=%s: %s", ifname, e)
            self.sock.close()
            raise RuntimeError(
                f"SocketCAN 绑定失败: {ifname} ({e})。"
                f"请确认接口存在且已 UP: sudo ip link set {ifname} up type can "
                f"bitrate {bitrate}") from e

        self._subs: Dict[int, "queue.Queue[bytes]"] = {}
        self._lock = threading.Lock()
        self._closed = False
        self._rx_thread = threading.Thread(
            target=self._rx_loop, daemon=True, name=f"socketcan-{ifname}")
        self._rx_thread.start()

    # ---- 收帧 ----
    def _rx_loop(self) -> None:
        while not self._closed:
            try:
                frame = self.sock.recv(CAN_FRAME_SIZE)
            except OSError:
                break
            if len(frame) < CAN_FRAME_SIZE:
                continue
            can_id, _dlc, data = struct.unpack(CAN_FRAME_FMT, frame)
            if can_id & (CAN_ERR_FLAG | CAN_RTR_FLAG):
                continue
            dlc = frame[4] & 0x0F
            payload = data[:dlc]
            with self._lock:
                subs = list(self._subs.items())
            self.rx_count += 1
            for cid, q in subs:
                if cid == can_id:
                    q.put(payload)

    # ---- 发送 ----
    def _send_impl(self, can_id: int, data: bytes, fd: bool = False) -> int:
        if self._closed:
            return 1
        is_eff = can_id > 0x7FF
        cid = (can_id & CAN_EFF_MASK) | (CAN_EFF_FLAG if is_eff else 0)
        dlc = min(len(data), 8)
        frame = struct.pack(CAN_FRAME_FMT, cid, dlc,
                            bytes(data[:8]).ljust(8, b"\x00"))
        try:
            self.sock.send(frame)
        except OSError as e:
            log.error("socketcan %s TX failed id=0x%03X: %s (interface down?)",
                      self.ifname, can_id, e)
            return 1
        log.debug("socketcan %s TX id=0x%03X dlc=%d data=%s",
                  self.ifname, can_id, len(data), data.hex())
        self.tx_count += 1
        return 0

    # ---- 订阅 ----
    def subscribe(self, can_id: int) -> "queue.Queue[bytes]":
        q: "queue.Queue[bytes]" = queue.Queue()
        with self._lock:
            self._subs[can_id] = q
        return q

    def unsubscribe(self, can_id: int, q) -> None:
        with self._lock:
            if self._subs.get(can_id) is q:
                del self._subs[can_id]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.sock.close()
        except OSError:
            pass
