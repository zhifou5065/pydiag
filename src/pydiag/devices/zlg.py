# -*- coding: utf-8 -*-
"""多厂商 CAN/LIN 设备适配层。"""
from __future__ import annotations

import ctypes, os, queue, struct, sys, threading, time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

"""ZLG USBCANFD 适配: 多盒序列号枚举 / cross_watch / pad_to8。"""
from .core import (DeviceInfo, BaseChannel, BaseCanDevice, BaseDriver)
from ..logutil import get_logger, LOG_PATH

log = get_logger(__name__)


class ZlgDriver(BaseDriver):
    name = "zlg"

    def __init__(self, runtime_dir: Optional[str] = None) -> None:
        self.runtime_dir = runtime_dir or self._find_runtime()
        self._zcan = None

    @staticmethod
    def _find_runtime() -> Optional[str]:
        here = os.path.dirname(os.path.abspath(__file__))
        pkg_root = os.path.dirname(here)
        # 注: 这里定位的是 Python 绑定 (zlgcan.py) 所在目录;
        # 二进制库的实际加载在 zlgcan.py 内部做"系统优先, 回退包内"。
        cands = [
            os.environ.get("ZLG_RUNTIME", ""),
            # 包内预置运行时: site-packages/pydiag/zlg_runtime
            os.path.join(pkg_root, "zlg_runtime"),
            # 包内 vendor 预置: site-packages/pydiag/vendor/zlg
            os.path.join(pkg_root, "vendor", "zlg"),
            # 源码树布局: src/pydiag/devices → 项目根/zlg_runtime
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(here))),
                         "zlg_runtime"),
        ]
        for c in cands:
            if not c:
                continue
            # 定位的是 Python 绑定 (zlgcan.py) 所在目录;
            # 二进制库 (zlgcan.dll / libzlgcan.so) 由 zlgcan.py 内部按
            # "系统优先 → 包内 vendor/zlg → 绑定目录"加载。
            if os.path.isfile(os.path.join(c, "zlgcan.py")):
                return c
        return None

    def is_available(self) -> bool:
        try:
            self._load()
            return True
        except Exception:
            return False

    def _load(self):
        from ..platform import IS_WINDOWS, IS_LINUX
        if not (IS_WINDOWS or IS_LINUX):
            raise OSError("ZLG 驱动支持 Windows/Linux; macOS 无官方库")
        if self._zcan is not None:
            return self._zcan
        if IS_LINUX:
            # Linux: 官方 libusb 方案 (libusbcanfd.so, VCI_* API)
            from .linux_vci import LinuxVci, check_permissions
            perm = check_permissions()
            if perm:
                raise OSError(perm)
            self._zcan = LinuxVci()
            self._consts = dict(LINUX_VCI=True)
            return self._zcan
        if not self.runtime_dir:
            raise OSError(
                "zlg_runtime 未找到 (Windows 需 zlgcan.dll)")
        sys.path.insert(0, self.runtime_dir)
        # Python 绑定 (zlgcan.py) 在 zlg_runtime; 二进制库由 zlgcan.py 内部
        # 按"系统优先 → 包内 vendor/zlg → 绑定目录"查找加载。
        if IS_WINDOWS:
            from ..vendor import vendor_lib_dir
            os.add_dll_directory(vendor_lib_dir("zlg"))
            kd = os.path.join(self.runtime_dir, "kerneldlls")
            if os.path.isdir(kd):
                os.add_dll_directory(kd)
        elif IS_LINUX:
            # Linux: vendor/zlg 与 kerneldlls 加入加载路径提示
            from ..vendor import vendor_lib_dir
            for so_dir in (vendor_lib_dir("zlg"), self.runtime_dir,
                           os.path.join(self.runtime_dir, "kerneldlls")):
                if os.path.isdir(so_dir) and so_dir not in sys.path:
                    sys.path.insert(0, so_dir)
        import io as _io
        _real_stdout = sys.stdout
        try:
            # 官方 zlgcan.py 的 ZCAN.__init__ 会无条件 print("lookup: ...")——探测期静音
            sys.stdout = _io.StringIO()
            from zlgcan import (ZCAN, ZCAN_USBCANFD_200U, ZCAN_TYPE_CAN,
                                ZCAN_TYPE_CANFD, ZCAN_CHANNEL_INIT_CONFIG,
                                ZCAN_STATUS_OK)
            self._zcan = ZCAN()
        finally:
            sys.stdout = _real_stdout
        # 存常量引用供 Device/Channel 用
        self._consts = dict(
            USBCANFD_200U=ZCAN_USBCANFD_200U,
            TYPE_CAN=ZCAN_TYPE_CAN, TYPE_CANFD=ZCAN_TYPE_CANFD,
            CFG=ZCAN_CHANNEL_INIT_CONFIG, OK=ZCAN_STATUS_OK)
        return self._zcan

    #: 枚举的最大设备索引（ZLG 支持多盒同挂）
    MAX_DEV_INDEX = 4

    def list_devices(self) -> List[DeviceInfo]:
        """枚举所有 ZLG 盒（多盒支持）: device_index 0..MAX, 读序列号做唯一标识。

        device_id 形如 "zlg:0123456789ABCDEF"（序列号），无序列号回退 "zlg:idx0"。
        """
        try:
            z = self._load()
        except Exception as e:
            log.debug("zlg enumerate skip: %s", e)
            return []
        out = []
        linux = self._consts.get("LINUX_VCI")
        for idx in range(self.MAX_DEV_INDEX):
            serial = ""
            if linux:
                if not z.open_device(idx):
                    log.debug("zlg dev_index=%d VCI_OpenDevice empty (absent/occupied)", idx)
                    continue
                try:
                    serial = z.read_serial(idx)
                except Exception:
                    serial = ""
                z.close_device(idx)
            else:
                h = z.OpenDevice(self._consts["USBCANFD_200U"], idx, 0)
                if not h:
                    log.debug("zlg dev_index=%d OpenDevice empty (absent/busy)", idx)
                    continue
                try:
                    info = z.GetDeviceInf(h)
                    if info is not None:
                        serial = info.serial or ""
                except Exception:
                    serial = ""
                try:
                    z.CloseDevice(h)
                except Exception:
                    pass
            dev_id = f"zlg:{serial}" if serial else f"zlg:idx{idx}"
            out.append(DeviceInfo(
                driver=self.name, device_id=dev_id, name="USBCANFD-200U",
                serial=serial, channels=2, extra={"dev_index": idx}))
        return out

    def open(self, device_id: str) -> "ZlgDevice":
        """按 device_id 打开: "zlg:<serial>" 或 "zlg:idx<N>"。

        序列号形式: 枚举所有索引读序列号匹配; idx 形式: 直接用索引。
        """
        payload = device_id.split(":", 1)[1] if ":" in device_id else ""
        z = self._load()
        if payload.startswith("idx"):
            dev_index = int(payload[3:])
            return ZlgDevice(
                DeviceInfo(driver=self.name, device_id=device_id,
                           name="USBCANFD-200U", channels=2,
                           extra={"dev_index": dev_index}),
                z, self._consts, self.runtime_dir, dev_index=dev_index)
        # 序列号匹配
        linux = self._consts.get("LINUX_VCI")
        for idx in range(self.MAX_DEV_INDEX):
            if linux:
                if not z.open_device(idx):
                    continue
                try:
                    serial = z.read_serial(idx)
                except Exception:
                    serial = ""
                z.close_device(idx)
            else:
                h = z.OpenDevice(self._consts["USBCANFD_200U"], idx, 0)
                if not h:
                    continue
                serial = ""
                try:
                    info = z.GetDeviceInf(h)
                    serial = info.serial or "" if info is not None else ""
                except Exception:
                    pass
                z.CloseDevice(h)
            if serial == payload:
                return ZlgDevice(
                    DeviceInfo(driver=self.name, device_id=device_id,
                               name="USBCANFD-200U", serial=serial, channels=2,
                               extra={"dev_index": idx}),
                    z, self._consts, self.runtime_dir, dev_index=idx)
        raise RuntimeError(f"ZLG 设备未找到: {device_id}（检查接线或设备索引）")


