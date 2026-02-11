"""Low-level automation primitives built on top of GUI automation libraries."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

try:  # pragma: no cover - optional dependency
    import pyautogui
except Exception:  # pragma: no cover - fallback to optional import errors
    pyautogui = None  # type: ignore

try:  # pragma: no cover - optional dependency
    import keyboard
except Exception:  # pragma: no cover - fallback to optional import errors
    keyboard = None  # type: ignore

LOGGER = logging.getLogger(__name__)


class AutomationError(RuntimeError):
    """Raised when automation safeguards fail."""


@dataclass
class SafetySettings:
    """Configuration that keeps automation tasks safe for end users."""

    delay_between_actions: float = 0.2
    failsafe: bool = True
    hotkey: Optional[str] = "ctrl+alt+esc"
    require_window_title: Optional[str] = None

    def apply(self) -> None:
        """Apply the safety configuration to the underlying automation engine."""

        if pyautogui is None:
            LOGGER.warning("pyautogui is not available; automation disabled")
            return

        LOGGER.debug(
            "Applying safety settings: delay=%s failsafe=%s hotkey=%s focus=%s",
            self.delay_between_actions,
            self.failsafe,
            self.hotkey,
            self.require_window_title,
        )
        pyautogui.PAUSE = max(self.delay_between_actions, 0)
        pyautogui.FAILSAFE = bool(self.failsafe)

    def ensure_focus(self) -> None:
        """Ensure the expected window has focus before continuing."""

        if not self.require_window_title:
            return

        if pyautogui is None:
            raise AutomationError(
                "pyautogui is required for focus checks but is not installed."
            )

        window = pyautogui.getActiveWindow()  # type: ignore[attr-defined]
        if window is None:
            raise AutomationError(
                "No active window detected while focus is required."
            )

        title = (window.title or "").lower()
        if self.require_window_title.lower() not in title:
            raise AutomationError(
                f"Focused window '{window.title}' does not match required title "
                f"'{self.require_window_title}'."
            )


def ensure_focus(settings: SafetySettings) -> None:
    """Public helper to perform focus checks without exposing internals."""

    settings.ensure_focus()


class HotkeyListener:
    """Optional hotkey monitor that allows the user to cancel execution."""

    def __init__(self, hotkey: Optional[str]):
        self._hotkey = hotkey
        self._event = threading.Event()
        self._registered_hotkey: Optional[str] = None

    def __enter__(self) -> "HotkeyListener":
        if not self._hotkey:
            return self

        if keyboard is None:
            LOGGER.warning(
                "Hotkey '%s' requested but the 'keyboard' package is unavailable.",
                self._hotkey,
            )
            return self

        LOGGER.info("Registering hotkey '%s' for emergency stop", self._hotkey)
        self._registered_hotkey = keyboard.add_hotkey(  # type: ignore[call-arg]
            self._hotkey, self._event.set
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._registered_hotkey and keyboard is not None:
            LOGGER.info("Removing hotkey '%s'", self._hotkey)
            keyboard.remove_hotkey(self._registered_hotkey)  # type: ignore[arg-type]
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self._event.wait(timeout)


class AutomationAPI:
    """Thin wrapper around :mod:`pyautogui` that can be mocked in tests."""

    def __init__(self) -> None:
        if pyautogui is None:
            raise AutomationError(
                "pyautogui is required to use AutomationAPI but is not installed."
            )
        self._pg = pyautogui

    def move_to(self, x: float, y: float, duration: float = 0.0) -> None:
        LOGGER.debug("Moving mouse to (%s, %s) over %ss", x, y, duration)
        self._pg.moveTo(x, y, duration=duration)

    def click(self, button: str = "left") -> None:
        LOGGER.debug("Clicking mouse button '%s'", button)
        self._pg.click(button=button)

    def write(self, text: str, interval: float = 0.0) -> None:
        LOGGER.debug("Typing text '%s' with interval %s", text, interval)
        self._pg.write(text, interval=interval)

    def hotkey(self, *keys: str) -> None:
        LOGGER.debug("Sending hotkey '%s'", "+".join(keys))
        self._pg.hotkey(*keys)

    def sleep(self, seconds: float) -> None:
        LOGGER.debug("Sleeping for %s seconds", seconds)
        time.sleep(max(seconds, 0.0))


def initialize_safety(settings: SafetySettings) -> HotkeyListener:
    """Prepare the automation environment with safety features enabled."""

    settings.apply()
    return HotkeyListener(settings.hotkey)

