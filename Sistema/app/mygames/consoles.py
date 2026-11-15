"""Catálogo dos consoles suportados pelo hub."""
from dataclasses import dataclass, field

from .paths import ASSETS_DIR, EMULATORS_DIR, GAMES_FOLDER_NAME


@dataclass(frozen=True)
class Download:
    """Onde baixar a versão oficial mais recente do emulador recomendado."""

    emulator: str
    repo: str
    asset_pattern: str
    note: str = ""


@dataclass(frozen=True)
class Console:
    id: str
    name: str
    short: str
    folder: str
    maker: str
    year: int
    extensions: frozenset
    download: Download
    bios_note: str = ""
    # Arquivos que são BIOS e não devem aparecer como jogos (ex.: neogeo.zip).
    hidden_files: frozenset = frozenset()
    # Nomes amigáveis para jogos identificados pelo nome curto do arquivo (ex.: kof98).
    titles: dict = field(default_factory=dict)

    @property
    def art(self):
        return ASSETS_DIR / "consoles" / self.id / "console.png"

    @property
    def outline(self):
        return ASSETS_DIR / "consoles" / self.id / "console-outline.png"

    @property
    def emulator_dir(self):
        return EMULATORS_DIR / self.folder

    @property
    def games_dir(self):
        """Pasta "Adicionar Jogos" dentro da pasta do emulador."""
        return self.emulator_dir / GAMES_FOLDER_NAME

    @property
    def data_dir(self):
        """Configurações, saves e memory cards do emulador ficam aqui."""
        return self.emulator_dir / "dados"


def _ext(*items):
    return frozenset("." + item for item in items)


NEO_GEO_TITLES = {
    "kof94": "The King of Fighters '94", "kof95": "The King of Fighters '95",
    "kof96": "The King of Fighters '96", "kof97": "The King of Fighters '97",
    "kof98": "The King of Fighters '98", "kof99": "The King of Fighters '99",
    "kof2000": "The King of Fighters 2000", "kof2001": "The King of Fighters 2001",
    "kof2002": "The King of Fighters 2002", "kof2003": "The King of Fighters 2003",
    "mslug": "Metal Slug", "mslug2": "Metal Slug 2", "mslugx": "Metal Slug X",
    "mslug3": "Metal Slug 3", "mslug4": "Metal Slug 4", "mslug5": "Metal Slug 5",
    "garou": "Garou: Mark of the Wolves", "samsho": "Samurai Shodown",
    "samsho2": "Samurai Shodown II", "samsho3": "Samurai Shodown III",
    "samsho4": "Samurai Shodown IV", "samsho5": "Samurai Shodown V",
    "fatfury1": "Fatal Fury", "fatfury2": "Fatal Fury 2", "fatfury3": "Fatal Fury 3",
    "fatfursp": "Fatal Fury Special", "rbff1": "Real Bout Fatal Fury",
    "rbffspec": "Real Bout Fatal Fury Special", "rbff2": "Real Bout Fatal Fury 2",
    "lastblad": "The Last Blade", "lastbld2": "The Last Blade 2",
    "aof": "Art of Fighting", "aof2": "Art of Fighting 2", "aof3": "Art of Fighting 3",
    "wh1": "World Heroes", "wh2": "World Heroes 2", "whp": "World Heroes Perfect",
    "sengoku": "Sengoku", "sengoku3": "Sengoku 3", "blazstar": "Blazing Star",
    "pulstar": "Pulstar", "neobombe": "Neo Bomberman", "shocktro": "Shock Troopers",
    "shocktr2": "Shock Troopers: 2nd Squad", "twinspri": "Twinkle Star Sprites",
    "matrim": "Matrimelee", "svc": "SNK vs. Capcom: SVC Chaos", "rotd": "Rage of the Dragons",
    "kizuna": "Kizuna Encounter", "breakrev": "Breakers Revenge", "ssideki": "Super Sidekicks",
    "magdrop3": "Magical Drop III", "puzzledp": "Puzzle de Pon!", "mutnat": "Mutation Nation",
    "nam1975": "NAM-1975", "baseball": "Baseball Stars", "ncombat": "Ninja Combat",
}

