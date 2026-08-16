"""Terminal color and glyph formatting with ASCII and NO_COLOR fallback support."""

from __future__ import annotations

import os


class ColorScheme:
    """ANSI color palette and glyphs used by the CLI."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    UNDERLINE = "\033[4m"

    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"

    BG_RED = "\033[101m"

    SUCCESS = GREEN
    ERROR = RED
    WARNING = YELLOW
    INFO = BLUE
    DEBUG = DIM
    PRIMARY = CYAN
    ACCENT = GREEN
    MUTED = DIM

    # Box drawing and UI glyphs
    BOX_TL = "╭"
    BOX_TR = "╮"
    BOX_BL = "╰"
    BOX_BR = "╯"
    BOX_H = "─"
    BOX_V = "│"
    BOX_T = "┬"
    BOX_B = "┴"
    BOX_L = "├"
    BOX_R = "┤"
    BOX_CROSS = "┼"

    DOT_ON = "●"
    DOT_OFF = "○"
    CHECK = "✔"
    CROSS = "✖"
    ARROW = "➜"
    BULLET = "•"

    @classmethod
    def disable_colors(cls) -> None:
        """Strip all ANSI escape sequences."""
        for attr in (
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
        ):
            setattr(cls, attr, "")

    @classmethod
    def enable_ascii_mode(cls) -> None:
        """Switch Unicode box drawing and symbols to ASCII equivalents."""
        cls.BOX_TL = "+"
        cls.BOX_TR = "+"
        cls.BOX_BL = "+"
        cls.BOX_BR = "+"
        cls.BOX_H = "-"
        cls.BOX_V = "|"
        cls.BOX_T = "+"
        cls.BOX_B = "+"
        cls.BOX_L = "+"
        cls.BOX_R = "+"
        cls.BOX_CROSS = "+"

        cls.DOT_ON = "[*]"
        cls.DOT_OFF = "[ ]"
        cls.CHECK = "[OK]"
        cls.CROSS = "[X]"
        cls.ARROW = "->"
        cls.BULLET = "*"

    @classmethod
    def auto_configure(cls) -> None:
        """Inspect environment for NO_COLOR and Unicode capabilities."""
        # NO_COLOR standard
        if "NO_COLOR" in os.environ and os.environ["NO_COLOR"] != "":
            cls.disable_colors()

        from platforms.detector import detect_terminal_capabilities

        _interactive, ansi, unicode_glyphs, _width = detect_terminal_capabilities()
        if not ansi:
            cls.disable_colors()
        if not unicode_glyphs:
            cls.enable_ascii_mode()


C = ColorScheme()
C.auto_configure()
