# -*- coding: utf-8 -*-
"""pydiag.devices.linux_vci — ZLG USBCANFD Linux 后端 (libusb 方案)。

封装官方 libusbcanfd.so 的 VCI_* API（与 Windows zlgcan.dll 的 ZCAN_* 逻辑
同源, 函数名/帧结构不同）:

  VCI_OpenDevice(Type, Card, Reserved)        打开设备
  VCI_CloseDevice(Type, Card)                 关闭设备
  VCI_InitCAN(Type, Card, Port, ZCANFD_INIT)  通道初始化 (tseg 时序配置)
  VCI_StartCAN / VCI_ResetCAN                 通道启动/复位
  VCI_Transmit / VCI_TransmitFD               发送 CAN / CANFD
  VCI_GetReceiveNum / VCI_Receive(FD)         接收
  VCI_SetReference(CMD_GET_SN)                读序列号

库查找顺序: 系统安装 (/usr/lib, /lib) → 包内 vendor/zlg/linux/。
设备类型 USBCANFD = 33; USB VID:PID = 3068:0009 (udev 权限见 vendor/zlg/linux)。

参考: https://manual.zlg.cn/web/#/152/15464 (Linux CAN 转以太网通讯库)
      https://manual.zlg.cn/web/#/146   (USB 接口 CAN 设备 → 软件资料)
"""
from __future__ import annotations

import ctypes
import os
import platform
from ctypes import (Structure, Union, c_uint8, c_uint16, c_uint32,
                    c_ubyte, POINTER, byref, sizeof)
from typing import List, Optional, Tuple

from ..logutil import get_logger

log = get_logger(__name__)

# ---- 设备类型常量 (官方 USBCANFD_DEMO.py) ----
USBCANFD = 33

# ---- SetReference 命令码 ----
CMD_CAN_TRES = 0x14                 # 终端电阻
CMD_GET_SN = 0x43                   # 读序列号

# ---- 帧信息位域 (ZCAN_MSG_INFO) ----
class _MSG_INFO(Structure):
    _fields_ = [("txm", c_uint, 4),        # 0 正常发送, 2 自发自收
                ("fmt", c_uint, 4),        # 0 CAN2.0, 1 CANFD
                ("sdf", c_uint, 1),        # 0 数据帧, 1 远程帧
                ("sef", c_uint, 1),        # 0 标准帧, 1 扩展帧
                ("err", c_uint, 1),
                ("brs", c_uint, 1),        # CANFD 加速
                ("est", c_uint, 1),
                ("tx", c_uint, 1),         # 收到的是发送帧
                ("echo", c_uint, 1),
                ("qsend_100us", c_uint, 1),
                ("qsend", c_uint, 1),
                ("pad", c_uint, 15)]


class _MSG_HDR(Structure):
    _fields_ = [("ts", c_uint32),
                ("id", c_uint32),
                ("inf", _MSG_INFO),
                ("pad", c_uint16),
                ("chn", c_uint8),
                ("len", c_uint8)]


class ZCAN_20_MSG(Structure):
    _fields_ = [("hdr", _MSG_HDR), ("dat", c_ubyte * 8)]


class ZCAN_FD_MSG(Structure):
    _fields_ = [("hdr", _MSG_HDR), ("dat", c_ubyte * 64)]


class _BIT_TIMING(Structure):
    _fields_ = [("tseg1", c_uint8), ("tseg2", c_uint8),
                ("sjw", c_uint8), ("smp", c_uint8), ("brp", c_uint8)]


class ZCANFD_INIT(Structure):
    _fields_ = [("clk", c_uint32), ("mode", c_uint32),
                ("abit", _BIT_TIMING), ("dbit", _BIT_TIMING)]


