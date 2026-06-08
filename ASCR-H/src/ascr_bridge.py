"""Bridge for calling ASCR modules from ASCR-H.

ASCR and ASCR-H both expose a top-level package named ``src``.  A long-running
ASCR-H process already has its own ``src`` package imported, so direct path
injection can make ASCR imports resolve against ASCR-H modules.  This helper
temporarily swaps the imported ``src`` package to ASCR and restores ASCR-H after
the call.
"""
from __future__ import annotations

import importlib
import os
import sys
from contextlib import contextmanager
from typing import Any


def ascr_root() -> str:
    default_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "ASCR")
    )
    return os.path.abspath(os.environ.get("ASCR_PROJECT_DIR", default_root))


def _is_src_module(name: str) -> bool:
    return name == "src" or name.startswith("src.")


@contextmanager
def ascr_import_context():
    saved_modules = {name: mod for name, mod in sys.modules.items() if _is_src_module(name)}
    saved_path = list(sys.path)
    root = ascr_root()

    for name in list(sys.modules):
        if _is_src_module(name):
            del sys.modules[name]
    sys.path = [root] + [path for path in sys.path if os.path.abspath(path or ".") != root]

    try:
        yield
    finally:
        for name in list(sys.modules):
            if _is_src_module(name):
                del sys.modules[name]
        sys.modules.update(saved_modules)
        sys.path = saved_path


def call_ascr(module_name: str, function_name: str, *args: Any, **kwargs: Any) -> Any:
    """Import ``src.<module_name>`` from ASCR and call ``function_name``."""
    with ascr_import_context():
        module = importlib.import_module(f"src.{module_name}")
        return getattr(module, function_name)(*args, **kwargs)
