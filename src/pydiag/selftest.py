# -*- coding: utf-8 -*-
"""pydiag.selftest — 内置自检（虚拟设备全流程, 无需硬件）。"""
from __future__ import annotations

from .devices import DeviceManager, default_manager, VirtualCanDevice


def run(verbose: bool = False) -> int:
    print("=== pydiag 自检（虚拟设备） ===")
    ok = 0
    total = 4

    # [1] 设备管理: 枚举 + 独占 + 隔离
    mgr = DeviceManager(include_virtual=True, register_builtin=False)
    dev = mgr.open("virtual:0")
    assert mgr.open("virtual:0") is dev, "独占保护失败"
    c0 = dev.open_channel(0)
    c1 = dev.open_channel(1)
    q = c1.subscribe(0x100)
    c0.send(0x100, b"\x01\x02")
    assert q.get(timeout=1) == b"\x01\x02"
    print("[1] 设备管理/虚拟回环 OK")
    ok += 1

    # [2] UDS 会话跑在 Channel 上
    from . import zuds
    ses = zuds.create_docan_session(c0, tx_can_id=0x721, rx_can_id=0x7A1)
    # 在 c1 上模拟 ECU: 收到 10 03 → 回 50 03
    from .zuds import IsoTpEndpoint
    ep = IsoTpEndpoint(c1, rx_can_id=0x721, tx_can_id=0x7A1)

    import threading
    stop = threading.Event()

    def ecu():
        while not stop.is_set():
            msg = ep.receive(timeout_s=0.1)
            if msg is None:
                continue
            sid = msg[0]
            if sid == 0x10:
                ep.send(bytes([0x50]) + msg[1:] + b"\x00\x32\x00\xC8")
            elif sid == 0x3E:
                ep.send(bytes([0x7E]) + msg[1:])
            elif sid == 0x22:
                ep.send(bytes([0x62]) + msg[1:] + b"L1NSPGHB8MA700037")

    t = threading.Thread(target=ecu, daemon=True)
    t.start()
    r = ses.request(0x10, bytes([0x03]), timeout_s=1.0)
    # UdsResponse.data 为 POS 响应中 SID 之后的有效数据 (03 00 32 00 C8)
    assert r.code == zuds.ResponseCode.POSITIVE and r.data[:2] == b"\x03\x00", r
    r = ses.request(0x3E, bytes([0x00]), timeout_s=1.0)
    assert r.code == zuds.ResponseCode.POSITIVE, r
    r = ses.request(0x22, bytes([0xF1, 0x90]), timeout_s=1.0)
    assert r.code == zuds.ResponseCode.POSITIVE and b"L1NS" in r.data, r
    stop.set()
    print("[2] UDS 会话 (10 03 / 3E 00 / 22 F1 90) OK")
    ok += 1

    # [3] 安全访问服务层（固定 key 流程, 用 SimulatedECU）
    from pydiag import zuds_security
    ses2 = zuds.create_docan_session(c0, tx_can_id=0x721, rx_can_id=0x7A1)
    ecu2 = zuds.SimulatedECU(c1, tx_can_id=0x7A1, rx_can_id=0x721)
    SEED, KEY = b"\xA9\x0A\x42\x45", b"\x00\x01\x02\x03"

    def sec_handler(req: bytes):
        # SimulatedECU 的 handler 收到 SID 之后的 data:
        # [01] → 67 01+seed; [02 key...] → 67 02; 其它 → None (NRC)
        if len(req) >= 1 and (req[0] & 1):        # 奇数子功能 = requestSeed
            return bytes([0x67, req[0]]) + SEED
        if len(req) >= 1 + len(KEY) and req[1:1 + len(KEY)] == KEY:
            return bytes([0x67, req[0]])
        return None                               # 无应答 → 上层 TIMEOUT/NEG

    ecu2.register(0x10, lambda d: bytes([0x50]))
    ecu2.register(0x27, sec_handler)
    sec = zuds_security.SecurityAccess(ses2)
    seed = sec.request_seed(1)
    assert any(seed), "seed 应非零"
    r = ses2.request(0x27, bytes([0x02]) + KEY, timeout_s=1.0)
    assert r.code == zuds.ResponseCode.POSITIVE, r
    print("[3] 安全访问固定 key 流程 OK")
    ok += 1

    # [4] LIN 通道与 CAN 域隔离
    from .devices.lin import BusType, DualBusVirtualDevice
    ddev = DualBusVirtualDevice(n_can=2, n_lin=2)
    can_ch = ddev.open_channel(0, bus_type=BusType.CAN)
    lin_a = ddev.open_channel(2, bus_type=BusType.LIN)
    lin_b = ddev.open_channel(3, bus_type=BusType.LIN)
    assert can_ch.bus_type == BusType.CAN
    assert lin_a.bus_type == BusType.LIN and lin_b.bus_type == BusType.LIN
    # LIN 域内 a→b 互通
    qlin = lin_b.subscribe_mrf()
    lin_a.send_mrf(bytes(range(8)))
    assert qlin.get(timeout=1) == bytes(range(8))
    # CAN 域与 LIN 域隔离: CAN 通道订阅不到 MRF
    qcan = can_ch.subscribe(0x3C)
    lin_a.send_mrf(bytes(range(8)))
    import queue as _q
    try:
        qcan.get(timeout=0.2)
        raise AssertionError("CAN 域不应收到 LIN 帧")
    except _q.Empty:
        pass
    print("[4] LIN 通道/MRF 路由/域隔离 OK")
    ok += 1

    # [5] 安全算法形态解析层 (sec_algo)
    from .sec_algo import resolve_sec_algo, load_py_algo, parse_kv_params
    from types import SimpleNamespace
    # 4a: 固定 key
    p = SimpleNamespace(sec_mode="fixed_key", key="A1B2C3D4")
    mode, algo = resolve_sec_algo(p)
    assert mode == "fixed_key" and algo == bytes.fromhex("A1B2C3D4"), (mode, algo)
    # 4b: builtin xor
    p = SimpleNamespace(sec_mode="builtin", sec_builtin="xor",
                        sec_builtin_params='{"key":"1A2B3C4D"}')
    mode, algo = resolve_sec_algo(p)
    k = algo(bytes.fromhex("12345678"), 1)
    assert k == bytes.fromhex("081F6A35"), k.hex()
    # 4c: 命令行覆盖 profile
    ns = SimpleNamespace(algo="builtin", builtin="invert", builtin_param=[])
    mode, algo = resolve_sec_algo(p, ns)
    assert mode == "builtin" and algo(bytes.fromhex("00FF"), 1) == bytes.fromhex("FF00")
    # 4d: py 脚本形态 (临时脚本)
    import tempfile, os
    tmp = os.path.join(tempfile.gettempdir(), "_pydiag_sec_test.py")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("def seed_key(seed, level):\n"
                "    return bytes(reversed(seed))\n")
    fn = load_py_algo(tmp)
    assert fn(bytes.fromhex("11223344"), 1) == bytes.fromhex("44332211")
    os.remove(tmp)
    # 4e: parse_kv_params 数值转换
    pp = parse_kv_params(["key=AB", "const=0x10", "shift=4"])
    assert pp["const"] == 16 and pp["shift"] == 4 and pp["key"] == "AB"
    print("[5] 安全算法形态解析 (fixed_key/builtin/py/参数) OK")
    ok += 1

    # [6] 跨平台抽象层 + 平台化驱动注册
    from . import platform as plat
    assert plat.OS_NAME in ("windows", "linux", "macos")
    assert plat.driver_supported_on_platform("virtual")
    assert plat.driver_supported_on_platform("socketcan") == plat.IS_LINUX
    assert plat.driver_supported_on_platform("zlg") == (plat.OS_NAME in ("windows", "linux"))
    assert plat.driver_supported_on_platform("pcan") == (plat.OS_NAME in ("windows", "macos"))
    assert plat.driver_supported_on_platform("vector") == plat.IS_WINDOWS
    # 设备管理器在任何平台可构建且枚举不崩
    mgr = DeviceManager(register_builtin=True)
    devs = mgr.list_devices()          # 各平台都应安全返回(可为空)
    mgr.close_all()
    # wheel 内 socketcan 驱动类可实例化
    from .devices.socketcan import SocketCanDriver
    sd = SocketCanDriver()
    assert sd.name == "socketcan"
    # vendor 预置库目录存在且可查
    from .vendor import VENDOR_DIR, find_vendor_lib
    assert os.path.isdir(VENDOR_DIR)
    find_vendor_lib("pcan", "windows")   # 不强 assert 文件存在(可被合法剔除), 只验 API
    print(f"[6] 跨平台层 ({plat.OS_NAME}: 驱动注册/枚举/SocketCAN 类) OK")
    ok += 1
    total = 6

    mgr.close_all()
    print(f"=== pydiag 自检 {ok}/{total} 通过 ===")
    return 0 if ok == total else 1


if __name__ == "__main__":
    import sys
    sys.exit(run())
