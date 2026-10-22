# -*- coding: utf-8 -*-
"""
zuds_security.py — UDS 0x27 SecurityAccess 编排 + seed→key 可插拔算法层
=======================================================================

参考: ISO 14229-1 §11.6；zuds.py 协议栈（UdsSession.request 提供原子请求）。

公开 API（编排层）:
    sec = SecurityAccess(session)                       # 绑定会话
    sec.unlock(1)                                        # 用构造时/后设的 algo
    sec.unlock(1, algo=...)                              # 临时指定算法
    sec.unlock(1, key=b"...")                            # 固定 key 形态（跳过 seed）
    sec.unlock(1, builtin="xor", key="1A2B3C4D")         # 内置算法表
    sec.unlock(1, dll=DllAlgo("oem.dll", "SecAccess"))   # OEM DLL 形态

公开 API（算法层，统一签名 SeedKeyFn = (seed: bytes, level: int) -> bytes）:
    Python 回调      任意可调用对象，零包装
    DllAlgo          OEM DLL（3 种调用约定 + 位数自检）
    builtin("xor", key=...)   注册表: xor/const_add/invert/swap/aes_ecb/mask_shift
    register_builtin(name, factory)   用户自定义内置（可覆盖同名）

安全语义（硬编码，不可配错）:
    * NRC 0x36 超尝试次数 → 立即抛 SecurityLockedError，绝不重试
    * NRC 0x37 防爆破延时 → DelayPolicy.RAISE 抛 / WAIT 等 delay_wait_s 重试一次
    * NRC 0x35 key 错误   → 抛 NegativeResponseError 交上层（自动重试只会烧次数）
    * seed 全零           → ECU 已解锁，跳过 sendKey 直接成功
    * 仅物理寻址（27 功能寻址多 ECU 各回各 seed，key 无法对应）

日志脱敏: 默认 seed/key 打 ****，verbose_security=True 才输出原文。
"""

from __future__ import annotations

import ctypes
import platform
import struct
import threading
import time
from typing import Callable, Dict, Optional, Tuple

try:
    from .zuds import UdsSession, ResponseCode
except ImportError:                     # 直接以脚本方式使用
    from zuds import UdsSession, ResponseCode

__all__ = [
    "SeedKeyFn",
    "SecurityAccess", "SecurityError", "SecurityLockedError",
    "DelayPolicy",
    "DllAlgo", "DllConvention",
    "builtin", "register_builtin", "available_builtins",
    "mask_key", "mask_hex",
]

# ============================================================================
# 类型与异常
# ============================================================================
#: seed→key 算法统一签名: (seed, request_level) -> key
SeedKeyFn = Callable[[bytes, int], bytes]

NRC_INVALID_KEY   = 0x35   # invalidKey
NRC_EXCEED_ATTEMPT = 0x36  # exceedNumberOfAttempts
NRC_REQUIRED_DELAY = 0x37  # requiredTimeDelayNotExpired


class SecurityError(RuntimeError):
    """安全访问流程性错误（意外响应/算法返回空等）。"""


class SecurityLockedError(SecurityError):
    """ECU 已进入防爆破锁定（NRC 0x36），重试只会延长锁定期。"""


def mask_hex(data: bytes, verbose: bool = False) -> str:
    """hex 显示；默认脱敏为 ****（verbose_security 时显示原文）。"""
    if not verbose:
        return "****"
    return data.hex(" ").upper() if data else "(空)"


def mask_key(key: bytes, verbose: bool = False) -> str:
    return mask_hex(key, verbose)


# ============================================================================
# NRC 0x37 策略
# ============================================================================
class DelayPolicy:
    RAISE = "raise"     # 遇 0x37 立即抛（默认）
    WAIT  = "wait"      # 等 delay_wait_s 重试一次（再遇 0x37 仍抛）


# ============================================================================
# 形态 3 — OEM DLL
# ============================================================================
class DllConvention:
    """OEM DLL 常见调用约定。

    A  uint32 fn(uint32 seed, uint32 level)          — ZLG/CANoe 风格，最常见
    B  void   fn(const uint8* seed, int seedLen,
                 uint8* key, int* keyLen, int level) — 数组指针风格
    C  int    fn(const uint8* appKey, int appLen,
                 const uint8* seed, int seedLen,
                 uint8* key, int keyLen, int level)  — IMA/成对数组风格
    """
    A = "A"
    B = "B"
    C = "C"


