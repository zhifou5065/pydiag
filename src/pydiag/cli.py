# -*- coding: utf-8 -*-
"""pydiag.cli — `pydiag` 命令行入口。

子命令:
    devices        枚举本机诊断设备
    probe          总线监听（报文统计）
    smoke          烟囱测试: 10 03 → 3E 00 → 22 F1 90
    request        发送任意 UDS 请求 (SID [bytes...])
    security       安全访问解锁 (27)
    diag-round     常用诊断命令完整一轮
    interactive    交互模式
    selftest       内置自检（无需硬件）
"""
from __future__ import annotations

import argparse
import binascii
import sys
import time
from typing import List, Optional

from .devices import DeviceManager, default_manager
from . import zuds
from . import __version__ as _VERSION

# ASCII 安全标记（GBK 控制台友好）
OK, NG = "[OK]", "[NG]"


# ============================================================================
# 设备/会话辅助
# ============================================================================
def _open_session(args) -> tuple:
    """按 --device/--ch/配置文件打开设备并创建 UDS 会话, 返回 (mgr, dev, ch, ses)。

    解析优先级: 命令行显式参数 > 配置文件 > 自动探测。
    自动探测结果写回配置缓存, 下次启动直接命中。
    protocol 分派: docan(CAN) / dolin(LIN) / doip(以太网)。
    """
    from .config import Config
    from .autodetect import autodetect

    cfg = Config.load()
    prof = cfg.profile

    # 协议: 命令行 --protocol > 配置文件 (docan/dolin/doip)
    protocol = getattr(args, "protocol", None) or prof.protocol

    if protocol == "doip":
        return _open_doip(args, cfg, prof)
    if protocol == "dolin":
        return _open_dolin(args, cfg, prof)
    if protocol == "obd":
        return _open_obd(args, cfg, prof)
    return _open_docan(args, cfg, prof, autodetect)


def _open_obd(args, cfg, prof) -> tuple:
    """OBD-II (SAE J1979): 标准 CAN ID 0x7DF(功能) / 0x7E0+i / 0x7E8+i。

    profile 相关字段: device / channel / ecu_index (新增, 0..7)。
    服务号用 OBD 模式号: 01(当前数据) 02(冻结帧) 03(DTC) 09(VIN) 等。
    """
    device_spec = args.device
    ch_req = args.ch
    ecu_index = getattr(args, "ecu_index", 0) or 0
    baud = args.baud if args.baud != 500_000 else prof.baud
    timeout = args.timeout if args.timeout != 2.0 else prof.timeout

    only = None
    if device_spec and not device_spec.lower().startswith("auto"):
        only = device_spec.split(":", 1)[0]
    mgr = default_manager(include_virtual=False, only_drivers=only)
    device_id = _resolve_device(mgr, device_spec)
    ch_idx = ch_req
    if ch_idx is None:
        ch_idx = 0
        if not device_id.startswith("pcan"):
            pass
    dev = mgr.open(device_id)
    if device_id.startswith("pcan"):
        ch_idx = 0                      # PCAN 单通道
    ch = dev.open_channel(ch_idx, bitrate=baud, pad_to8=False)
    ses = zuds.create_obd_session(ch, ecu_index=ecu_index)
    print(f"  (OBD-II: {device_id} ch{ch_idx} ECU{ecu_index} "
          f"req=0x{0x7E0+ecu_index:03X} res=0x{0x7E8+ecu_index:03X} "
          f"func=0x7DF)")
    if args.verbose:
        ses.transport.isotp._log = lambda lvl, msg: print(f"  [TP] {msg}")
    return mgr, dev, ch, ses


def _open_docan(args, cfg, prof, autodetect) -> tuple:
    """DoCAN (ISO 15765-2): CAN 总线诊断 — 原有逻辑。"""
    # 指定了具体驱动（非 auto）时只注册那一家, 避免加载无关驱动 DLL
    device_spec = args.device
    ch_req = args.ch
    req_id = args.req
    res_id = args.res
    # 命令行未显式给值时用配置文件值（argparse default 检测: 与默认值相同视为未给）
    if device_spec == "auto" and prof.device != "auto":
        device_spec = prof.device
    if req_id == 0x721 and prof.req_id != 0x721:
        req_id = prof.req_id
    if res_id == 0x7A1 and prof.res_id != 0x7A1:
        res_id = prof.res_id
    baud = args.baud if args.baud != 500_000 else prof.baud
    pad = not args.no_pad if not args.no_pad else False
    if not args.no_pad and not prof.pad_to8:
        pad = False
    timeout = args.timeout if args.timeout != 2.0 else prof.timeout

    only = None
    if device_spec and not device_spec.lower().startswith("auto"):
        only = device_spec.split(":", 1)[0]
    mgr = default_manager(include_virtual=False, only_drivers=only)

    # 通道: 显式 --ch 优先, 否则尝试自动探测
    ch_idx = ch_req
    device_id = _resolve_device(mgr, device_spec)
    if ch_idx is None:
        prof.device = device_id
        prof.req_id, prof.res_id, prof.baud = req_id, res_id, baud
        hit = autodetect(mgr, prof)
        if hit:
            device_id, ch_idx = hit
            print(f"  (自动探测: {device_id} 通道 {ch_idx})")
            cfg.update_profile(device=device_id, channel=ch_idx)
            cfg.save()
        else:
            ch_idx = 0
            print(f"  (自动探测失败, 回退通道 0)")
    dev = mgr.open(device_id)
    if device_id.startswith("pcan"):
        ch_idx = 0                      # PCAN 单通道
    ch = dev.open_channel(ch_idx, bitrate=baud, pad_to8=pad)
    ses = zuds.create_docan_session(ch, tx_can_id=req_id, rx_can_id=res_id)
    if args.verbose:
        ses.transport.isotp._log = lambda lvl, msg: print(f"  [TP] {msg}")
    return mgr, dev, ch, ses


