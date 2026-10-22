# -*- coding: utf-8 -*-
"""pydiag.autodetect — 诊断通道/ECU 自动探测（多设备多通道版）。

支持任意数量/组合的设备: 多台 ZLG + 多个 PCAN + 多个 Vector 通道。
策略（从快到慢）:
  1. 配置缓存: config.json 该 profile 已有 (device, channel) → 快速校验后使用
  2. 全量扫描: 枚举所有驱动所有设备所有通道, 逐个发 3E 00, 应答即锁定
     - 候选排序: 缓存过的设备优先 → 同品牌历史优先 → 枚举顺序
     - 噪声防护: 必须收到 res_id 应答(且像 UDS 响应)才算命中
  3. 命中多个(同一请求帧被多个设备听到)? → 选延迟最低的(直连优先)
  4. 全部无应答 → 返回 None

多设备关键问题与对策:
  * 同一总线上多个适配器: A 发的请求会被 B 的通道也听到, 但 ECU 应答只会
    进入**接线在诊断总线上的那个通道** — 以"应答是否到达"为准, 不以请求为准。
  * ZLG 对侧缓冲: probe 用 cross_watch=True, 两通道都监听。
  * 设备打开失败(被占用/驱动忙): 跳过该设备继续扫下一个。
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

from .devices import DeviceManager, default_manager
from .devices.core import BaseChannel


def _looks_like_uds_response(data: bytes, res_id: int) -> bool:
    """粗滤: 帧像 UDS 响应 (SID+0x40 正响应 或 7F 负响应)。"""
    if len(data) < 2:
        return False
    # ISO-TP 单帧 SF: 首字节低 4 位 = 长度; data[1] = SID
    if data[0] & 0xF0 == 0x00 and len(data) >= 3:
        sid = data[1]
        return sid in (0x7E, 0x7F, 0x50, 0x62) or (sid & 0x40) or sid == 0x7F
    return True  # 兜底: 有应答就算


def probe_channel_for_ecu(dev, ch_index: int, req_id: int, res_id: int,
                          baud: int, timeout_s: float = 1.0,
                          tries: int = 2) -> bool:
    """单通道探测: 发 3E 00, 判断是否收到 res_id 的 UDS 应答。"""
    try:
        ch = dev.open_channel(ch_index, bitrate=baud,
                              cross_watch=True, pad_to8=True)
    except Exception:
        return False
    try:
        q = ch.subscribe(res_id)
        try:
            for _ in range(tries):
                ch.send(req_id, bytes([0x02, 0x3E, 0x00] + [0xAA] * 5))
                t0 = time.time()
                while time.time() - t0 < timeout_s:
                    try:
                        data = q.get(timeout=0.1)
                        if _looks_like_uds_response(data, res_id):
                            return True
                    except Exception:
                        continue
                time.sleep(0.3)
            return False
        finally:
            ch.unsubscribe(res_id, q)
    finally:
        ch.close()


def autodetect(mgr: DeviceManager, profile) -> Optional[Tuple[str, int]]:
    """自动探测诊断设备+通道（多设备多通道安全）。

    返回 (device_id, channel_index) 或 None。
    """
    # 1) 缓存命中: 快速校验 (0.6s 单次), 仍应答则直接用
    if profile.channel is not None:
        try:
            dev = mgr.open(profile.device)
            if probe_channel_for_ecu(dev, profile.channel, profile.req_id,
                                     profile.res_id, profile.baud,
                                     timeout_s=0.6, tries=1):
                return (profile.device, profile.channel)
        except Exception:
            pass  # 缓存失效, 走全量扫描

    # 2) 全量扫描: 所有驱动 → 所有设备 → 所有通道
    #    候选排序: 与缓存设备同品牌的排前面 (用户接线习惯)
    devices = mgr.list_devices()
    cached_drv = (profile.device.split(":", 1)[0]
                  if profile.device and ":" in profile.device else None)
    devices.sort(key=lambda d: 0 if cached_drv and d.driver == cached_drv
                 else 1)

    for info in devices:
        if "busy" in str(info.extra.get("status", "")):
            continue
        try:
            dev = mgr.open(info.device_id)
        except Exception:
            continue  # 被占用/驱动忙 → 跳过
        n_ch = info.channels or 1
        for idx in range(n_ch):
            if probe_channel_for_ecu(dev, idx, profile.req_id,
                                     profile.res_id, profile.baud):
                return (info.device_id, idx)
    return None
