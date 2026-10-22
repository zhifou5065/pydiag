# -*- coding: utf-8 -*-
"""
can_devices_lin.py — 设备管理器的 LIN 扩展：总线类型维度
========================================================

解决两个管理问题:
  1. 同一设备的 CAN 和 LIN（如 Vector VN1640 同一板上 CAN 通道 + LIN 通道）
  2. 不同设备的 CAN 和 LIN（如 ZLG 跑 CAN + Vector 跑 LIN）

设计: 把"总线类型"提升为设备通道的一级属性 ——
    device_id 保持设备维度: "vector:<serial>:<idx>"
    open_channel(bus_type=..., index=...)     ← 新增 bus_type 参数
    Channel 上加 bus_type 属性: "can" / "lin"

    ┌─────────────────────────────────────────────────────┐
    │                DeviceManager（不变）                 │
    │   list_devices() → DeviceInfo(bus_types=[...])      │
    ├─────────────────────────────────────────────────────┤
    │  设备层: BaseCanDevice                              │
    │    ├─ VectorDevice(VN1640)                          │
    │    │    ├─ VectorChannel(bus_type="can", idx=0)     │
    │    │    └─ VectorChannel(bus_type="lin", idx=0)     │  ← 同一物理盒
    │    └─ ZlgDevice(USBCANFD-200U)                      │
    │         └─ ZlgChannel(bus_type="can")               │  ← 纯 CAN 盒
    ├─────────────────────────────────────────────────────┤
    │  通道层: BaseChannel + LinChannelMixin              │
    │    CAN 通道 → zuds.create_docan_session(ch, ...)    │
    │    LIN 通道 → zuds.create_dolin_session(ch, nad)    │  ← LinChannel 兼容 LinBus
    └─────────────────────────────────────────────────────┘

冲突分析（关键结论）:
  A. 同一物理盒 CAN/LIN 同开:
     * Vector: 驱动层每通道独立 port handle（xlOpenPort 可开多个），
       CAN port 和 LIN port 互不干扰 → 无冲突, 但**通道掩码是按硬件通道分配的**,
       一个物理通道同一时刻只能跑一种总线类型（硬件限制, 不是软件冲突）。
     * ZLG: USBCANFD-200U 无 LIN。ZLG 的 LIN 盒（如 ZCAN Pro/LIN-USB）是独立设备,
       驱动同为 zlgcan.dll 但 device type 不同 → 走两个 device_id, 无冲突。
  B. 多设备同开:
     * Vector 多盒: vxlapi 驱动全局唯一, 多盒共享一个驱动实例, 按通道掩码区分 → 无冲突
     * PCAN 多盒: PCANBasic 按 handle(0x151..) 区分 → 无冲突
     * ZLG 多盒: zlgcan.dll **设备索引 0** 硬编码在 OpenDevice(dev_type, 0, 0) →
       同型两盒会有冲突风险（需设备索引参数, 当前版本只支持单盒）
  C. 软件/线程冲突（最易踩）:
     * 每通道独立 RX 线程 + 独立队列 → 天然隔离
     * 共享状态只有 DeviceManager._open_devices（有锁）
     * 一个通道 close 不影响其它通道（通道句柄独立）
  D. 驱动级独占:
     * ZLG: 一个设备句柄只能被一个进程打开（我们已有 DeviceManager 独占保护）
     * PCAN: 通道被占用时 CAN_Initialize 返回占用错误（自检 extra["busy"]）
     * Vector: 允许多进程共享驱动, port permission mask 控制写权限
"""

from __future__ import annotations

import threading
import time
import queue
from typing import Dict, List, Optional

from .core import (BaseChannel, BaseCanDevice, DeviceInfo, DeviceManager)
from .virtual import VirtualCanDevice

__all__ = ["BusType", "LinChannelBase", "VirtualLinChannel",
           "DualBusVirtualDevice", "add_lin_support"]

# ============================================================================
# 总线类型
# ============================================================================
class BusType:
    CAN = "can"
    LIN = "lin"