def _open_dolin(args, cfg, prof) -> tuple:
    """DoLIN (ISO 17987-2): LIN 总线诊断, MRF=0x3C / SRF=0x3D。

    profile 相关字段: device(应为虚拟或支持 LIN 的设备) / channel / nad / baud。
    实硬件 LIN 适配(专用 LIN 盒)接入后, device 直接指向该设备即可。
    """
    from .devices.lin import DualBusVirtualDevice, BusType

    device_spec = args.device
    ch_req = args.ch
    nad = args.nad if args.nad != 0x01 else prof.nad
    baud = args.baud if args.baud != 500_000 else (19200 if prof.baud == 500_000 else prof.baud)
    timeout = args.timeout if args.timeout != 2.0 else prof.timeout

    # LIN 目前仅虚拟设备可跑 (virtual:0 = DualBusVirtualDevice, ch0=CAN ch1+=LIN)
    if device_spec.startswith("virtual") or device_spec == "auto":
        dev = DualBusVirtualDevice(n_can=1, n_lin=1)
        device_id = "virtual:lin"
        ch_idx = ch_req if ch_req is not None else 1     # 默认 LIN 通道 = 1
        ch = dev.open_channel(ch_idx, bitrate=baud, bus_type=BusType.LIN)
        ses = zuds.create_dolin_session(ch, nad=nad)
        print(f"  (DoLIN 虚拟: ch{ch_idx} NAD=0x{nad:02X} {baud} 波特率)")
        if args.verbose:
            ses.transport._log = lambda lvl, msg: print(f"  [LIN-TP] {msg}")
        return mgr_unused(dev), dev, ch, ses

    # 实硬件路径: 设备需支持 bus_type="lin" 的 open_channel
    only = device_spec.split(":", 1)[0]
    mgr = default_manager(include_virtual=False, only_drivers=only)
    device_id = _resolve_device(mgr, device_spec)
    dev = mgr.open(device_id)
    ch_idx = ch_req if ch_req is not None else (prof.channel or 0)
    ch = dev.open_channel(ch_idx, bitrate=baud, bus_type="lin")
    ses = zuds.create_dolin_session(ch, nad=nad)
    print(f"  (DoLIN: {device_id} ch{ch_idx} NAD=0x{nad:02X} {baud} 波特率)")
    if args.verbose:
        ses.transport._log = lambda lvl, msg: print(f"  [LIN-TP] {msg}")
    return mgr, dev, ch, ses


def mgr_unused(dev):
    """虚拟设备无 DeviceManager; 返回带 close_all 的哑管理器。"""
    class _Dummy:
        def close_all(self):
            dev.close()
    return _Dummy()


def _open_doip(args, cfg, prof) -> tuple:
    """DoIP (ISO 13400): 车载以太网诊断, TCP 直连网关。

    profile 相关字段: host / port / source_addr / target_addr。
    无 CAN 设备参与 — mgr 为空 DeviceManager, dev/ch 为 None。
    """
    host = args.host if args.host != "192.168.0.1" else prof.host
    port = args.port if args.port != 13400 else prof.port
    source_addr = args.source_addr if args.source_addr != 0x0E00 else prof.source_addr
    target_addr = args.target_addr if args.target_addr != 0x0001 else prof.target_addr
    timeout = args.timeout if args.timeout != 2.0 else prof.timeout

    ses = zuds.create_doip_session(host=host, port=port,
                                   source_addr=source_addr,
                                   target_addr=target_addr)
    print(f"  (DoIP: {host}:{port} SA=0x{source_addr:04X} TA=0x{target_addr:04X})")
    return _NoopMgr(), None, _NoopCh(), ses


class _NoopMgr:
    """DoIP 无 CAN 设备 — 哑管理器, 兼容 cmd 收尾的 close_all()/close() 调用。"""
    def close_all(self): pass
    def close(self, *a, **k): pass


class _NoopCh:
    def close(self): pass


def _resolve_device(mgr: DeviceManager, spec: str) -> str:
    """把 --device 参数解析成实际 device_id。

    支持: "auto" / "<drv>:auto" (该厂商内自动选第一台) / 具体设备 id。
    """
    if spec and ":" in spec and not spec.split(":", 1)[1].lower() == "auto":
        return spec                     # 具体设备 id
    # auto 或 <drv>:auto → 枚举选第一台可用
    devs = mgr.list_devices()
    drv = spec.split(":", 1)[0] if spec and ":" in spec else None
    if drv and drv.lower() != "auto":
        devs = [d for d in devs if d.driver == drv.lower()]
    free = [d for d in devs if "busy" not in str(d.extra.get("status", ""))]
    if not free:
        raise SystemExit(f"未找到可用诊断设备（检查驱动安装与接线）。"
                         f" 枚举结果: {[str(d) for d in devs]}")
    return free[0].device_id


