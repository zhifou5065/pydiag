# -*- coding: utf-8 -*-
"""pydiag.logutil — 统一日志基础设施。

设计:
    * 日志文件: $PYDIAG_HOME/logs/pydiag.log (默认 ~/.pydiag/logs/pydiag.log)
      可用 PYDIAG_LOG_LEVEL 覆盖级别 (DEBUG/INFO/WARNING/ERROR, 默认 INFO)。
    * RotatingFileHandler: 单文件 5MB, 保留 5 个备份 — 不会无限膨胀。
    * 控制台只输出用户需要看到的 (由 CLI 的 print 负责); logging 落文件为主,
      ERROR 级同时镜像到 stderr, 保证无日志文件权限时问题仍可见。
    * 帧级收发走 DEBUG (verbose 模式或 PYDIAG_LOG_LEVEL=DEBUG 打开)。

用法:
    from .logutil import get_logger
    log = get_logger(__name__)          # 名字如 pydiag.devices.zlg
    log.info("open device %s", dev_id)
    log.debug("TX 0x%03X %s", can_id, data.hex())
    log.exception("open failed")        # 自动带堆栈

命令行排障入口:
    pydiag logs [--tail N] [--level DEBUG]   查看日志
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

__all__ = ["get_logger", "LOG_DIR", "LOG_PATH", "setup_logging",
           "tail_log", "LOG_FORMAT"]

_CONFIG_DIR = os.environ.get("PYDIAG_HOME") or os.path.join(
    os.path.expanduser("~"), ".pydiag")
LOG_DIR = os.path.join(_CONFIG_DIR, "logs")
LOG_PATH = os.path.join(LOG_DIR, "pydiag.log")
LOG_FORMAT = "%(asctime)s.%(msecs)03d [%(levelname)-7s] %(name)s: %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

_MAX_BYTES = 5 * 1024 * 1024
_BACKUPS = 5

_initialized = False


def _level_from_env() -> int:
    lv = os.environ.get("PYDIAG_LOG_LEVEL", "INFO").upper()
    return {"DEBUG": logging.DEBUG, "INFO": logging.INFO,
            "WARNING": logging.WARNING, "ERROR": logging.ERROR}.get(lv, logging.INFO)


def _usable_log_dir() -> str:
    """选择可用日志目录: PYDIAG_HOME/logs → ~/.pydiag/logs → %TEMP%/pydiag/logs。"""
    for d in (LOG_DIR, os.path.join(os.path.expanduser("~"), ".pydiag", "logs"),
              os.path.join(os.environ.get("TEMP", os.getcwd()),
                           "pydiag", "logs")):
        try:
            os.makedirs(d, exist_ok=True)
            # 试写探测
            probe = os.path.join(d, ".write_test")
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
            return d
        except OSError:
            continue
    return ""


def setup_logging(level: int | None = None) -> str:
    """初始化根 logger（幂等）。返回实际日志文件路径（全部不可写时 ""）。"""
    global _initialized, LOG_DIR, LOG_PATH
    if _initialized:
        return LOG_PATH if os.path.isfile(LOG_PATH) else ""
    lvl = level if level is not None else _level_from_env()
    root = logging.getLogger("pydiag")
    root.setLevel(lvl)
    root.propagate = False

    fh = None
    usable = _usable_log_dir()
    if usable:
        try:
            LOG_DIR = usable
            LOG_PATH = os.path.join(usable, "pydiag.log")
            fh = RotatingFileHandler(LOG_PATH, maxBytes=_MAX_BYTES,
                                     backupCount=_BACKUPS, encoding="utf-8")
            fh.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
            fh.setLevel(logging.DEBUG)      # 文件总是收全量, 级别由 root 过滤
            root.addHandler(fh)
        except OSError:
            fh = None

    # ERROR 镜像到 stderr (无日志文件权限时问题仍可见)
    if not sys.stderr.closed:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(logging.Formatter("[pydiag] %(levelname)s %(name)s: %(message)s"))
        sh.setLevel(logging.ERROR)
        root.addHandler(sh)

    _initialized = True
    return LOG_PATH if fh else ""


def get_logger(name: str) -> logging.Logger:
    """取 pydiag.* 子 logger。首次调用自动完成 setup_logging。"""
    if not name.startswith("pydiag"):
        name = f"pydiag.{name}"
    if not _initialized:
        setup_logging()
    return logging.getLogger(name)


def _resolve_actual_log_path() -> str:
    """找到实际使用的日志文件（setup 动态 fallback 后的路径）。"""
    candidates = [LOG_PATH]
    if os.environ.get("PYDIAG_HOME"):
        candidates.append(os.path.join(os.environ["PYDIAG_HOME"], "logs", "pydiag.log"))
    candidates.append(os.path.join(os.path.expanduser("~"), ".pydiag", "logs", "pydiag.log"))
    temp_dir = os.environ.get("TEMP")
    if temp_dir:
        candidates.append(os.path.join(temp_dir, "pydiag", "logs", "pydiag.log"))
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return ""


def tail_log(n: int = 50) -> str:
    """读日志最后 n 行（`pydiag logs` 子命令用）。"""
    path = _resolve_actual_log_path()
    if not path:
        return "(no log file yet)"
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:])
    except OSError as e:
        return f"(read log failed: {e})"


def switch_level(level: str) -> None:
    """运行时调整级别 (interactive 模式 :log debug 用)。"""
    lv = {"DEBUG": logging.DEBUG, "INFO": logging.INFO,
          "WARNING": logging.WARNING, "ERROR": logging.ERROR}.get(level.upper())
    if lv is not None:
        logging.getLogger("pydiag").setLevel(lv)
