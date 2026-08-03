"""Keep the machine awake for the lifetime of a long-running collector.

An overnight capture is worthless if the host sleeps through it. The first
unattended run journalled 9 cycles instead of the expected 96, with a single
four-hour gap in the middle, because the machine's idle timeout was five
minutes and the collector idles for roughly two hundred seconds between cycles.
Nothing crashed and no errors were recorded, which is precisely why this is
worth handling explicitly: a sleeping host looks exactly like a working one.

Scope is deliberately narrow. The request is made by the running process and
released when it exits, so a crash or a Ctrl+C cannot leave the machine
permanently unable to sleep. Editing the user's global power plan would be more
effective and considerably ruder.

The display is allowed to sleep; only system sleep is suppressed. On anything
other than Windows this is a no-op, because that is the platform where the
problem was observed and pretending otherwise would be a lie in the logs.
"""

from __future__ import annotations

import sys
from types import TracebackType
from typing import Self

# SetThreadExecutionState flags.
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


class KeepAwake:
    """Context manager that suppresses system sleep while active."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled and sys.platform == "win32"
        self.active = False
        self.reason = "" if self.enabled else "unsupported_platform"

    def __enter__(self) -> Self:
        if not self.enabled:
            return self
        try:
            import ctypes

            result = ctypes.windll.kernel32.SetThreadExecutionState(
                _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED
            )
            # A zero return means the request was refused. Reporting that is
            # better than silently believing the machine will stay awake.
            self.active = result != 0
            if not self.active:
                self.reason = "request_refused"
        except Exception as error:  # noqa: BLE001 - never block collection
            self.active = False
            self.reason = f"{type(error).__name__}: {error}"
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self.active:
            return
        try:
            import ctypes

            ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
        except Exception as error:  # noqa: BLE001 - shutdown must not raise
            # Releasing the request is best-effort. The OS drops it when the
            # process exits regardless, so a failure here cannot leave the
            # machine stuck awake.
            self.reason = f"release_failed: {type(error).__name__}: {error}"
        self.active = False

    @property
    def status(self) -> str:
        if self.active:
            return "system sleep suppressed while collecting"
        if not self.enabled:
            return "sleep suppression unavailable on this platform"
        return f"sleep suppression failed ({self.reason}); the host may sleep"