_NRC_NAMES = {
    0x00: "positiveResponse(异常, 应为POS)",
    0x10: "generalReject 一般拒绝",
    0x11: "serviceNotSupported 服务不支持",
    0x12: "subFunctionNotSupported 子功能不支持",
    0x13: "incorrectMessageLengthOrInvalidFormat 长度或格式错误",
    0x14: "responseTooLong 响应过长",
    0x21: "busyRepeatRequest 忙, 请重发",
    0x22: "conditionsNotCorrect 执行条件不满足",
    0x24: "requestSequenceError 请求顺序错误",
    0x25: "noResponseFromSubnetComponent 子网组件无响应",
    0x26: "failurePreventsExecutionOfRequestedAction 故障阻止执行",
    0x31: "requestOutOfRange 请求值越界(DID/参数不存在)",
    0x33: "securityAccessDenied 未解锁/安全拒绝",
    0x35: "invalidKey key 错误",
    0x36: "exceedNumberOfAttempts 超尝试次数, 已锁定",
    0x37: "requiredTimeDelayNotExpired 防爆破延时未到期",
    0x38: "uploadedDownloadedNotAccepted 上下载不被接受",
    0x39: "transferDataSuspended 传输被挂起",
    0x40: "generalProgrammingFailure 编程通用失败",
    0x41: "wrongBlockSequenceCounter 块序号错误",
    0x43: "subFunctionNotSupportedInActiveSession 本会话不支持该子功能",
    0x51: "controlDTCSettingOff DTC 记录已关闭, 写不进去",
    0x52: "authenticationFailed 认证失败",
    0x53: "authenticationRequired 需要认证",
    0x54: "authenticationTimeout 认证超时",
    0x55: "preConditionsNotCorrect 前置条件不满足",
    0x56: "requestedNumberOfAttemptsExceeded 认证尝试次数超限",
    0x57: "assetNotAvailable 资产不可用",
    0x71: "transferDataSuspension 传输挂起",
    0x72: "generalProgrammingFailure 编程失败",
    0x73: "wrongBlockSequenceCounter 块序列计数错误",
    0x78: "requestCorrectlyReceived-ResponsePending 请求已收到-响应挂起(继续等)",
    0x7E: "subFunctionNotSupportedInActiveSession 本会话不支持该子功能",
    0x7F: "serviceNotSupportedInActiveSession 本会话不支持该服务(需先 10 03)",
    0x81: "rpmTooHigh 转速过高",
    0x82: "rpmTooLow 转速过低",
    0x83: "engineIsRunning 发动机运转中",
    0x84: "engineIsNotRunning 发动机未运转",
    0x85: "engineRunTimeTooLow 发动机运行时间过短",
    0x86: "temperatureTooHigh 温度过高",
    0x87: "temperatureTooLow 温度过低",
    0x88: "vehicleSpeedTooHigh 车速过高",
    0x89: "vehicleSpeedTooLow 车速过低",
    0x8A: "throttlePedalTooHigh 油门开度过大",
    0x8B: "throttlePedalTooLow 油门开度过小",
    0x8C: "transmissionRangeNotInNeutral 变速箱不在空挡",
    0x8D: "transmissionRangeNotInGear 变速箱未挂挡",
    0x8F: "brakeSwitchNotClosed 制动开关未闭合",
    0x90: "shadowingFaultExists 被其它故障屏蔽",
    0x91: "torqueDemandTooHigh 扭矩需求过高",
    0x92: "torqueDemandTooLow 扭矩需求过低",
    0x93: "voltageTooHigh 电压过高",
    0x94: "voltageTooLow 电压过低",
    0x95: "resourceTemporarilyNotAvailable 资源暂时不可用",
}


def _nrc_desc(nrc: int) -> str:
    """ISO 14229-1 负响应码文字描述。"""
    return _NRC_NAMES.get(nrc, "未知/厂商自定义 NRC")


def _show(tag: str, r, note: str = "") -> None:
    if r.code == zuds.ResponseCode.POSITIVE:
        print(f"  {OK} {tag}: POS {r.data.hex(' ').upper()[:64]} {note}")
    elif r.code == zuds.ResponseCode.NEGATIVE:
        print(f"  [NEG] {tag}: NRC 0x{r.nrc:02X} {_nrc_desc(r.nrc)} {note}")
    else:
        reason = {
            zuds.ResponseCode.TIMEOUT: "超时(ECU 无响应: 检查接线/ID/波特率)",
            zuds.ResponseCode.SEND_FAIL: "发送失败(总线错误/设备异常)",
            zuds.ResponseCode.CANCEL: "已取消",
        }.get(r.code, "")
        tail = f" {note}" if note else ""
        print(f"  {NG} {tag}: code={r.code} {reason}{tail}".rstrip())


