# -*- coding: utf-8 -*-
"""sec_algo.py — 从 CLI 参数/配置 profile 解析出 SeedKeyFn 算法。

支持形态（互斥, 优先级从高到低）:
    1. fixed_key  固定密钥   --key 1A2B3C4D           (跳过算法, 直接发 key)
    2. py         Python 脚本 --algo py --py-file my_sec.py
                  脚本约定: 模块级 def seed_key(seed: bytes, level: int) -> bytes
                  脚本可 import 任意第三方库( hashlib / pycryptodome / ... )
    3. dll        OEM DLL    --algo dll --dll oem_sec.dll [--dll-func SecAccess]
                  调用约定 A/B/C 见 zuds_security.DllConvention
    4. builtin    内置算法表 --algo builtin --builtin xor --builtin-param key=1A2B3C4D
                  xor / const_add / invert / swap / mask_shift / aes_ecb

解析入口: resolve_sec_algo(profile, args_ns) -> (mode, algo_or_key)
    mode       ∈ {"fixed_key", "py", "dll", "builtin"}
    algo_or_key 固定 key 形态为 bytes; 其余为 SeedKeyFn 可调用对象
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from types import SimpleNamespace
from typing import Optional, Tuple

from .zuds_security import (
    SeedKeyFn, SecurityError, DllAlgo, DllConvention, builtin,
)

__all__ = ["resolve_sec_algo", "load_py_algo", "parse_kv_params"]

VALID_MODES = ("fixed_key", "py", "dll", "builtin")


def parse_kv_params(pairs) -> dict:
    """["key=1A2B3C4D", "shift=4"] -> {"key": "1A2B3C4D", "shift": "4"}。
    数值型参数自动转 int（const/mask/shift）。"""
    params: dict = {}
    for kv in pairs or ():
        if "=" not in kv:
            raise SecurityError(f"参数格式错误: {kv}（应为 name=value）")
        k, v = kv.split("=", 1)
        params[k.strip()] = v.strip()
    # 数值型参数转 int
    for k in ("const", "mask", "shift"):
        if k in params:
            try:
                params[k] = int(params[k], 0)
            except ValueError:
                pass
    return params


def load_py_algo(py_path: str) -> SeedKeyFn:
    """从 .py 文件加载 seed_key(seed, level) 函数。

    脚本示例:
        # my_sec.py
        def seed_key(seed: bytes, level: int) -> bytes:
            k = [0x1A, 0x2B, 0x3C, 0x4D]
            return bytes(s ^ k[i % 4] for i, s in enumerate(seed))
    """
    path = os.path.abspath(py_path)
    if not os.path.isfile(path):
        raise SecurityError(f"Python 算法脚本不存在: {path}")
    mod_name = "_pydiag_user_sec_" + os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise SecurityError(f"无法加载脚本: {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod          # 让脚本可被 dataclasses 等内省
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        raise SecurityError(f"脚本执行失败: {path} ({e})") from e
    fn = getattr(mod, "seed_key", None)
    if fn is None or not callable(fn):
        raise SecurityError(
            f"脚本 {path} 缺少 def seed_key(seed: bytes, level: int) -> bytes")
    return fn


def resolve_sec_algo(profile, args_ns=None) -> Tuple[str, object]:
    """合并优先级: 命令行 args > profile 配置。

    profile:  EcuProfile (或 SimpleNamespace), 读 sec_mode/sec_* 字段
    args_ns:  argparse Namespace, 读 algo/key/builtin/builtin_param/
              dll/dll_func/dll_conv/py_file（属性缺失视为未指定）

    返回 (mode, algo_or_key):
        fixed_key → bytes   (ECU 固定密钥, 无需算法)
        其它      → SeedKeyFn
    """
    a = args_ns or SimpleNamespace()
    g = lambda name, default=None: getattr(a, name, default)

    # ---- 命令行显式指定（最高优先级） ----
    cli_algo = g("algo")
    cli_key = g("key")

    if cli_key and cli_key not in ("00010203",) and not cli_algo:
        # 有 --key 且非占位值、未指明 --algo → 兼容旧行为: 固定 key
        return "fixed_key", bytes.fromhex(cli_key)

    mode = (cli_algo or getattr(profile, "sec_mode", "") or "fixed_key").lower()
    if mode not in VALID_MODES:
        raise SecurityError(
            f"未知安全算法形态 {mode!r}（可用: {', '.join(VALID_MODES)}）")

    if mode == "fixed_key":
        # 占位值 "00010203" 视为未指定, 回退 profile.key
        key_hex = cli_key if cli_key and cli_key != "00010203" else (
            getattr(profile, "key", "") or "00010203")
        return "fixed_key", bytes.fromhex(key_hex)

    if mode == "builtin":
        name = g("builtin") or getattr(profile, "sec_builtin", "")
        if not name:
            raise SecurityError("builtin 形态需要 --builtin <name> "
                                f"（可用: xor/const_add/invert/swap/mask_shift/aes_ecb）")
        raw_params = list(g("builtin_param") or [])
        if not raw_params:
            jp = getattr(profile, "sec_builtin_params", "")
            if jp:
                try:
                    raw_params = [f"{k}={v}" for k, v in
                                  json.loads(jp).items()]
                except json.JSONDecodeError as e:
                    raise SecurityError(
                        f"profile sec_builtin_params JSON 错误: {e}") from e
        return "builtin", builtin(name, **parse_kv_params(raw_params))

    if mode == "dll":
        dll_path = g("dll") or getattr(profile, "sec_dll", "")
        if not dll_path:
            raise SecurityError("dll 形态需要 --dll <path>")
        if not os.path.isfile(dll_path):
            raise SecurityError(f"DLL 文件不存在: {os.path.abspath(dll_path)}")
        return "dll", DllAlgo(
            dll_path,
            func_name=g("dll_func") or getattr(profile, "sec_dll_func", "SecAccess"),
            convention=g("dll_conv") or getattr(profile, "sec_dll_conv", "A"),
        )

    if mode == "py":
        py_path = g("py_file") or getattr(profile, "sec_py", "")
        if not py_path:
            raise SecurityError("py 形态需要 --py-file <script.py>")
        return "py", load_py_algo(py_path)

    raise SecurityError(f"unreachable: mode={mode!r}")
