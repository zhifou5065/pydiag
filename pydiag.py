#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pydiag launcher: run from source tree (adds src/ to sys.path).

Not needed after `pip install pydiag` - use the `pydiag` command then.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from pydiag.cli import main

if __name__ == "__main__":
    sys.exit(main())