def _retry(ses, sid, data, timeout: float, tries: int = 3):
    """ECU 已知抖动的温和重试。"""
    import time as _t
    for _ in range(tries):
        r = ses.request(sid, data, timeout_s=timeout)
        if r.code in (zuds.ResponseCode.POSITIVE, zuds.ResponseCode.NEGATIVE):
            return r
        _t.sleep(0.5)
    return r


def _hex_list(vals: List[str]) -> bytes:
    try:
        return bytes(int(v, 16) for v in vals)
    except ValueError:
        raise SystemExit(f"字节参数需为十六进制: {vals}")


# ============================================================================
# 子命令
# ============================================================================
def cmd_devices(args) -> int:
    only = None
    if args.device and not args.device.lower().startswith("auto"):
        only = args.device.split(":", 1)[0]
    mgr = default_manager(include_virtual=args.virtual, only_drivers=only)
    print("本机诊断设备:")
    devs = mgr.list_devices()
    if not devs:
        print("  (无)")
        return 1
    for d in devs:
        status = d.extra.get("status", "")
        mark = "" if status in ("", "free") else f"  status={status}"
        print(f"  {d}  ch={d.channels} serial={d.serial or '-'}{mark}")
    return 0


def cmd_probe(args) -> int:
    only = None
    if args.device and not args.device.lower().startswith("auto"):
        only = args.device.split(":", 1)[0]
    mgr = default_manager(only_drivers=only)
    device_id = _resolve_device(mgr, args.device)
    dev = mgr.open(device_id)
    ch_idx = 0 if device_id.startswith("pcan") else args.ch
    ch = dev.open_channel(ch_idx, bitrate=args.baud, pad_to8=not args.no_pad)
    print(f"监听 {device_id} ch{ch_idx} @{args.baud} 波特率, {args.seconds} 秒…")
    seen: dict = {}
    q = ch.subscribe(0x7A1)             # 只为让 _dispatch 计数; 统计走 rx_count? 直接轮询订阅所有不可能——
    # 用通道层统计 + 通配订阅（subscribe 用 ID 精确匹配, 这里挂诊断应答+常见功能地址）
    for extra_id in (0x721, 0x7A1, 0x7E0, 0x7E8):
        ch.subscribe(extra_id)
    t0 = time.time()
    while time.time() - t0 < args.seconds:
        time.sleep(0.05)
    print(f"统计: tx={ch.tx_count} rx={ch.rx_count}")
    ch.close(); mgr.close_all()
    return 0


def cmd_smoke(args) -> int:
    mgr, dev, ch, ses = _open_session(args)
    print(f"设备 {dev.info} | {ses}")
    r = _retry(ses, 0x10, bytes([0x03]), args.timeout)
    _show("10 03 扩展会话", r)
    if r.code != zuds.ResponseCode.POSITIVE:
        ch.close(); mgr.close_all()
        return 1
    r = _retry(ses, 0x3E, bytes([0x00]), args.timeout)
    _show("3E 00 TesterPresent", r)
    r = _retry(ses, 0x22, bytes([0xF1, 0x90]), args.timeout)
    if r.code == zuds.ResponseCode.POSITIVE:
        _show("22 F1 90 读 VIN", r, f"= {r.data[2:].decode('ascii', 'replace')}")
    else:
        _show("22 F1 90 读 VIN", r)
    ch.close(); mgr.close_all()
    return 0


def cmd_request(args) -> int:
    data = _hex_list(args.data)
    sid, payload = data[0], bytes(data[1:])
    mgr, dev, ch, ses = _open_session(args)
    r = _retry(ses, sid, payload, args.timeout, tries=args.retry)
    _show(f"{sid:02X} {payload.hex(' ').upper()}".strip(), r)
    if args.decode_vin and r.code == zuds.ResponseCode.POSITIVE and sid == 0x22:
        print(f"  文本: {r.data[2:].decode('ascii', 'replace')}")
    ch.close(); mgr.close_all()
    return 0 if r.code == zuds.ResponseCode.POSITIVE else 1


