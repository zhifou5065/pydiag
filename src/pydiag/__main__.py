# -*- coding: utf-8 -*-
"""支持 `python -m pydiag` 调用，等价于 `pydiag` 命令。

存在的意义：`pip install --user` 装出来的 `pydiag` 可执行文件通常落在
用户级 Scripts 目录（如 %APPDATA%\\Python\\Python3xx\\Scripts），该目录默认
不在 PATH 上，用户敲 `pydiag` 会提示找不到命令。此时 `python -m pydiag`
是可用的替代入口。
"""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