class DllAlgo:
    """把 OEM 的 win32 DLL 包装成 SeedKeyFn。

    用法:
        algo = DllAlgo("oem_sec.dll")                        # 默认约定 A, 函数名 SecAccess
        algo = DllAlgo("oem_sec.dll", func_name="ComputeKey",
                       convention=DllConvention.B, seed_width=4, key_width=4)
        sec.unlock(1, dll=algo)

    位数自检: 32 位 DLL 必须配 32 位 Python（struct.error/AccessViolation 常见根源）。
    """

    def __init__(self, dll_path: str,
                 func_name: str = "SecAccess",
                 convention: str = DllConvention.A,
                 seed_width: int = 4,
                 key_width: int = 4,
                 call_timeout_guard: bool = True) -> None:
        self.dll_path = str(dll_path)
        self.func_name = str(func_name)
        self.convention = str(convention).upper()
        self.seed_width = int(seed_width)
        self.key_width = int(key_width)
        self._lock = threading.Lock()
        self._lib = None
        self._fn = None
        self._loaded = False
        self._selftest_done = not call_timeout_guard

    # ---- 延迟加载（unlock 时才碰文件系统） ----
    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            is64_py = platform.architecture()[0] == "64bit"
            if is64_py and "32" in platform.architecture()[1]:
                raise SecurityError(
                    f"DLL {self.dll_path}: 位数自检失败（Python 64bit）")
            try:
                self._lib = ctypes.WinDLL(self.dll_path)
            except OSError as e:
                raise SecurityError(
                    f"加载 DLL 失败: {self.dll_path} ({e}); "
                    f"检查路径/依赖/位数(当前 Python {platform.architecture()[0]})") from e
            try:
                self._fn = getattr(self._lib, self.func_name)
            except AttributeError as e:
                raise SecurityError(
                    f"DLL {self.dll_path} 中找不到函数 {self.func_name} "
                    f"(可用: depends 工具确认导出名)") from e
            self._loaded = True

    # ---- 约定 A: uint32 fn(uint32 seed, uint32 level) ----
    def _call_a(self, seed: bytes, level: int) -> bytes:
        w = self.seed_width
        s = int.from_bytes(seed[-w:].rjust(w, b"\x00"), "big")
        self._fn.restype = ctypes.c_uint
        self._fn.argtypes = [ctypes.c_uint, ctypes.c_uint]
        ret = self._fn(s, level & 0xFFFFFFFF)
        return (ret & ((1 << (8 * self.key_width)) - 1)).to_bytes(self.key_width, "big")

    # ---- 约定 B: (seed*, seedLen, key*, &keyLen, level) ----
    def _call_b(self, seed: bytes, level: int) -> bytes:
        sw, kw = self.seed_width, self.key_width
        seed_buf = (ctypes.c_ubyte * sw)(
            *seed[-sw:].rjust(sw, b"\x00"))
        key_buf = (ctypes.c_ubyte * kw)()
        key_len = ctypes.c_int(kw)
        self._fn.restype = None
        self._fn.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int,
                             ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(ctypes.c_int),
                             ctypes.c_int]
        self._fn(seed_buf, sw, key_buf, ctypes.byref(key_len), level)
        n = min(max(key_len.value, 0), kw)
        return bytes(key_buf[:n]) if n else bytes(key_buf)

    # ---- 约定 C: (appKey*, appLen, seed*, seedLen, key*, keyLen, level) ----
    def _call_c(self, seed: bytes, level: int) -> bytes:
        sw, kw = self.seed_width, self.key_width
        seed_buf = (ctypes.c_ubyte * sw)(*seed[-sw:].rjust(sw, b"\x00"))
        key_buf = (ctypes.c_ubyte * kw)()
        self._fn.restype = ctypes.c_int
        self._fn.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int,
                             ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int,
                             ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int,
                             ctypes.c_int]
        rc = self._fn(None, 0, seed_buf, sw, key_buf, kw, level)
        if rc != 0:
            raise SecurityError(f"DLL 约定 C 返回错误码 {rc}")
        return bytes(key_buf)

    def __call__(self, seed: bytes, level: int) -> bytes:
        self._ensure_loaded()
        if len(seed) == 0:
            raise SecurityError("DLL 算法收到空 seed（ECU 已解锁？不应走到这里）")
        with self._lock:                      # DLL 非线程安全时兜底串行
            if self.convention == DllConvention.A:
                return self._call_a(seed, level)
            if self.convention == DllConvention.B:
                return self._call_b(seed, level)
            if self.convention == DllConvention.C:
                return self._call_c(seed, level)
            raise SecurityError(f"未知 DLL 调用约定: {self.convention}")

    def __repr__(self) -> str:
        return (f"DllAlgo({self.dll_path!r}, {self.func_name!r}, "
                f"convention={self.convention}, seed_w={self.seed_width})")


