"""macOS process classification for headless commands inside the app bundle."""

from __future__ import annotations

import ctypes
import sys


_APPLICATION_SERVICES = (
    "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
)
_TRANSFORM_TO_UI_ELEMENT = 4
_TRANSFORM_TO_FOREGROUND = 1


class _ProcessSerialNumber(ctypes.Structure):
    _fields_ = [
        ("highLongOfPSN", ctypes.c_uint32),
        ("lowLongOfPSN", ctypes.c_uint32),
    ]


def _transform_process(transform_state: int) -> bool:
    if sys.platform != "darwin":
        return True
    try:
        services = ctypes.CDLL(_APPLICATION_SERVICES)
        services.GetCurrentProcess.argtypes = [ctypes.POINTER(_ProcessSerialNumber)]
        services.GetCurrentProcess.restype = ctypes.c_int32
        services.TransformProcessType.argtypes = [
            ctypes.POINTER(_ProcessSerialNumber),
            ctypes.c_uint32,
        ]
        services.TransformProcessType.restype = ctypes.c_int32
        psn = _ProcessSerialNumber()
        if services.GetCurrentProcess(ctypes.byref(psn)) != 0:
            return False
        return services.TransformProcessType(
            ctypes.byref(psn), transform_state
        ) == 0
    except (AttributeError, OSError):
        return False


def hide_headless_process_from_dock() -> bool:
    """Turn the current packaged CLI/daemon process into a Dock-less UIElement.

    The frozen desktop and daemon share one Mach-O executable inside DBAide.app.
    Without this early transform, LaunchServices treats the daemon as a second
    foreground application and shows a duplicate Dock icon.
    """
    return _transform_process(_TRANSFORM_TO_UI_ELEMENT)


def show_gui_process_in_dock() -> bool:
    """Promote the LSUIElement app to the one normal foreground GUI process."""
    return _transform_process(_TRANSFORM_TO_FOREGROUND)
