"""Identidade visual de cada aba: cores do fundo e formato das caixas."""
from dataclasses import dataclass


def rgb(value):
    """Converte "#rrggbb" em (r, g, b) de 0 a 1."""
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))


@dataclass(frozen=True)
class Theme:
    """Cores e formato das caixas de uma aba."""
    top: str  # cor do alto do fundo
    bottom: str  # cor da base do fundo
    glow: str  # brilho atrás da prateleira
    lines: str  # linhas onduladas
    spine: str  # lombada das caixas
    spine_text: str
    kind: str  # "CONSOLE DE MESA", "PORTÁTIL"...
    aspect: float = 0.71  # largura / altura da caixa sem capa
    depth: float = 0.085  # espessura da caixa em relação à altura


THEMES = {
    "home": Theme("#101521", "#05070c", "#3a4a72", "#8fa6d8", "#1b2232", "#e8ecf6", "HUB DE EMULAÇÃO"),
    "ps2": Theme("#0d2a78", "#030a24", "#2f6bff", "#7fb0ff", "#101318", "#dfe6f5", "CONSOLE DE MESA", 0.71, 0.08),
    "super-nintendo": Theme(
        "#8f92b8", "#2d2f4a", "#d7d9f2", "#ffffff", "#2b2c38", "#f2f2fa", "CONSOLE DE MESA", 1.38, 0.2
    ),
    "gameboy-advance": Theme(
        "#3d2a9e", "#0c0730", "#7c5cff", "#b9a6ff", "#24195c", "#eae4ff", "PORTÁTIL", 0.98, 0.17
    ),
    "gamecube": Theme("#3b2a96", "#0d0a2e", "#6d5cff", "#a99cff", "#121016", "#e6e2ff", "CONSOLE DE MESA", 0.71, 0.08),
    "nintendo-64": Theme(
        "#0f6b45", "#021a10", "#2ee08a", "#8ff0bf", "#151a17", "#e3f7ec", "CONSOLE DE MESA", 1.38, 0.24
    ),
    "neo-geo": Theme("#7a5a12", "#120c02", "#ffc23a", "#ffe08a", "#121212", "#f6e7c2", "ARCADE · CONSOLE", 0.72, 0.2),
}


def theme_for(key):
    """Tema de uma aba (o da aba Início se não houver um específico)."""
    return THEMES.get(key, THEMES["home"])


# Nome curto de cada console nas abas e no letreiro do topo.
TAB_NAMES = {
    "home": "Início",
    "ps2": "PS2",
    "super-nintendo": "SNES",
    "gameboy-advance": "GBA",
    "gamecube": "GameCube",
    "nintendo-64": "N64",
    "neo-geo": "Neo Geo",
}