class ZlgDevice(BaseCanDevice):
    """ZLG 物理设备。一个进程只能打开一个（ZLG 独占），由 DeviceManager 保证。"""

    def __init__(self, info: DeviceInfo, zcan, consts: dict, runtime_dir: str,
                 dev_index: int = 0) -> None:
        super().__init__(info)
        self._z = zcan
        self._c = consts
        self._dev_index = int(dev_index)
        log.info("zlg open device index=%d id=%s", dev_index, info.device_id)
        if consts.get("LINUX_VCI"):
            self._h = 1 if zcan.open_device(self._dev_index) else 0
        else:
            self._h = zcan.OpenDevice(consts["USBCANFD_200U"], self._dev_index, 0)
        if not self._h:
            log.error("zlg OpenDevice failed index=%d (occupied by another process? USB?)",
                      self._dev_index)
            raise RuntimeError(
                f"ZLG 设备打开失败(index={self._dev_index})（被其它程序占用/USB 未就绪; "
                "详情见日志: " + LOG_PATH + "）")

    def open_channel(self, index: int = 0, bitrate: int = 500_000,
                     data_bitrate: int = 2_000_000, fd: bool = False,
                     cross_watch: bool = True, pad_to8: bool = True,
                     verbose: bool = False) -> BaseChannel:
        def factory():
            try:
                return ZlgChannel(self, index, bitrate, data_bitrate, fd,
                                  cross_watch=cross_watch, pad_to8=pad_to8,
                                  verbose=verbose)
            except RuntimeError:
                # ZLG 驱动内部状态可能残留: 连续 InitCAN 失败时唯一可靠恢复 = 设备级重开。
                self.recover()
                return ZlgChannel(self, index, bitrate, data_bitrate, fd,
                                  cross_watch=cross_watch, pad_to8=pad_to8,
                                  verbose=verbose)
        return self._get_or_create_channel(index, factory)

    def recover(self) -> None:
        """设备级恢复: 停线程 → CloseDevice → OpenDevice, 重建此前打开的活通道。"""
        import time as _time
        saved = [(idx, ch) for idx, ch in list(self._channels.items())
                 if not ch._closed]
        with self._lock:
            self._channels.clear()
        # 先停所有 RX 线程（CloseDevice 后线程会卡死在 DLL 调用里）
        for idx, ch in saved:
            ch._stop.set()
        for idx, ch in saved:
            try:
                ch._thread.join(timeout=2.0)
            except Exception:
                pass
        try:
            if self._c.get("LINUX_VCI"):
                self._z.close_device(self._dev_index)
            else:
                self._z.CloseDevice(self._h)
        except Exception:
            pass
        _time.sleep(0.5)
        log.warning("zlg device-level recovery: reopening dev_index=%d", self._dev_index)
        if self._c.get("LINUX_VCI"):
            self._h = 1 if self._z.open_device(self._dev_index) else 0
        else:
            self._h = self._z.OpenDevice(self._c["USBCANFD_200U"],
                                         self._dev_index, 0)
        if not self._h:
            log.error("zlg recovery OpenDevice failed (device may need replug)")
            raise RuntimeError("ZLG 设备恢复失败（OpenDevice）— 尝试重新插拔设备")
        for idx, ch in saved:
            def f(i=idx, c=ch):
                return ZlgChannel(self, i, c.bitrate, c.data_bitrate, c.fd,
                                  cross_watch=getattr(c, "cross_watch", True),
                                  pad_to8=getattr(c, "pad_to8", True))
            self._channels[idx] = f()

    def _close_impl(self) -> None:
        try:
            if self._c.get("LINUX_VCI"):
                self._z.close_device(self._dev_index)
            else:
                self._z.CloseDevice(self._h)
        except Exception:
            pass

    @property
    def handle(self):
        return self._h