# ---- 波特率 → tseg 时序 (clk=60MHz V1.01; 80MHz V1.03+ 官方 demo 默认 60M) ----
# 仲裁域: tseg1=14 tseg2=3 sjw=2 brp=N → (1+14+3)*N/60M
# brp=5 → 500k; brp=4 → 625k; brp=10 → 250k; brp=20 → 125k; brp=2 → 1M
_ARBITration_BRP = {1_000_000: 2, 500_000: 5, 250_000: 10, 125_000: 20}
# 数据域: tseg1=10 tseg2=2 sjw=2 brp=1 → (1+10+2)/60M ≈ 2M (官方 2M 配置)
_DATA_TIMING = dict(tseg1=10, tseg2=2, sjw=2, smp=0, brp=1)


def _arbitration_timing(bitrate: int) -> Tuple[int, int, int, int, int]:
    brp = _ARBITration_BRP.get(bitrate)
    if brp is None:
        raise RuntimeError(
            f"Linux libusbcanfd 不支持波特率 {bitrate} "
            f"(支持: {sorted(_ARBITration_BRP)}; 其它波特率需自行换算 tseg)")
    return (14, 3, 2, 0, brp)


def _find_library() -> Optional[str]:
    """libusbcanfd.so 查找: 系统优先 → 包内 vendor/zlg/linux。"""
    # 1) 系统安装路径 (readme 推荐 cp 到 /lib)
    for d in ("/usr/lib", "/usr/local/lib", "/lib"):
        p = os.path.join(d, "libusbcanfd.so")
        if os.path.isfile(p):
            return p
    # 2) 包内预置
    here = os.path.dirname(os.path.abspath(__file__))
    pkg_root = os.path.dirname(here)
    p = os.path.join(pkg_root, "vendor", "zlg", "linux", "libusbcanfd.so")
    if os.path.isfile(p):
        return p
    return None


