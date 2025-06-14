import os
import sys
import threading
import ctypes
import asyncio
from typing import Optional

try:
    from PIL import Image
    import pystray
    from pystray import Menu, MenuItem
except ImportError as e:  # pragma: no cover
    # Allow importing this module even when GUI libs are missing (e.g. on CI)
    raise RuntimeError(
        "pystray or Pillow is not installed. Add 'pystray' and 'pillow' to requirements before using tray support."  # noqa: E501
    ) from e

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _resource_path(rel_path: str) -> str:
    """Return the absolute path to a resource bundled by PyInstaller.

    When running in a PyInstaller one-file bundle, data are unpacked to a
    temporary directory referenced by ``sys._MEIPASS``.  When running from the
    source tree it falls back to the current working directory.
    """
    base_path = getattr(sys, "_MEIPASS", os.path.abspath(os.getcwd()))
    return os.path.join(base_path, rel_path)


def _build_icon(loop: asyncio.AbstractEventLoop, title: str) -> "pystray.Icon":
    """Create a pystray.Icon instance with standard menu."""

    # Load icon – default to res/icon.ico shipped with the project
    try:
        image_path = _resource_path("res/icon.ico")
        img = Image.open(image_path)
    except Exception:
        # Fallback to a simple blank image to avoid crash if icon missing
        img = Image.new("RGB", (64, 64), (0, 0, 0))

    # Console show/hide toggle (Windows only; no-op on other OS)
    _console_visible = True

    def _toggle_console(_icon, _item):
        nonlocal _console_visible
        if sys.platform.startswith("win"):
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0 if _console_visible else 5)
        else:
            # On non-Windows platforms, keep flag but no actual effect
            pass
        _console_visible = not _console_visible

    def _on_exit(_icon, _item):
        # Hide icon first to avoid lingering
        _icon.visible = False
        # Ask the asyncio loop running on the main thread to stop gracefully
        loop.call_soon_threadsafe(loop.stop)

    menu = Menu(
        MenuItem("隐藏/显示窗口", _toggle_console),
        MenuItem("退出", _on_exit)
    )

    return pystray.Icon("lx_music_api_server", img, title, menu)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_tray(loop: asyncio.AbstractEventLoop) -> None:
    """Blocking helper that starts the tray menu in current thread."""

    icon = _build_icon(loop, "LX Music API Server")
    # `icon.run()` blocks; therefore place this entire function in a dedicated
    # daemon thread from caller.
    icon.run()


def start_tray_background(loop: asyncio.AbstractEventLoop) -> None:
    """Spawn tray in a background daemon thread."""

    threading.Thread(
        target=run_tray,
        args=(loop,),
        daemon=True,
    ).start() 