class ZlgChannel(BaseChannel):
    """ZLG 逻辑通道：初始化 + 收发 + 后台线程分发（含 cross_watch）。"""

    def __init__(self, device: ZlgDevice, index: int, bitrate: int,
                 data_bitrate: int, fd: bool, cross_watch: bool,
                 pad_to8: bool, verbose: bool = False) -> None:
        super().__init__(device, index, bitrate, data_bitrate, fd)
        self.cross_watch = cross_watch
        self.pad_to8 = pad_to8
        self.verbose = verbose
        z, c = device._z, device._c
        if c.get("LINUX_VCI"):
            # Linux VCI: 通道号即 Port, 无 IProperty; cross_watch 用另一 Port
            if not z.init_can(device._dev_index, index, bitrate):
                raise RuntimeError(
                    f"ZLG VCI_InitCAN(ch={index}) 失败 (波特率 {bitrate} 不支持? 设备被占用?)")
            z.set_term_resistance(device._dev_index, index, True)
            if not z.start_can(device._dev_index, index):
                raise RuntimeError(f"ZLG VCI_StartCAN(ch={index}) 失败")
            log.info("zlg(linux) channel %d started (bitrate=%d fd=%s)",
                     index, bitrate, fd)
            self._chn = index
            self._chn_b = None
            if cross_watch and index in (0, 1):
                try:
                    other = 1 - index
                    if z.init_can(device._dev_index, other, bitrate):
                        z.set_term_resistance(device._dev_index, other, True)
                        if z.start_can(device._dev_index, other):
                            self._chn_b = other
                except Exception:
                    self._chn_b = None
            self._stop = threading.Event()
            self._thread = threading.Thread(target=self._rx_loop, daemon=True,
                                            name=f"ZlgCh{index}-Rx")
            self._thread.start()
            return
        self._setup_props(device.handle, index, bitrate, data_bitrate, fd)
        self._chn = self._init_can(device.handle, index, bitrate, fd)
        z.StartCAN(self._chn)
        log.info("zlg channel %d started (bitrate=%d fd=%s)", index, bitrate, fd)
        self._chn_b = None
        if cross_watch and index in (0, 1):
            try:
                self._chn_b = self._init_can(device.handle, 1 - index,
                                             bitrate, fd)
                z.StartCAN(self._chn_b)
            except Exception:
                self._chn_b = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._rx_loop, daemon=True,
                                        name=f"ZlgCh{index}-Rx")
        self._thread.start()

    # ---- 初始化 ----
    def _setup_props(self, h, index: int, abit: int, dbit: int, fd: bool) -> None:
        z = self.device._z
        ip = z.GetIProperty(h)
        p = "%d/" % index
        props = [(p + "clock", "60000000"),
                 (p + "initenal_resistance", "1")]
        if fd:
            props += [(p + "canfd_standard", "0"),
                      (p + "protocol", "1"),
                      (p + "canfd_abit_baud_rate", str(abit)),
                      (p + "canfd_dbit_baud_rate", str(dbit))]
        else:
            props += [(p + "protocol", "0")]
        for k, v in props:
            try:
                z.SetValue(ip, k, v)
            except Exception:
                pass
        z.ReleaseIProperty(ip)

    def _init_can(self, h, index: int, abit: int, fd: bool):
        z, c = self.device._z, self.device._c
        cfg = c["CFG"]()
        if fd:
            cfg.can_type = c["TYPE_CANFD"]
            cfg.config.canfd.mode = 0
            cfg.config.canfd.abit_timing = 0
            cfg.config.canfd.dbit_timing = 0
        else:
            cfg.can_type = c["TYPE_CAN"]
            btr = {1_000_000: (0x00, 0x14), 500_000: (0x00, 0x1C),
                   250_000: (0x01, 0x1C), 125_000: (0x03, 0x1C)}
            t0, t1 = btr.get(abit, (0x00, 0x1C))
            cfg.config.can.timing0 = t0
            cfg.config.can.timing1 = t1
            cfg.config.can.filter = 0
            cfg.config.can.acc_code = 0
            cfg.config.can.acc_mask = 0xFFFFFFFF
            cfg.config.can.mode = 0
        chn = z.InitCAN(h, index, cfg)
        if not chn:
            raise RuntimeError(f"ZLG InitCAN(ch={index}) 失败")
        return chn

    # ---- 发送 ----
    def _send_impl(self, can_id: int, data: bytes) -> int:
        z = self.device._z
        if self.device._c.get("LINUX_VCI"):
            card = self.device._dev_index
            if self.fd:
                r = z.transmit_fd(card, self._chn, can_id, data)
            else:
                r = z.transmit(card, self._chn, can_id, data,
                               pad_to8=self.pad_to8)
            if r != 1:
                log.error("zlg(linux) ch%d TX failed id=0x%03X r=%d "
                          "(bus-off? device unplugged?)", self.index, can_id, r)
            log.debug("zlg(linux) ch%d TX id=0x%03X dlc=%d data=%s r=%d",
                      self.index, can_id, len(data), data.hex(), r)
            return 0 if r == 1 else 1
        if self.fd:
            from zlgcan import ZCAN_TransmitFD_Data
            m = (ZCAN_TransmitFD_Data * 1)()
            m[0].transmit_type = 0
            m[0].frame.can_id = can_id
            m[0].frame.len = len(data)
            m[0].frame.brs = 1
            for i, b in enumerate(data):
                m[0].frame.data[i] = b
            r = z.TransmitFD(self._chn, m, 1)
        else:
            from zlgcan import ZCAN_Transmit_Data
            m = (ZCAN_Transmit_Data * 1)()
            m[0].transmit_type = 0
            m[0].frame.can_id = can_id
            if self.pad_to8 and len(data) < 8:
                d = (list(data) + [0xAA] * 8)[:8]
                m[0].frame.can_dlc = 8
                for i, b in enumerate(d):
                    m[0].frame.data[i] = b
            else:
                m[0].frame.can_dlc = len(data)
                for i, b in enumerate(data):
                    m[0].frame.data[i] = b
            r = z.Transmit(self._chn, m, 1)
        if r != 1:
            log.error("zlg ch%d TX failed id=0x%03X r=%d (bus-off? device unplugged?)",
                      self.index, can_id, r)
        log.debug("zlg ch%d TX id=0x%03X dlc=%d data=%s r=%d",
                  self.index, can_id, len(data), data.hex(), r)
        return 0 if r == 1 else 1

    # ---- 接收（含 cross_watch 交叉监听） ----
    def _rx_loop(self) -> None:
        if self.device._c.get("LINUX_VCI"):
            self._rx_loop_linux()
            return
        from zlgcan import ZCAN_ReceiveFD_Data, ZCAN_Receive_Data
        z = self.device._z
        while not self._stop.is_set():
            got = False
            for chn in ((self._chn, False), (self._chn_b, True)):
                if chn[0] is None:
                    continue
                ch, cross = chn
                nfd = z.GetReceiveNum(ch, 1)
                nc = z.GetReceiveNum(ch, 0)
                if nfd:
                    msgs, cnt = z.ReceiveFD(ch, nfd)
                    for i in range(cnt):
                        f = msgs[i].frame
                        self._dispatch(f.can_id, bytes(f.data[:f.len]))
                        got = True
                if nc:
                    msgs, cnt = z.Receive(ch, nc)
                    for i in range(cnt):
                        f = msgs[i].frame
                        log.debug("zlg ch%d RX id=0x%03X dlc=%d data=%s%s",
                                  self.index, f.can_id, f.can_dlc,
                                  bytes(f.data[:f.can_dlc]).hex(),
                                  " (cross)" if cross else "")
                        self._dispatch(f.can_id, bytes(f.data[:f.can_dlc]))
                        got = True
            if not got:
                time.sleep(0.001)

    def _rx_loop_linux(self) -> None:
        """Linux VCI 接收轮询 (含 cross_watch 交叉 Port)。"""
        z = self.device._z
        card = self.device._dev_index
        while not self._stop.is_set():
            got = False
            for chn in ((self._chn, False), (self._chn_b, True)):
                if chn[0] is None:
                    continue
                ch, cross = chn
                nc = z.get_receive_num(card, ch, fd=False)
                if nc:
                    for cid, dat in z.receive(card, ch, nc, fd=False):
                        log.debug("zlg(linux) ch%d RX id=0x%03X dlc=%d data=%s%s",
                                  self.index, cid, len(dat), dat.hex(),
                                  " (cross)" if cross else "")
                        self._dispatch(cid, dat)
                        got = True
                nfd = z.get_receive_num(card, ch, fd=True)
                if nfd:
                    for cid, dat in z.receive(card, ch, nfd, fd=True):
                        log.debug("zlg(linux) ch%d RX-FD id=0x%03X len=%d%s",
                                  self.index, cid, len(dat),
                                  " (cross)" if cross else "")
                        self._dispatch(cid, dat)
                        got = True
            if not got:
                time.sleep(0.001)

    def close(self) -> None:
        if self._closed:
            return
        log.debug("zlg ch%d closing", self.index)
        self._stop.set()
        try:
            self._thread.join(timeout=1.0)
        except Exception:
            pass
        # 不做 ResetCAN: ZLG 驱动在交替 InitCAN 场景存在内部状态残留,
        # 通道保持初始化状态, 由设备级 CloseDevice 统一释放;
        # 若必须单通道复位, 调用方可用 force_reset()。
        super().close()
        self.device._remove_channel(self.index)

    def force_reset(self) -> None:
        """显式复位本通道（需要时手动调用; 会触发 ZLG 驱动的连续 InitCAN 问题）。"""
        if self.device._c.get("LINUX_VCI"):
            try:
                self.device._z.reset_can(self.device._dev_index, self._chn)
            except Exception:
                pass
            if self._chn_b is not None:
                try:
                    self.device._z.reset_can(self.device._dev_index, self._chn_b)
                except Exception:
                    pass
            return
        try:
            self.device._z.ResetCAN(self._chn)
        except Exception:
            pass
        if self._chn_b:
            try:
                self.device._z.ResetCAN(self._chn_b)
            except Exception:
                pass