CONSOLE_LIST = [
    Console(
        id="ps2",
        name="PlayStation 2",
        short="PS2",
        folder="PlayStation 2",
        maker="Sony",
        year=2000,
        extensions=_ext("iso", "chd", "cso", "zso", "cue", "bin", "img", "mdf", "nrg", "gz", "elf", "m3u"),
        download=Download(
            emulator="PCSX2",
            repo="pkgforge-dev/PCSX2-AppImage-Enhanced",
            asset_pattern=r"anylinux-x86_64\.AppImage$",
            note="AppImage autossuficiente mantido pela comunidade (pkgforge-dev).",
        ),
        bios_note=(
            "O PCSX2 precisa da BIOS do seu PlayStation 2. Coloque o arquivo "
            "na pasta Emuladores/PlayStation 2/bios."
        ),
    ),
    Console(
        id="super-nintendo",
        name="Super Nintendo",
        short="SNES",
        folder="Super Nintendo",
        maker="Nintendo",
        year=1990,
        extensions=_ext("sfc", "smc", "fig", "swc", "bs", "st", "zip", "7z", "jma"),
        download=Download(
            emulator="Snes9x",
            repo="pkgforge-dev/Snes9x-AppImage-Enhanced",
            asset_pattern=r"anylinux-x86_64\.AppImage$",
            note="AppImage autossuficiente mantido pela comunidade (pkgforge-dev).",
        ),
    ),
    Console(
        id="gameboy-advance",
        name="Game Boy Advance",
        short="GBA",
        folder="Game Boy Advance",
        maker="Nintendo",
        year=2001,
        extensions=_ext("gba", "agb", "gb", "gbc", "sgb", "mb", "zip", "7z"),
        download=Download(
            emulator="mGBA",
            repo="mgba-emu/mgba",
            asset_pattern=r"appimage-x64\.appimage$",
        ),
    ),
    Console(
        id="gamecube",
        name="GameCube",
        short="GC",
        folder="GameCube",
        maker="Nintendo",
        year=2001,
        extensions=_ext("iso", "gcm", "gcz", "rvz", "wia", "ciso", "wbfs", "tgc", "dol", "elf", "m3u"),
        download=Download(
            emulator="Dolphin",
            repo="pkgforge-dev/Dolphin-emu-AppImage",
            asset_pattern=r"anylinux-x86_64\.AppImage$",
            note="AppImage não oficial mantido pela comunidade (pkgforge-dev).",
        ),
    ),
    Console(
        id="nintendo-64",
        name="Nintendo 64",
        short="N64",
        folder="Nintendo 64",
        maker="Nintendo",
        year=1996,
        extensions=_ext("n64", "z64", "v64", "ndd", "zip", "7z"),
        download=Download(
            emulator="RMG",
            repo="Rosalie241/RMG",
            asset_pattern=r"Linux64-.*\.AppImage$",
        ),
    ),
    Console(
        id="neo-geo",
        name="Neo Geo",
        short="NEO",
        folder="Neo Geo",
        maker="SNK",
        year=1990,
        extensions=_ext("neo", "zip", "7z"),
        download=Download(
            emulator="RetroArch",
            repo="",
            asset_pattern="",
            note=(
                "RetroArch com os núcleos Geolith (jogos .neo, mesmo dentro de .zip) e FBNeo "
                "(romsets no padrão MAME, ex.: kof98.zip — esses não podem ser renomeados)."
            ),
        ),
        bios_note=(
            "O Neo Geo precisa de uma BIOS em Emuladores/Neo Geo/bios: aes.zip (modo console) "
            "ou neogeo.zip (modo arcade). Não descompacte."
        ),
        hidden_files=frozenset({"neogeo", "aes", "neocdz", "ng"}),
        titles=NEO_GEO_TITLES,
    ),
]

CONSOLES = {console.id: console for console in CONSOLE_LIST}
