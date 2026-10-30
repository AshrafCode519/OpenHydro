"""
OpenHydro API Package
"""

import sys
import builtins
builtins.sys = sys

from .server import create_app

__all__ = ["create_app"]