# ============================================================================
# 形态 4 — 内置算法注册表
# ============================================================================
_BUILTIN: Dict[str, Callable[..., SeedKeyFn]] = {}


def register_builtin(name: str, factory: Callable[..., SeedKeyFn]) -> None:
    """注册/覆盖内置算法工厂: factory(**params) -> SeedKeyFn。"""
    _BUILTIN[str(name).lower()] = factory


def available_builtins() -> Tuple[str, ...]:
    return tuple(sorted(_BUILTIN))


def _hex_to_bytes(s) -> bytes:
    s = str(s or "").replace(" ", "").strip()
    if len(s) % 2:
        s = "0" + s
    return bytes.fromhex(s)


def _builtin_xor(key: str = "00000000", **_) -> SeedKeyFn:
    kb = _hex_to_bytes(key)

    def fn(seed: bytes, level: int) -> bytes:
        out = bytearray(seed)
        for i in range(len(out)):
            out[i] ^= kb[i % len(kb)]
        return bytes(out)
    return fn


def _builtin_const_add(const: int = 1, **_) -> SeedKeyFn:
    n_const = int(str(const), 0) if isinstance(const, str) else int(const)

    def fn(seed: bytes, level: int) -> bytes:
        n = int.from_bytes(seed, "big")
        width = max(len(seed), 1)
        return ((n + n_const) % (1 << (8 * width))).to_bytes(width, "big")
    return fn


def _builtin_invert(**_) -> SeedKeyFn:
    def fn(seed: bytes, level: int) -> bytes:
        return bytes((~b) & 0xFF for b in seed)
    return fn


def _builtin_swap(**_) -> SeedKeyFn:
    def fn(seed: bytes, level: int) -> bytes:
        return bytes(reversed(seed))
    return fn


def _builtin_mask_shift(mask: int = 0xFFFFFFFF, shift: int = 0, **_) -> SeedKeyFn:
    m = int(str(mask), 0) if isinstance(mask, str) else int(mask)
    sh = int(shift)

    def fn(seed: bytes, level: int) -> bytes:
        n = int.from_bytes(seed, "big")
        width = max(len(seed), 1)
        return (((n & m) >> sh) % (1 << (8 * width))).to_bytes(width, "big")
    return fn


def _builtin_aes_ecb(key: str = "", **_) -> SeedKeyFn:
    """AES-128-ECB: seed 左补零到 16B → 加密 → 取前 4 字节（常见 OEM 变体）。"""
    kb = _hex_to_bytes(key)

    def fn(seed: bytes, level: int) -> bytes:
        try:
            from Crypto.Cipher import AES      # pycryptodome, 可选依赖
        except ImportError as e:
            raise SecurityError(
                "aes_ecb 需要 pycryptodome: pip install pycryptodome") from e
        s = bytearray(seed)
        while len(s) < 16:
            s.append(0x00)
        cipher = AES.new(bytes(kb[:16]), AES.MODE_ECB)
        enc = cipher.encrypt(bytes(s[:16]))
        return bytes(enc[:4])
    return fn


register_builtin("xor", _builtin_xor)
register_builtin("const_add", _builtin_const_add)
register_builtin("invert", _builtin_invert)
register_builtin("swap", _builtin_swap)
register_builtin("mask_shift", _builtin_mask_shift)
register_builtin("aes_ecb", _builtin_aes_ecb)


def builtin(name: str, **params) -> SeedKeyFn:
    """按名取内置算法: builtin("xor", key="1A2B3C4D") -> SeedKeyFn。"""
    factory = _BUILTIN.get(str(name).lower())
    if factory is None:
        raise SecurityError(
            f"未知内置算法 {name!r}（可用: {', '.join(available_builtins())}）")
    return factory(**params)


