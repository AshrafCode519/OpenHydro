"""
OpenHydro API Package
"""

from .server import create_app


def websocket_implementation() -> str:
    """
    uvicorn's WebSocket implementation for the app (run.py) and the UI tests: the "websockets-sansio" one
    where available. uvicorn deprecated its old "websockets" implementation (a warning at every start)
    and will remove it, and the build always takes the newest uvicorn.
    """
    from uvicorn.config import WS_PROTOCOLS
    return "websockets-sansio" if "websockets-sansio" in WS_PROTOCOLS else "websockets"


__all__ = ["create_app", "websocket_implementation"]