def cmd_security(args) -> int:
    from .zuds_security import SecurityAccess, level_pair
    from .sec_algo import resolve_sec_algo
    from .config import Config
    mgr, dev, ch, ses = _open_session(args)

    try:
        mode, algo = resolve_sec_algo(Config.load().profile, args)
    except Exception as e:
        print(f"  {NG} 算法解析失败: {e}")
        ch.close(); mgr.close_all()
        return 1
    print(f"  安全算法形态: {mode}")

    # 先进扩展会话（27 在默认会话会被 7F 拒绝）
    r = _retry(ses, 0x10, bytes([0x03]), args.timeout)
    _show("10 03 扩展会话", r)
    if r.code != zuds.ResponseCode.POSITIVE:
        ch.close(); mgr.close_all()
        return 1

    sec = SecurityAccess(ses)
    try:
        # --level 语义: 允许填奇数(seed 级 1)或偶数(key 级 2)——都归一到 seed 级
        lvl = int(args.level)
        if lvl % 2 == 0:
            print(f"  (输入 --level {lvl} 为 key 级, 已换算为 seed 级 {lvl - 1})")
            lvl -= 1
        seed = sec.request_seed(lvl)
        print(f"  27 {lvl:02X} seed = {seed.hex(' ').upper()} ({len(seed)}B)")
        if not any(seed):
            print(f"  {OK} seed 全零 → 已解锁, 无需 sendKey")
            ch.close(); mgr.close_all()
            return 0
        if mode == "fixed_key":
            key = algo
        else:
            key = algo(seed, lvl)
            print(f"  27 {lvl:02X} key  = {key.hex(' ').upper()}"
                  f"  (由 {mode} 算法从 seed 算出)")
        send_lvl = lvl + 1
        r = ses.request(0x27, bytes([send_lvl]) + key, timeout_s=args.timeout)
        if mode == "fixed_key":
            print(f"  27 {send_lvl:02X} key  = {key.hex(' ').upper()} (固定密钥)")
        if r.code == zuds.ResponseCode.POSITIVE:
            print(f"  {OK} 解锁成功")
            # 验证: 再取 seed 应全零
            r2 = ses.request(0x27, bytes([lvl]), timeout_s=args.timeout)
            if r2.code == zuds.ResponseCode.POSITIVE and not any(r2.data[1:]):
                print(f"  {OK} 解锁确认: seed 全零")
            return 0
        if r.code == zuds.ResponseCode.NEGATIVE:
            nrc = r.nrc
            why = {0x35: "key 错误(检查算法/密钥)",
                   0x36: "超尝试次数, ECU 已锁定, 勿再重试",
                   0x37: "防爆破延时未到期, 稍后再试"}.get(nrc)
            extra = f" ({why})" if why else ""
            print(f"  {NG} 解锁失败: NRC 0x{nrc:02X} {_nrc_desc(nrc)}{extra}")
            return 1
        print(f"  {NG} 解锁失败: code={r.code}")
        return 1
    except Exception as e:
        print(f"  {NG} 安全访问异常: {e}")
        return 1
    finally:
        ch.close(); mgr.close_all()


def cmd_diag_round(args) -> int:
    """常用诊断命令完整一轮（会话/读数据/DTC/控制/安全访问）。"""
    from .zuds_security import SecurityAccess
    mgr, dev, ch, ses = _open_session(args)
    print(f"设备 {dev.info}\n")
    passed, failed = [], []

    def check(tag, r, note=""):
        if r.code == zuds.ResponseCode.POSITIVE:
            passed.append(tag)
        else:
            failed.append(tag)
        _show(tag, r, note)

    print("[1] 会话管理")
    check("3E 00", _retry(ses, 0x3E, bytes([0x00]), args.timeout))
    check("10 03", _retry(ses, 0x10, bytes([0x03]), args.timeout))
    check("3E 00(扩展)", _retry(ses, 0x3E, bytes([0x00]), args.timeout))

    print("[2] 读数据")
    r = _retry(ses, 0x22, bytes([0xF1, 0x90]), args.timeout)
    check("22 F1 90 VIN", r,
          f"= {r.data[2:].decode('ascii', 'replace')}" if r.code == zuds.ResponseCode.POSITIVE else "")
    check("22 F1 86 活动会话", _retry(ses, 0x22, bytes([0xF1, 0x86]), args.timeout))

    print("[3] DTC")
    r = _retry(ses, 0x19, bytes([0x01, 0xFF]), args.timeout)
    note = ""
    if r.code == zuds.ResponseCode.POSITIVE and len(r.data) >= 4:
        note = f"= {(r.data[2] << 8) | r.data[3]} 个 DTC"
    check("19 01 FF 总数", r, note)
    check("19 02 09 明细", _retry(ses, 0x19, bytes([0x02, 0x09]), args.timeout))

    print("[4] 控制类")
    check("85 02 DTC setting on", _retry(ses, 0x85, bytes([0x02]), args.timeout))
    check("85 01 DTC setting off", _retry(ses, 0x85, bytes([0x01]), args.timeout))
    check("28 03 03 关收发", _retry(ses, 0x28, bytes([0x03, 0x03]), args.timeout))
    # 部分设备仅支持 28 03 03; 恢复通过回默认会话隐式完成
    check("10 01 隐式恢复", _retry(ses, 0x10, bytes([0x01]), args.timeout))
    check("10 03 确认恢复", _retry(ses, 0x10, bytes([0x03]), args.timeout))

    print("[5] 安全访问")
    sec = SecurityAccess(ses)
    try:
        seed = sec.request_seed(1)
        print(f"  27 01 seed = {seed.hex(' ').upper()}")
        if not any(seed):
            print(f"  {OK} 已解锁（seed 全零）")
            passed.append("27 已解锁")
        else:
            r = _retry(ses, 0x27, bytes([0x02]) + bytes.fromhex(args.key), args.timeout)
            check("27 02 sendKey", r)
    except Exception as e:
        print(f"  {NG} {e}")
        failed.append("27")

    print(f"\n结果: {len(passed)} PASS, {len(failed)} 未通过")
    if failed:
        print("未通过:", "; ".join(failed))
    print(f"通道统计: tx={ch.tx_count} rx={ch.rx_count}")
    ch.close(); mgr.close_all()
    return 0 if not failed else 1