# ============================================================================
# 等级配对（ISO 14229-1 §11.6: 奇数请求 seed, 偶数发 key）
# ============================================================================
def level_pair(request_level: int) -> Tuple[int, int]:
    """奇数级 → (request_seed_level, send_key_level)。"""
    req = int(request_level) & 0x7F
    if req % 2 == 0:
        raise SecurityError(f"安全等级须为奇数(0x01/0x03/...), 收到 0x{req:02X}")
    if req in (0x43, 0x45, 0x47, 0x49, 0x4B, 0x4D, 0x6B, 0x6D):
        raise SecurityError(f"等级 0x{req:02X} 为 ISO 保留/数据链路安全类, 不支持")
    return req, req + 1


# ============================================================================
# 编排层 — SecurityAccess
# ============================================================================
class SecurityAccess:
    """27 安全访问编排（复用 UdsSession._request_lock, 27 两步天然原子）。

    生命周期: 一个 UdsSession 配一个实例；unlock 可反复调用，
    重复解锁同等级时 ECU 侧 seed 全零会短路返回。

    事件回调（可选）: on_event(evt, info_dict)
        evt ∈ {"seed", "key", "unlocked", "delay_retry", "already_unlocked"}
    """

    def __init__(self, session: UdsSession,
                 algo: Optional[SeedKeyFn] = None,
                 default_pre_session: Optional[int] = 0x03,
                 on_event: Optional[Callable[[str, dict], None]] = None,
                 verbose_security: bool = False,
                 log=None) -> None:
        self.session = session
        self.algo = algo                       # 会话级默认算法
        self.default_pre_session = default_pre_session
        self.on_event = on_event
        self.verbose_security = bool(verbose_security)
        self._log = log or (lambda lvl, msg: None)

        self.current_level = 0                 # 最近成功解锁等级(0=无)
        self.last_seed = b""
        self.last_key = b""                    # 敏感: 仅 verbose_security 打日志
        self.unlocked_at = 0.0                 # monotonic 时间戳

    # ---- 内部 ----
    def _emit(self, evt: str, **info) -> None:
        if self.on_event:
            try:
                self.on_event(evt, info)
            except Exception:
                pass

    def _log_sec(self, msg: str) -> None:
        self._log(0, f"[SEC] {msg}")

    # ---- 单步 ----
    def request_seed(self, request_level: int, timeout_s: float = 2.0) -> bytes:
        """27 0x(奇) → 正响应 67 level+seed, 返回 seed(可为全零=已解锁)。"""
        req, _ = level_pair(request_level)
        r = self.session.request(0x27, bytes([req]), timeout_s=timeout_s)
        if r.code == ResponseCode.NEGATIVE:
            raise SecurityError(
                f"requestSeed 失败: NRC 0x{r.nrc:02X}")
        if r.code != ResponseCode.POSITIVE or len(r.data) < 2 or r.data[0] != req:
            raise SecurityError(
                f"requestSeed 意外响应: code={r.code} data={r.data.hex(' ').upper()}")
        seed = bytes(r.data[1:])
        self.last_seed = seed
        self._log_sec(f"level 0x{req:02X} seed={mask_hex(seed, self.verbose_security)}")
        self._emit("seed", level=req, seed=seed)
        return seed

    def send_key(self, request_level: int, key: bytes,
                 timeout_s: float = 2.0) -> None:
        """27 0x(偶)+key → 正响应 67 level；NRC 语义见 unlock。"""
        req, send = level_pair(request_level)
        payload = bytes([send]) + bytes(key)
        r = self.session.request(0x27, payload, timeout_s=timeout_s)
        if r.code == ResponseCode.POSITIVE:
            self._log_sec(f"level 0x{send:02X} key={mask_key(key, self.verbose_security)} OK")
            return
        if r.code == ResponseCode.NEGATIVE:
            if r.nrc == NRC_EXCEED_ATTEMPT:
                raise SecurityLockedError(
                    f"NRC 0x36 超尝试次数(等级 0x{send:02X})——ECU 已锁定, 请勿重试")
            if r.nrc == NRC_REQUIRED_DELAY:
                raise _DelayNotExpired(f"NRC 0x37 防爆破延时未到期(等级 0x{send:02X})")
            if r.nrc == NRC_INVALID_KEY:
                raise SecurityError(
                    f"NRC 0x35 key 错误(等级 0x{send:02X})——检查算法/密钥版本")
            raise SecurityError(f"sendKey 失败: NRC 0x{r.nrc:02X}")
        raise SecurityError(f"sendKey 意外结果: code={r.code}")

    # ---- 编排 ----
    def unlock(self, request_level: int, *,
               algo: Optional[SeedKeyFn] = None,
               key: Optional[bytes] = None,
               builtin: Optional[str] = None,
               builtin_params: Optional[dict] = None,
               dll: Optional[DllAlgo] = None,
               pre_session: Optional[int] = -1,
               on_delay: str = DelayPolicy.RAISE,
               delay_wait_s: float = 1.0) -> bool:
        """自动解锁: [会话前置] → requestSeed → [algo] → sendKey。

        参数（互斥, 优先级 key > dll > builtin > algo > self.algo）:
            key=b"..."      形态1 固定密钥（跳过 requestSeed）
            dll=DllAlgo     形态3 OEM DLL
            builtin="xor"   形态4 内置表（+ builtin_params）
            algo=callable   形态2 任意回调

        pre_session: 解锁前先发的诊断会话（None=不发, -1=用 default_pre_session）
        on_delay:    DelayPolicy.RAISE / DelayPolicy.WAIT

        返回 True=本次完成解锁流程; 已解锁(seed全零)也返回 True。
        """
        req, send = level_pair(request_level)
        fn = self._resolve_algo(algo=algo, builtin=builtin,
                                builtin_params=builtin_params, dll=dll, key=key)
        if fn is None and key is None:
            raise SecurityError("未提供 seed→key 算法 (algo=/builtin=/dll=/key= 之一)")

        # 会话前置（-1 → 默认; None → 不发）
        if pre_session == -1:
            pre_session = self.default_pre_session
        if pre_session is not None:
            self.session.request(0x10, bytes([pre_session]), timeout_s=2.0)

        retries_left = 1 if on_delay == DelayPolicy.WAIT else 0
        while True:
            if key is None:
                seed = self.request_seed(req)
                if not seed or not any(seed):
                    self._log_sec(f"level 0x{req:02X} seed 全零 → 已解锁")
                    self._emit("already_unlocked", level=req)
                    self._mark_unlocked(req)
                    return True
                key_bytes = fn(seed, req)
                if not key_bytes:
                    raise SecurityError("seed→key 算法返回空 key")
                self.last_key = bytes(key_bytes)
            else:
                # 形态1 固定 key: 仍先探测 seed —— 已解锁则不发 key（省一次尝试计数）
                seed = self.request_seed(req)
                if not seed or not any(seed):
                    self._log_sec(f"level 0x{req:02X} seed 全零 → 已解锁")
                    self._emit("already_unlocked", level=req)
                    self._mark_unlocked(req)
                    return True
                key_bytes = bytes(key)
                self.last_key = key_bytes

            try:
                self.send_key(req, key_bytes)
                self._mark_unlocked(req)
                self._emit("unlocked", level=req)
                return True
            except _DelayNotExpired:
                if retries_left > 0:
                    retries_left -= 1
                    self._log_sec(f"0x37 → 等待 {delay_wait_s}s 后重试一次")
                    self._emit("delay_retry", level=req, wait=delay_wait_s)
                    time.sleep(delay_wait_s)
                    continue
                raise

    # ---- 辅助 ----
    def _resolve_algo(self, *, algo, builtin, builtin_params, dll, key) -> Optional[SeedKeyFn]:
        if key is not None:
            return None                          # 固定 key 不需要算法
        if dll is not None:
            return dll
        if builtin is not None:
            return globals()["builtin"](builtin, **(builtin_params or {}))
        if algo is not None:
            return algo
        return self.algo

    def _mark_unlocked(self, level: int) -> None:
        self.current_level = level
        self.unlocked_at = time.monotonic()

    def status(self) -> dict:
        return {
            "current_level": self.current_level,
            "unlocked_at": self.unlocked_at,
            "last_seed_masked": mask_hex(self.last_seed, self.verbose_security),
            "last_key_masked": mask_key(self.last_key, self.verbose_security),
        }


class _DelayNotExpired(SecurityError):
    """内部用: 0x37 延时未到期（unlock 编排层捕获做 WAIT 重试）。"""
