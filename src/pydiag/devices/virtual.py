# -*- coding: utf-8 -*-
"""多厂商 CAN/LIN 设备适配层。"""
from __future__ import annotations

import ctypes, os, queue, struct, sys, threading, time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

"""虚拟回环设备: 无硬件开发/CI 用。"""
from .core import DeviceInfo, BaseChannel, BaseCanDevice


class VirtualChannel(BaseChannel):
    """进程内回环通道（同一 VirtualCanDevice 的所有通道互通）。"""

    def _send_impl(self, can_id: int, data: bytes) -> int:
        dev: VirtualCanDevice = self.device
        with dev._hub_lock:
            targets = [c for c in dev._channels.values() if c is not self]
        for c in targets:
            c._dispatch(can_id, bytes(data))
        return 0


class VirtualCanDevice(BaseCanDevice):
    """虚拟设备：通道之间回环（A 发 B 收），无硬件。"""

    def __init__(self, n_channels: int = 2) -> None:
        super().__init__(DeviceInfo(driver="virtual", device_id="virtual:0",
                                    name="虚拟总线", channels=n_channels))
        self._hub_lock = threading.Lock()
        self._n = n_channels

    def open_channel(self, index: int = 0, bitrate: int = 500_000,
                     data_bitrate: int = 2_000_000, fd: bool = False,
                     **opts) -> BaseChannel:
        def factory():
            return VirtualChannel(self, index, bitrate, data_bitrate, fd)
        return self._get_or_create_channel(index, factory)

    def _close_impl(self) -> None:
        pass