def cmd_interactive(args) -> int:
    from .zuds import TesterPresentThread
    mgr, dev, ch, ses = _open_session(args)
    print("交互模式: 输入 'SID DD DD..'（如 `10 03`、`22 F1 90`），q 退出。")
    print("        扩展会话内自动 3E 00 心跳(S3 保活), 手动输入慢不掉会话。")
    tp = TesterPresentThread(ses, interval_s=2.0, suppress=True,
                             functional=False)
    tp_active = False
    while True:
        try:
            line = input("pydiag> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line.lower() in ("q", "quit", "exit"):
            break
        if not line:
            continue
        # 内置快捷命令: :unlock 一步解锁, :session N 切会话
        if line.startswith(":"):
            cmd, *rest = line.split()
            if cmd == ":unlock":
                key = rest[0] if rest else None
                _interactive_unlock(ses, key, args.timeout, args_ns=args)
                continue
            if cmd == ":session":
                lvl = int(rest[0], 16) if rest else 3
                r = _retry(ses, 0x10, bytes([lvl]), args.timeout)
                _show(f"10 {lvl:02X}", r)
                # 会话切换后按需启停心跳 (默认会话无需保活, 但也无害)
                if lvl >= 2 and not tp_active:
                    tp.start(); tp_active = True
                continue
            if cmd == ":log":
                from .logutil import switch_level, tail_log
                if rest and rest[0].lower() in ("debug", "info", "warning", "error"):
                    switch_level(rest[0])
                    print(f"  日志级别 → {rest[0].upper()} "
                          f"(帧级收发 DEBUG 需 :log debug)")
                else:
                    print(tail_log(20))
                continue
            print(f"  未知命令 {cmd} (可用: :unlock [key] / :session <hex> / "
                  f":log [debug|info|error])")
            continue
        # 交互模式支持 --algo/--dll/--py-file/--builtin 参数, :unlock 自动带上
        parts = line.split()
        try:
            data = _hex_list(parts)
        except SystemExit as e:
            print(e)
            continue
        sid, payload = data[0], bytes(data[1:])
        # 10 03/10 02 等扩展类会话切换后自动启心跳
        if sid == 0x10 and len(payload) >= 1 and payload[0] >= 2 and not tp_active:
            tp.start(); tp_active = True
        r = _retry(ses, sid, payload, args.timeout)
        _show(f"{sid:02X}", r)
        if sid == 0x22 and r.code == zuds.ResponseCode.POSITIVE:
            print(f"  文本: {r.data[2:].decode('ascii', 'replace')}")
    if tp_active:
        tp.stop()
    ch.close(); mgr.close_all()
    return 0


def _interactive_unlock(ses, key_hex: Optional[str], timeout: float,
                        args_ns=None) -> None:
    """:unlock 快捷命令: 进扩展会话 + 27 解锁（支持四种算法形态）。"""
    from .config import Config
    from .sec_algo import resolve_sec_algo
    r = _retry(ses, 0x10, bytes([0x03]), timeout)
    _show("10 03 扩展会话", r)
    if r.code != zuds.ResponseCode.POSITIVE:
        return
    r = _retry(ses, 0x27, bytes([0x01]), timeout)
    _show("27 01 requestSeed", r)
    if r.code != zuds.ResponseCode.POSITIVE:
        return
    seed = r.data[1:]
    if not any(seed):
        print(f"  {OK} seed 全零 → 已解锁")
        return
    try:
        mode, algo = resolve_sec_algo(Config.load().profile, args_ns)
    except Exception as e:
        print(f"  {NG} 算法解析失败: {e}")
        return
    try:
        if mode == "fixed_key":
            key = algo
            print(f"  (固定密钥形态: {key.hex(' ').upper()})")
        else:
            key = bytes(algo(seed, 1))
            print(f"  (key 由 {mode} 算法算出: {key.hex(' ').upper()})")
    except Exception as e:
        print(f"  {NG} 算法计算失败: {e}")
        return
    r = _retry(ses, 0x27, bytes([0x02]) + key, timeout)
    _show("27 02 sendKey", r)
    if r.code == zuds.ResponseCode.POSITIVE:
        print(f"  {OK} 解锁成功")


def cmd_selftest(args) -> int:
    from . import selftest as st
    return st.run(verbose=args.verbose)


# ============================================================================
# config 子命令 — 查看/设置持久化诊断配置
# ============================================================================
def cmd_config(args) -> int:
    from .config import Config
    cfg = Config.load()

    if args.show:
        from .config import CONFIG_PATH
        print(f"配置文件: {CONFIG_PATH}")
        print(f"活动 profile: {cfg.active}")
        for name, p in cfg.profiles.items():
            mark = " ← 当前" if name == cfg.active else ""
            proto = p.get("protocol", "docan")
            print(f"\n[{name}]{mark}  ({proto})")
            # 按 protocol 分组显示相关字段
            common_keys = ("name", "protocol", "device", "channel", "baud",
                           "timeout", "note")
            proto_keys = {"docan": ("req_id", "res_id", "pad_to8"),
                          "dolin": ("nad",),
                          "doip": ("host", "port", "source_addr", "target_addr"),
                         "obd": ("ecu_index",)}
            # 安全访问算法形态字段
            sec_keys = ("key", "sec_mode", "sec_builtin",
                        "sec_builtin_params", "sec_dll", "sec_dll_func",
                        "sec_dll_conv", "sec_py")
            keys = common_keys + proto_keys.get(proto, ()) + sec_keys
            for k in keys:
                if k not in p:
                    continue
                v = p[k]
                if k in ("req_id", "res_id", "source_addr", "target_addr",
                         "nad") and isinstance(v, int):
                    v = f"0x{v:X}"
                if k == "channel" and v is None:
                    v = "自动探测"
                print(f"  {k:12} = {v}")
        return 0

    if args.use:
        cfg.set_active(args.use)
        cfg.save()
        print(f"活动 profile → {cfg.active}")
        return 0

    if args.set:
        updates = {}
        for kv in args.set:
            if "=" not in kv:
                print(f"格式错误: {kv}（应为 key=value）")
                return 1
            k, v = kv.split("=", 1)
            k = k.strip()
            if k == "protocol" and v.lower() in ("docan", "dolin", "doip", "obd"):
                updates[k] = v.lower()
                continue
            # 类型转换
            if k in ("channel", "req_id", "res_id", "baud", "nad",
                     "port", "source_addr", "target_addr"):
                v = int(v, 0) if k != "channel" else (None if v.lower() in ("none", "auto") else int(v, 0))
            elif k in ("timeout",):
                v = float(v)
            elif k in ("pad_to8",):
                v = v.lower() in ("1", "true", "yes", "on")
            elif k == "sec_mode" and v.lower() in ("fixed_key", "builtin",
                                                   "dll", "py"):
                v = v.lower()
            updates[k] = v
        p = cfg.update_profile(**updates)
        cfg.save()
        print(f"已更新 [{cfg.active}]:")
        for k, v in updates.items():
            if k in ("req_id", "res_id", "source_addr", "target_addr",
                     "nad") and isinstance(v, int):
                v = f"0x{v:X}"
            print(f"  {k} = {v}")
        return 0

    if args.reset:
        from .config import EcuProfile, asdict
        cfg.profiles[cfg.active] = asdict(EcuProfile())
        cfg.save()
        print(f"[{cfg.active}] 已重置为默认（通道恢复自动探测）")
        return 0

    # 无参数 = 显示
    args.show = True
    return cmd_config(args)


# ============================================================================
# 参数解析
# ============================================================================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pydiag",
        description="pydiag — UDS (ISO 14229) 命令行诊断工具")
    p.add_argument("--version", action="version", version=f"pydiag {_VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, ch_default=None):
        sp.add_argument("--protocol", choices=["docan", "dolin", "doip", "obd"],
                        default=None,
                        help="诊断协议: docan=CAN / dolin=LIN / doip=以太网 / obd=OBD-II"
                             "（默认取配置, 再默认 docan）")
        sp.add_argument("--device", default="auto",
                        help="设备: auto / zlg:<serial> / zlg:idxN / pcan:<uid> / pcan:usbN / vector:appN")
        sp.add_argument("--req", type=lambda x: int(x, 0), default=0x721,
                        help="[docan] 诊断请求 CAN ID (默认 0x721)")
        sp.add_argument("--res", type=lambda x: int(x, 0), default=0x7A1,
                        help="[docan] 诊断应答 CAN ID (默认 0x7A1)")
        sp.add_argument("--nad", type=lambda x: int(x, 0), default=0x01,
                        help="[dolin] LIN 节点地址 NAD (默认 0x01)")
        sp.add_argument("--host", default="192.168.0.1",
                        help="[doip] DoIP 网关 IP")
        sp.add_argument("--port", type=int, default=13400,
                        help="[doip] DoIP 端口 (默认 13400)")
        sp.add_argument("--source-addr", type=lambda x: int(x, 0), default=0x0E00,
                        help="[doip] 诊断仪逻辑地址 SA")
        sp.add_argument("--target-addr", type=lambda x: int(x, 0), default=0x0001,
                        help="[doip] ECU 逻辑地址 TA")
        sp.add_argument("--ecu-index", type=int, default=0,
                        help="[obd] ECU 索引 0..7 (req=0x7E0+i, res=0x7E8+i, 默认 0)")
        sp.add_argument("--ch", type=int, default=ch_default,
                        help="物理通道索引 (缺省 = 配置值, 否则自动探测)")
        sp.add_argument("--baud", type=int, default=500_000)
        sp.add_argument("--no-pad", action="store_true",
                        help="关闭 DLC=8 补 0xAA（默认开启）")
        sp.add_argument("--timeout", type=float, default=2.0)
        sp.add_argument("-v", "--verbose", action="store_true")

    sp_dev = sub.add_parser("devices", help="枚举本机诊断设备")
    sp_dev.add_argument("--virtual", action="store_true", help="包含虚拟设备")
    sp_dev.add_argument("--device", default="auto",
                        help="只枚举指定驱动: zlg / pcan / vector（默认全部）")
    sp_dev.set_defaults(func=cmd_devices)

    sp = sub.add_parser("probe", help="总线监听")
    common(sp); sp.add_argument("--seconds", type=float, default=3.0)
    sp.set_defaults(func=cmd_probe)

    sp = sub.add_parser("smoke", help="烟囱测试 10 03 → 3E 00 → 22 F1 90")
    common(sp); sp.set_defaults(func=cmd_smoke)

    sp = sub.add_parser("request", help="发送 UDS 请求: request 22 F1 90")
    common(sp)
    sp.add_argument("data", nargs="+", help="十六进制字节, 首字节为 SID")
    sp.add_argument("--retry", type=int, default=3)
    sp.add_argument("--decode-vin", action="store_true")
    sp.set_defaults(func=cmd_request)

    sp = sub.add_parser("security", help="安全访问解锁 (27)")
    common(sp)
    sp.add_argument("--level", type=int, default=1, help="奇数 seed 级别")
    sp.add_argument("--key", default="00010203",
                    help="[fixed_key 形态] 固定密钥十六进制（也兼容旧用法）")
    sp.add_argument("--algo", choices=["fixed_key", "py", "dll", "builtin"],
                    default=None,
                    help="算法形态: fixed_key=固定密钥 / py=Python 脚本 / "
                         "dll=OEM DLL / builtin=内置算法（默认取配置 sec_mode）")
    sp.add_argument("--py-file", metavar="SCRIPT.py",
                    help="[py 形态] 算法脚本, 需含 def seed_key(seed, level)")
    sp.add_argument("--dll", metavar="SEC.dll",
                    help="[dll 形态] OEM 算法 DLL 路径")
    sp.add_argument("--dll-func", default="SecAccess",
                    help="[dll 形态] 导出函数名（默认 SecAccess）")
    sp.add_argument("--dll-conv", choices=["A", "B", "C"], default="A",
                    help="[dll 形态] 调用约定 A/B/C（默认 A）")
    sp.add_argument("--builtin", metavar="NAME",
                    help="[builtin 形态] xor/const_add/invert/swap/"
                         "mask_shift/aes_ecb")
    sp.add_argument("--builtin-param", nargs="*", metavar="NAME=VALUE",
                    help="[builtin 形态] 参数, 如 key=1A2B3C4D const=1")
    sp.set_defaults(func=cmd_security)

    sp = sub.add_parser("diag-round", help="常用诊断一轮全流程")
    common(sp)
    sp.add_argument("--key", default="00010203")
    sp.set_defaults(func=cmd_diag_round)

    sp = sub.add_parser("interactive", help="交互模式")
    common(sp); sp.set_defaults(func=cmd_interactive)

    sp = sub.add_parser("selftest", help="内置自检（无需硬件）")
    sp.add_argument("-v", "--verbose", action="store_true")
    sp.set_defaults(func=cmd_selftest)

    sp = sub.add_parser("config", help="查看/设置持久化诊断配置")
    sp.add_argument("--show", action="store_true", help="显示当前配置")
    sp.add_argument("--use", metavar="NAME", help="切换活动 ECU profile")
    sp.add_argument("--set", nargs="+", metavar="KEY=VALUE",
                    help="设置项: device=... channel=N|auto req_id=0x721 "
                         "res_id=0x7A1 baud=500000 timeout=2.0 pad_to8=true "
                         "key=1A2B3C4D sec_mode=fixed_key|builtin|dll|py "
                         "sec_builtin=xor sec_builtin_params='{\"key\":\"1A2B\"}' "
                         "sec_dll=oem.dll sec_dll_func=SecAccess sec_dll_conv=A "
                         "sec_py=my_sec.py note=...")
    sp.add_argument("--reset", action="store_true", help="重置活动 profile 为默认")
    sp.set_defaults(func=cmd_config)

    # logs — 查看日志
    sp_logs = sub.add_parser("logs", help="查看诊断日志 (定位问题用)")
    sp_logs.add_argument("--tail", type=int, default=50,
                         help="显示最后 N 行 (默认 50)")
    sp_logs.add_argument("--path", action="store_true",
                         help="只打印日志文件路径")
    sp_logs.set_defaults(func=cmd_logs)

    return p


def cmd_logs(args) -> int:
    """查看日志文件（排障入口）。"""
    from .logutil import tail_log, _resolve_actual_log_path
    path = _resolve_actual_log_path() or "(未生成)"
    if args.path:
        print(path)
        return 0
    print(f"日志文件: {path}")
    print("=" * 60)
    print(tail_log(args.tail))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    from .logutil import setup_logging, get_logger, LOG_PATH
    setup_logging()
    log = get_logger("cli")
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n中断")
        return 130
    except RuntimeError as e:
        # 设备/协议层可预期错误 → 简洁提示 + 日志指引
        log.exception("runtime error")
        print(f"\n错误: {e}", file=sys.stderr)
        print(f"详情见日志: {LOG_PATH}", file=sys.stderr)
        return 1
    except OSError as e:
        # 设备打开失败/驱动缺失等
        log.exception("os error")
        print(f"\n设备/系统错误: {e}", file=sys.stderr)
        print(f"详情见日志: {LOG_PATH}", file=sys.stderr)
        return 2
    except Exception as e:
        # 未预期错误 → 完整堆栈进日志, 简洁提示给用户
        log.exception("unexpected error")
        print(f"\n内部错误: {type(e).__name__}: {e}", file=sys.stderr)
        print(f"完整堆栈已记录到日志: {LOG_PATH}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
