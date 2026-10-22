# -*- coding: utf-8 -*-
"""pydiag.config — 持久化配置管理。

保存/加载诊断配置(设备/通道/ECU ID/总线类型), 供 `uds config` 子命令与自动探测使用。
存储位置: ~/.pydiag/config.json (可用环境变量 PYDIAG_HOME 覆盖)

支持三种总线协议的 profile:
  protocol = "docan"   CAN 诊断 (ISO 15765-2): req_id/res_id + 设备通道
  protocol = "dolin"   LIN 诊断 (ISO 17987-2): nad + MRF/SRF (0x3C/0x3D)
  protocol = "doip"    车载以太网诊断 (ISO 13400): host/port + SA/TA 逻辑地址
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Optional

CONFIG_DIR = os.environ.get("PYDIAG_HOME") or os.path.join(
    os.path.expanduser("~"), ".pydiag")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

VALID_PROTOCOLS = ("docan", "dolin", "doip", "obd")


@dataclass
class EcuProfile:
    """单个 ECU 的诊断参数（按 protocol 取用相关字段）。"""
    name: str = "default"
    protocol: str = "docan"        # docan / dolin / doip / obd

    # ---- 公共 ----
    device: str = "auto"           # docan/dolin: auto / zlg:<serial> / pcan:<uid> / vector:appN
    channel: Optional[int] = None  # docan/dolin: None = 自动探测
    baud: int = 500_000            # docan: 500k; dolin: 19200 (LIN 常用)
    timeout: float = 2.0
    key: str = "00010203"          # 27 服务默认 key (固定 key 形态)
    note: str = ""

    # ---- 安全访问算法形态 (27 服务 seed→key) ----
    # sec_mode: fixed_key / builtin / dll / py
    sec_mode: str = "fixed_key"
    sec_builtin: str = ""          # builtin 形态: xor/const_add/invert/swap/mask_shift/aes_ecb
    sec_builtin_params: str = ""   # builtin 参数 JSON: '{"key":"1A2B3C4D"}'
    sec_dll: str = ""              # dll 形态: DLL 路径
    sec_dll_func: str = "SecAccess"  # dll 导出函数名
    sec_dll_conv: str = "A"        # dll 调用约定 A/B/C
    sec_py: str = ""               # py 形态: 脚本路径 (模块级 seed_key(seed, level) 函数)

    # ---- DoCAN 专用 ----
    req_id: int = 0x721
    res_id: int = 0x7A1
    pad_to8: bool = True

    # ---- DoLIN 专用 ----
    nad: int = 0x01                # LIN 节点地址 (slave NAD)

    # ---- DoIP 专用 ----
    host: str = "192.168.0.1"      # DoIP 网关 IP
    port: int = 13400              # DoIP 端口 (ISO 13400)
    source_addr: int = 0x0E00      # 诊断仪逻辑地址 (SA)
    target_addr: int = 0x0001      # ECU 逻辑地址 (TA)


@dataclass
class Config:
    active: str = "default"
    profiles: dict = field(default_factory=lambda: {"default": asdict(EcuProfile())})

    # ---- IO ----
    @classmethod
    def load(cls) -> "Config":
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg = cls()
            cfg.active = data.get("active", "default")
            cfg.profiles = data.get("profiles", cfg.profiles)
            return cfg
        except (FileNotFoundError, json.JSONDecodeError):
            return cls()

    def save(self) -> None:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"active": self.active, "profiles": self.profiles},
                      f, indent=2, ensure_ascii=False)

    # ---- profile 访问 ----
    @property
    def profile(self) -> EcuProfile:
        p = self.profiles.get(self.active)
        if p is None:
            p = asdict(EcuProfile())
            self.profiles[self.active] = p
        return EcuProfile(**p)

    def update_profile(self, **kwargs) -> EcuProfile:
        p = self.profile
        for k, v in kwargs.items():
            if hasattr(p, k) and v is not None:
                setattr(p, k, v)
        self.profiles[self.active] = asdict(p)
        return p

    def set_active(self, name: str) -> None:
        if name not in self.profiles:
            # 从 default 复制一份新 profile
            base = dict(self.profiles.get("default", asdict(EcuProfile())))
            base["name"] = name
            self.profiles[name] = base
        self.active = name
