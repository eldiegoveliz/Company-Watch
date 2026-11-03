"""Compatibility imports for installations using the original package name."""
from importlib import import_module
import sys

from company_watch import __version__

for _name in ("config", "profiles", "timeutils", "db", "classifiers", "scheduler",
              "pollers", "telegram_alerts", "analyzer", "daemon"):
    _module = import_module(f"company_watch.{_name}")
    sys.modules[f"{__name__}.{_name}"] = _module
    globals()[_name] = _module