class LinuxVci:
    """libusbcanfd.so 的 ctypes 薄封装。"""

    def __init__(self) -> None:
        path = _find_library()
        if not path:
            raise OSError(
                "libusbcanfd.so 未找到（系统 /usr/lib 或 "
                "pydiag/vendor/zlg/linux/ 均无; 见 "
                "https://manual.zlg.cn/web/#/146 的 Linux 驱动下载）")
        # libusbcanfd 依赖 libusb-1.0; 包内带了副本, 先预加载
        # (若系统已有 libusb-1.0.so.0 则用系统的)
        self._path = path
        try:
            ctypes.CDLL("libusb-1.0.so.0")
        except OSError:
            bundled = os.path.join(os.path.dirname(path), "libusb-1.0.so")
            if os.path.isfile(bundled):
                ctypes.CDLL(bundled)
            else:
                raise OSError(
                    "libusb-1.0 缺失: sudo apt-get install libusb-1.0-0")
        self.dll = ctypes.CDLL(path)
        self._set_prototypes()
        log.info("linux_vci loaded %s", path)

    def _set_prototypes(self) -> None:
        d = self.dll
        d.VCI_OpenDevice.argtypes = [c_uint32, c_uint32, c_uint32]
        d.VCI_OpenDevice.restype = c_uint32
        d.VCI_CloseDevice.argtypes = [c_uint32, c_uint32]
        d.VCI_CloseDevice.restype = c_uint32
        d.VCI_InitCAN.argtypes = [c_uint32, c_uint32, c_uint32,
                                  POINTER(ZCANFD_INIT)]
        d.VCI_InitCAN.restype = c_uint32
        d.VCI_StartCAN.argtypes = [c_uint32, c_uint32, c_uint32]
        d.VCI_StartCAN.restype = c_uint32
        d.VCI_ResetCAN.argtypes = [c_uint32, c_uint32, c_uint32]
        d.VCI_ResetCAN.restype = c_uint32
        d.VCI_Transmit.argtypes = [c_uint32, c_uint32, c_uint32,
                                   POINTER(ZCAN_20_MSG), c_uint32]
        d.VCI_Transmit.restype = c_uint32
        d.VCI_TransmitFD.argtypes = [c_uint32, c_uint32, c_uint32,
                                     POINTER(ZCAN_FD_MSG), c_uint32]
        d.VCI_TransmitFD.restype = c_uint32
        d.VCI_GetReceiveNum.argtypes = [c_uint32, c_uint32, c_uint32]
        d.VCI_GetReceiveNum.restype = c_uint32
        d.VCI_Receive.argtypes = [c_uint32, c_uint32, c_uint32,
                                  POINTER(ZCAN_20_MSG), c_uint32, c_uint32]
        d.VCI_Receive.restype = c_uint32
        d.VCI_ReceiveFD.argtypes = [c_uint32, c_uint32, c_uint32,
                                    POINTER(ZCAN_FD_MSG), c_uint32, c_uint32]
        d.VCI_ReceiveFD.restype = c_uint32
        d.VCI_SetReference.argtypes = [c_uint32, c_uint32, c_uint32,
                                       c_uint32, ctypes.c_void_p]
        d.VCI_SetReference.restype = c_uint32
        d.VCI_GetReference.argtypes = [c_uint32, c_uint32, c_uint32,
                                       c_uint32, ctypes.c_void_p]
        d.VCI_GetReference.restype = c_uint32
        d.VCI_ClearBuffer.argtypes = [c_uint32, c_uint32, c_uint32]
        d.VCI_ClearBuffer.restype = c_uint32

    # ---- 设备级 ----
    def open_device(self, card: int = 0) -> bool:
        return bool(self.dll.VCI_OpenDevice(USBCANFD, card, 0))

    def close_device(self, card: int = 0) -> None:
        self.dll.VCI_CloseDevice(USBCANFD, card)

    def read_serial(self, card: int = 0) -> str:
        """读序列号 (CMD_GET_SN, 返回 uint32 数组, 官方 demo 未细说格式 —
        实测为 4 个 uint32; 取非零段转 hex)。"""
        buf = (c_uint32 * 4)()
        r = self.dll.VCI_GetReference(USBCANFD, card, 0, CMD_GET_SN,
                                      ctypes.cast(buf, ctypes.c_void_p))
        if not r:
            return ""
        parts = [f"{buf[i]:08X}" for i in range(4) if buf[i]]
        return "".join(parts)

    # ---- 通道级 ----
    def init_can(self, card: int, channel: int, bitrate: int) -> bool:
        t1, t2, sjw, smp, brp = _arbitration_timing(bitrate)
        cfg = ZCANFD_INIT()
        cfg.clk = 60000000
        cfg.mode = 0
        cfg.abit = _BIT_TIMING(tseg1=t1, tseg2=t2, sjw=sjw, smp=smp, brp=brp)
        cfg.dbit = _BIT_TIMING(**_DATA_TIMING)
        return bool(self.dll.VCI_InitCAN(USBCANFD, card, channel, byref(cfg)))

    def set_term_resistance(self, card: int, channel: int, on: bool = True) -> None:
        v = c_uint8(1 if on else 0)
        self.dll.VCI_SetReference(USBCANFD, card, channel, CMD_CAN_TRES,
                                  ctypes.cast(byref(v), ctypes.c_void_p))

    def start_can(self, card: int, channel: int) -> bool:
        return bool(self.dll.VCI_StartCAN(USBCANFD, card, channel))

    def reset_can(self, card: int, channel: int) -> None:
        self.dll.VCI_ResetCAN(USBCANFD, card, channel)

    def clear_buffer(self, card: int, channel: int) -> None:
        self.dll.VCI_ClearBuffer(USBCANFD, card, channel)

    # ---- 发送 ----
    def transmit(self, card: int, channel: int, can_id: int,
                 data: bytes, pad_to8: bool = False) -> int:
        """CAN2.0 发送; 返回成功帧数 (1=ok)。"""
        msg = ZCAN_20_MSG()
        msg.hdr.inf.txm = 0
        msg.hdr.inf.fmt = 0
        msg.hdr.inf.sdf = 0
        msg.hdr.inf.sef = 1 if can_id > 0x7FF else 0
        msg.hdr.id = can_id
        msg.hdr.chn = channel
        d = bytes(data)
        if pad_to8 and len(d) < 8:
            d = (d + b"\xAA" * 8)[:8]
        msg.hdr.len = min(len(d), 8)
        for i in range(msg.hdr.len):
            msg.dat[i] = d[i]
        return int(self.dll.VCI_Transmit(USBCANFD, card, channel,
                                         byref(msg), 1))

    def transmit_fd(self, card: int, channel: int, can_id: int,
                    data: bytes, brs: bool = True) -> int:
        msg = ZCAN_FD_MSG()
        msg.hdr.inf.txm = 0
        msg.hdr.inf.fmt = 1
        msg.hdr.inf.sdf = 0
        msg.hdr.inf.sef = 1 if can_id > 0x7FF else 0
        msg.hdr.inf.brs = 1 if brs else 0
        msg.hdr.id = can_id
        msg.hdr.chn = channel
        d = bytes(data)[:64]
        msg.hdr.len = len(d)
        for i in range(len(d)):
            msg.dat[i] = d[i]
        return int(self.dll.VCI_TransmitFD(USBCANFD, card, channel,
                                           byref(msg), 1))

    # ---- 接收 ----
    def get_receive_num(self, card: int, channel: int, fd: bool = False) -> int:
        # CANFD 数量用高位标志 0x80000000+chn (官方 demo)
        port = (0x80000000 + channel) if fd else channel
        return int(self.dll.VCI_GetReceiveNum(USBCANFD, card, port))

    def receive(self, card: int, channel: int, max_n: int,
                fd: bool = False, wait_ms: int = 0) -> List[Tuple[int, bytes]]:
        """返回 [(can_id, data), ...] (只收数据帧, 标准扩展都处理)。"""
        out: List[Tuple[int, bytes]] = []
        if fd:
            arr = (ZCAN_FD_MSG * max_n)()
            n = int(self.dll.VCI_ReceiveFD(USBCANFD, card, channel, arr,
                                           max_n, wait_ms))
            for i in range(n):
                h = arr[i].hdr
                if h.inf.sdf:       # 远程帧不带数据, 跳过
                    continue
                out.append((h.id, bytes(arr[i].dat[:h.len])))
        else:
            arr = (ZCAN_20_MSG * max_n)()
            n = int(self.dll.VCI_Receive(USBCANFD, card, channel, arr,
                                         max_n, wait_ms))
            for i in range(n):
                h = arr[i].hdr
                if h.inf.sdf:
                    continue
                out.append((h.id, bytes(arr[i].dat[:h.len])))
        return out


