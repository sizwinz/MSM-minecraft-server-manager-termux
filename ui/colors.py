"""Terminal color and glyph formatting with ASCII and NO_COLOR fallback support."""

from __future__ import annotations

import os
import re
import sys

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return _ANSI_RE.sub("", text)


def visible_len(text: str) -> int:
    """Calculate visible character length excluding ANSI escape sequences."""
    return len(strip_ansi(text))


class _DualMethod:
    """Allow a method to be called cleanly on both an instance and the class."""

    def __init__(self, func):
        self.func = func

    def __get__(self, instance, owner=None):
        target = instance if instance is not None else owner

        def wrapper(*args, **kwargs):
            return self.func(target, *args, **kwargs)

        return wrapper


class ColorScheme:
    """ANSI color palette and glyphs used by the CLI."""

    # True-Black & Warm Copper Palette
    ACCENT = "\033[38;5;208m"
    FG = "\033[97m"
    DIM = "\033[90m"
    OK = "\033[38;5;78m"
    WARN = "\033[38;5;214m"
    ERR = "\033[38;5;203m"

    RESET = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"

    # Compatibility aliases
    RED = ERR
    GREEN = OK
    YELLOW = WARN
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = FG
    BG_RED = "\033[101m"

    SUCCESS = OK
    ERROR = ERR
    WARNING = WARN
    INFO = CYAN
    DEBUG = DIM
    PRIMARY = ACCENT
    MUTED = DIM

    # Box drawing and UI glyphs (Unicode geometric)
    BOX_TL = "┌"
    BOX_TR = "┐"
    BOX_BL = "└"
    BOX_BR = "┘"
    BOX_H = "─"
    BOX_V = "│"
    BOX_T = "┬"
    BOX_B = "┴"
    BOX_L = "├"
    BOX_R = "┤"
    BOX_CROSS = "┼"

    TREE_BRANCH = "├─"
    TREE_CORNER = "└─"

    DOT_ON = "●"
    DOT_OFF = "○"
    DOT_HALF = "◐"
    POINTER = "›"
    SQUARE = "▪"
    ARROW = "›"
    BULLET = "▪"
    CHECK = "[OK]"
    CROSS = "[X]"

    _COLOR_ATTRS = (
        "RESET",
        "BOLD",
        "DIM",
        "UNDERLINE",
        "RED",
        "GREEN",
        "YELLOW",
        "BLUE",
        "MAGENTA",
        "CYAN",
        "WHITE",
        "BG_RED",
        "SUCCESS",
        "ERROR",
        "WARNING",
        "INFO",
        "DEBUG",
        "PRIMARY",
        "ACCENT",
        "MUTED",
        "FG",
        "OK",
        "WARN",
        "ERR",
    )

    def __init__(self) -> None:
        self.reset_defaults()

    def reset_defaults(self) -> None:
        """Reset instance attributes to the default ColorScheme palette and glyphs."""
        self.ACCENT = "\033[38;5;208m"
        self.FG = "\033[97m"
        self.DIM = "\033[90m"
        self.OK = "\033[38;5;78m"
        self.WARN = "\033[38;5;214m"
        self.ERR = "\033[38;5;203m"
        self.RESET = "\033[0m"
        self.BOLD = "\033[1m"
        self.UNDERLINE = "\033[4m"
        self.RED = self.ERR
        self.GREEN = self.OK
        self.YELLOW = self.WARN
        self.BLUE = "\033[94m"
        self.MAGENTA = "\033[95m"
        self.CYAN = "\033[96m"
        self.WHITE = self.FG
        self.BG_RED = "\033[101m"
        self.SUCCESS = self.OK
        self.ERROR = self.ERR
        self.WARNING = self.WARN
        self.INFO = self.CYAN
        self.DEBUG = self.DIM
        self.PRIMARY = self.ACCENT
        self.MUTED = self.DIM

        self.BOX_TL = "┌"
        self.BOX_TR = "┐"
        self.BOX_BL = "└"
        self.BOX_BR = "┘"
        self.BOX_H = "─"
        self.BOX_V = "│"
        self.BOX_T = "┬"
        self.BOX_B = "┴"
        self.BOX_L = "├"
        self.BOX_R = "┤"
        self.BOX_CROSS = "┼"
        self.TREE_BRANCH = "├─"
        self.TREE_CORNER = "└─"
        self.DOT_ON = "●"
        self.DOT_OFF = "○"
        self.DOT_HALF = "◐"
        self.POINTER = "›"
        self.SQUARE = "▪"
        self.ARROW = "›"
        self.BULLET = "▪"
        self.CHECK = "[OK]"
        self.CROSS = "[X]"

    @_DualMethod
    def disable_colors(target) -> None:
        """Strip all ANSI escape sequences on target instance or class."""
        for attr in ColorScheme._COLOR_ATTRS:
            setattr(target, attr, "")

    @_DualMethod
    def enable_ascii_mode(target) -> None:
        """Switch Unicode box drawing and symbols to ASCII equivalents."""
        target.BOX_TL = "+"
        target.BOX_TR = "+"
        target.BOX_BL = "+"
        target.BOX_BR = "+"
        target.BOX_H = "-"
        target.BOX_V = "|"
        target.BOX_T = "+"
        target.BOX_B = "+"
        target.BOX_L = "+"
        target.BOX_R = "+"
        target.BOX_CROSS = "+"

        target.TREE_BRANCH = "+-"
        target.TREE_CORNER = "\\-"

        target.DOT_ON = "*"
        target.DOT_OFF = "o"
        target.DOT_HALF = "o"
        target.POINTER = ">"
        target.SQUARE = "*"
        target.ARROW = ">"
        target.BULLET = "*"
        target.CHECK = "[OK]"
        target.CROSS = "[X]"

    @_DualMethod
    def auto_configure(target) -> None:
        """Inspect environment for NO_COLOR and Unicode capabilities."""
        if "NO_COLOR" in os.environ and os.environ["NO_COLOR"] != "":
            target.disable_colors()
            return

        term = os.environ.get("TERM", "").lower()
        if term == "dumb":
            target.disable_colors()
            return

        from platforms.detector import detect_terminal_capabilities

        _interactive, ansi, unicode_glyphs, _width = detect_terminal_capabilities()
        if not ansi:
            target.disable_colors()

        encoding = (getattr(sys.stdout, "encoding", "") or "utf-8").lower()
        if not unicode_glyphs or "utf" not in encoding:
            target.enable_ascii_mode()


C = ColorScheme()
C.auto_configure()