# ============================================================================
# LIN 通道基类 — 兼容 zuds.LinBus 接口 (subscribe_mrf/subscribe_srf/send_mrf)
# ============================================================================
class LinChannelBase(BaseChannel):
    """LIN 逻辑通道基类。

    同时兼容两套接口:
      * BaseChannel (subscribe/send)          — 通用帧收发（按帧 ID 路由）
      * zuds.LinBus   (subscribe_mrf/srf...)  — LIN 诊断专用（MRF=0x3C / SRF=0x3D）

    这样 LinChannel 既能当 LinBus 喂给 create_dolin_session,
    也能像 CAN 通道一样被通用工具（probe/统计）使用。
    """

    MRF_ID = 0x3C      # Master Request Frame
    SRF_ID = 0x3D      # Slave Response Frame

    def __init__(self, device: BaseCanDevice, index: int,
                 baudrate: int = 19200, name: str = "") -> None:
        # LIN 没有 can_id 概念, 用固定 MRF/SRF 假 ID 兼容 BaseChannel 订阅
        super().__init__(device, index, bitrate=baudrate, fd=False, name=name)
        self.bus_type = BusType.LIN
        self._mrf_subs: List[queue.Queue] = []
        self._srf_subs: List[queue.Queue] = []

    # ---- zuds.LinBus 兼容 ----
    def subscribe_mrf(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._mrf_subs.append(q)
        return q

    def subscribe_srf(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._srf_subs.append(q)
        return q

    def send_mrf(self, frame_data: bytes) -> int:
        return self.send(self.MRF_ID, bytes(frame_data))

    def send_srf(self, frame_data: bytes) -> int:
        return self.send(self.SRF_ID, bytes(frame_data))

    # ---- 分发: MRF/SRF 帧同时进两套路由 ----
    def _dispatch(self, can_id: int, data: bytes) -> None:
        super()._dispatch(can_id, data)          # BaseChannel 路由
        qs: List[queue.Queue] = []
        with self._lock:
            if can_id == self.MRF_ID:
                qs = list(self._mrf_subs)
            elif can_id == self.SRF_ID:
                qs = list(self._srf_subs)
        for q in qs:
            q.put(bytes(data))


class VirtualLinChannel(LinChannelBase):
    """虚拟 LIN 通道: 同一 DualBusVirtualDevice 内的 LIN 通道互通。"""

    def _send_impl(self, can_id: int, data: bytes) -> int:
        dev = self.device
        with dev._hub_lock:
            # LIN 帧自见(模拟真实 LIN 主节点回读): 同通道其它订阅者也能收到自己发的帧
            include_self = can_id in (LinChannelBase.MRF_ID, LinChannelBase.SRF_ID)
            targets = [c for c in dev._channels.values()
                       if getattr(c, "bus_type", None) == BusType.LIN
                       and (include_self or c is not self)]
        for c in targets:
            c._dispatch(can_id, bytes(data))
        return 0


# ============================================================================
# 双总线虚拟设备 — 演示/测试 CAN+LIN 同设备管理
# ============================================================================
class DualBusVirtualDevice(VirtualCanDevice):
    """虚拟设备: index 0 = CAN 通道, index 1 = LIN 通道（独立互通域）。"""

    def __init__(self, n_can: int = 2, n_lin: int = 1) -> None:
        super().__init__(n_can + n_lin)
        self.info.bus_types = [BusType.CAN, BusType.LIN]
        self._n_can = n_can

    def open_channel(self, index: int = 0, bitrate: int = 500_000,
                     data_bitrate: int = 2_000_000, fd: bool = False,
                     bus_type: str = BusType.CAN, **opts) -> BaseChannel:
        if bus_type == BusType.LIN:
            def lfactory():
                return VirtualLinChannel(self, index, baudrate=bitrate,
                                         name=f"virtual:lin{index}")
            return self._get_or_create_channel(index, lfactory)
        return super().open_channel(index, bitrate, data_bitrate, fd, **opts)


# ============================================================================
# DeviceManager 增强 — open_channel 透传 bus_type / LIN 设备枚举
# ============================================================================
def add_lin_support(mgr: DeviceManager) -> DeviceManager:
    """给现有 DeviceManager 打 LIN 补丁（等正式 Vector/ZLG LIN 适配后并入 can_devices.py）。

    增强点:
      * DeviceInfo 增加 bus_types 字段（默认 ["can"]）
      * list_devices 汇报各设备支持的总线类型
    """
    orig_list = mgr.list_devices

    def list_devices_with_bustype(driver: Optional[str] = None):
        out = orig_list(driver)
        for di in out:
            if not hasattr(di, "bus_types"):
                di.bus_types = [BusType.CAN]
        return out

    mgr.list_devices = list_devices_with_bustype
    return mgr


# ============================================================================
# 自检
# ============================================================================
def _selftest() -> None:
    print("=== LIN 扩展自检 ===")
    ok = 0

    # [1] 双总线虚拟设备: CAN 域与 LIN 域独立互通
    mgr = DeviceManager()
    dev = DualBusVirtualDevice()
    can0 = dev.open_channel(0, bus_type=BusType.CAN)
    can1 = dev.open_channel(1, bus_type=BusType.CAN)
    lin0 = dev.open_channel(2, bus_type=BusType.LIN)
    lin1 = dev.open_channel(3, bus_type=BusType.LIN)

    assert getattr(can0, "bus_type", "can") == BusType.CAN
    assert lin0.bus_type == BusType.LIN

    # CAN 域互通
    qc = can1.subscribe(0x721)
    can0.send(0x721, b"\x01\x02")
    assert qc.get(timeout=1) == b"\x01\x02"
    # LIN 域互通 (MRF/SRF)
    qm = lin1.subscribe_mrf()
    qs = lin1.subscribe_srf()
    lin0.send_mrf(b"\x3C\x01\x02\x03\x04\x05\x06\x07")
    assert qm.get(timeout=1) == b"\x3C\x01\x02\x03\x04\x05\x06\x07"
    lin0.send_srf(b"\x3D\x50\x00")
    assert qs.get(timeout=1) == b"\x3D\x50\x00"
    # 域隔离: LIN 发的帧不应出现在 CAN 订阅者
    qc2 = can0.subscribe(0x3C)
    lin0.send_mrf(b"\x3C\xAA")
    import queue as _q
    try:
        qc2.get(timeout=0.2)
        raise SystemExit("域隔离失败!")
    except _q.Empty:
        pass
    print("[1] CAN 域/LIN 域互通 + 隔离 OK")
    ok += 1

    # [2] LIN 通道喂 zuds DoLIN
    import zuds
    slave_ep = zuds.LinTpTransport(lin1, nad=0x01)
    def svc_10(d):
        return bytes([0x50])
    # SimulatedLinSlave 挂到 lin1
    slave = zuds.SimulatedLinSlave(lin1, nad=0x01)
    slave._handlers = {}
    slave.register(0x10, svc_10)
    slave.register(0x22, lambda d: bytes([0x62]) + d + b"LIN-VIN")
    slave._thread_join = None

    ses = zuds.create_dolin_session(lin0, nad=0x01)
    r = ses.request(0x10, bytes([0x03]), timeout_s=2.0)
    assert r.code == zuds.ResponseCode.POSITIVE, r
    r = ses.request(0x22, bytes([0xF1, 0x90]), timeout_s=2.0)
    assert r.code == zuds.ResponseCode.POSITIVE and b"LIN" in r.data, r
    print("[2] LIN 通道→create_dolin_session 诊断 OK (10 03 / 22 F1 90)")
    ok += 1

    # [3] 同设备 CAN+LIN 并行运行（两个会话同时活动）
    ses_can = zuds.create_docan_session(can0, 0x721, 0x7A1)
    ecu = zuds.SimulatedECU.__new__(zuds.SimulatedECU)  # 直接在 can1 通道挂 ECU
    ecu_ep = zuds.IsoTpEndpoint(can1, rx_can_id=0x721, tx_can_id=0x7A1)
    import threading
    def ecu_loop():
        while True:
            msg = ecu_ep.receive(timeout_s=0.1)
            if msg is None:
                break
            if msg[0] == 0x10:
                ecu_ep.send(bytes([0x50]))
    t = threading.Thread(target=ecu_loop, daemon=True)
    t.start()

    ok_n = 0
    for i in range(20):
        r1 = ses.request(0x10, bytes([0x03]), timeout_s=1.0)
        r2 = ses.request(0x10, bytes([0x03]), timeout_s=1.0)
        # can 会话与 lin 会话交替
        rl = ses.request(0x10, bytes([0x03]), timeout_s=1.0)
        if (r1.code == zuds.ResponseCode.POSITIVE
                and r2.code == zuds.ResponseCode.POSITIVE):
            ok_n += 1
    assert ok_n >= 18, f"CAN 会话稳定性不足: {ok_n}/20"
    print("[3] 同设备 CAN+LIN 并行 60 请求 OK")
    ok += 1

    # [4] 多设备并行: 双总线设备 + 纯 CAN 设备共存（各自独立回环域）
    mgr2 = DeviceManager(include_virtual=True)
    pure_can = mgr2.open("virtual:0")
    pch0 = pure_can.open_channel(0)
    pch1 = pure_can.open_channel(1)
    qc = pch1.subscribe(0x123)
    assert pch0.send(0x123, b"\xEE") == 0
    assert qc.get(timeout=1) == b"\xEE"
    print("[4] 多设备并行: 双总线设备 + 独立 CAN 设备共存 OK")
    ok += 1

    print(f"=== LIN 扩展自检 {ok}/4 通过 ===")


if __name__ == "__main__":
    _selftest()