def check_permissions() -> Optional[str]:
    """检测 USB 设备权限; 返回用户可读的修复指引 (无问题时 None)。"""
    if platform.system() != "Linux":
        return None
    # 无 lsusb 时跳过检测
    import subprocess
    try:
        r = subprocess.run(["lsusb"], capture_output=True, text=True,
                           timeout=5)
        found = any("3068:0009" in line for line in r.stdout.splitlines())
    except (OSError, subprocess.SubprocessError):
        return None
    if not found:
        return None
    # 找到设备 — 检查节点权限
    import glob
    for path in glob.glob("/dev/bus/usb/*/*"):
        try:
            with open(path, "rb") as f:
                desc = f.read(18)
            if len(desc) >= 9 and desc[8] == 0x68 and desc[9] == 0x30:
                # little-endian idVendor=0x3068; idProduct 在 10-11
                if desc[10] == 0x09 and desc[11] == 0x00:
                    if not os.access(path, os.R_OK | os.W_OK):
                        return (
                            f"ZLG USB 设备存在但无读写权限 ({path})。修复:\n"
                            "  sudo cp .../vendor/zlg/linux/50-usbcanfd.rules "
                            "/etc/udev/rules.d/ && sudo udevadm control --reload\n"
                            "  或临时: sudo chmod 666 " + path)
                    return None
        except OSError:
            continue
    return None
