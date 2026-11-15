"""Busca de jogos nas pastas de cada console."""
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .config import Config


@dataclass(frozen=True)
class Game:
    """Um jogo encontrado: arquivo, nome bonito, pasta onde está e tamanho."""
    path: Path
    title: str
    location: str
    size: int

    @property
    def kind(self):
        """Tipo do arquivo em maiúsculas, ex.: "ISO", "ZIP"."""
        return self.path.suffix.lstrip(".").upper()[:4] or "?"


@dataclass
class ScanResult:
    """Resultado da busca: jogos, pastas lidas e pastas que não foram encontradas."""
    games: list
    folders: list
    missing: list


def pretty_size(size):
    """Tamanho legível em português (ex.: "3,5 GB")."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit in ("B", "KB") else f"{value:.1f} {unit}".replace(".", ",")
        value /= 1024
    return ""


def pretty_title(path, titles=None):
    """Nome para mostrar: tira "(USA)", "[!]", código do disco e sublinhados."""
    if titles and path.stem.casefold() in titles:
        return titles[path.stem.casefold()]
    title = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", path.stem)  # tira "(USA)", "[!]"...
    title = re.sub(r"^[A-Z]{4}[_-]?\d{3}\.?\d{2}[.\s_-]*", "", title)  # código do disco (SLUS_212.69.)
    title = re.sub(r"\s+", " ", title.replace("_", " ")).strip(" .-")
    return title or path.stem or path.name


def game_folders_for(console, config):
    """Pasta interna do hub + pastas extras escolhidas pelo usuário."""
    folders = [console.games_dir]
    for folder in config.game_folders(console.id):
        if folder not in folders:
            folders.append(folder)
    return folders


def scan_console(console, config=None):
    """Procura os jogos do console em Adicionar Jogos e nas pastas extras, sem repetir."""
    config = config or Config()
    games = {}
    folders = []
    missing = []
    for folder in game_folders_for(console, config):
        if not folder.is_dir():
            if folder != console.games_dir:
                missing.append(folder)
            continue
        folders.append(folder)
        for directory, subdirectories, files in os.walk(folder, onerror=lambda _e: None):
            subdirectories[:] = sorted(d for d in subdirectories if not d.startswith("."))
            names = {name.casefold() for name in files}
            has_playlist = any(name.endswith((".cue", ".m3u")) for name in names)
            for name in files:
                if name.startswith("."):
                    continue
                path = Path(directory) / name
                suffix = path.suffix.casefold()
                if suffix not in console.extensions or path.stem.casefold() in console.hidden_files:
                    continue
                # .bin acompanhado de .cue/.m3u é só uma trilha do mesmo jogo.
                if suffix in (".bin", ".img") and has_playlist:
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                relative = Path(directory).relative_to(folder)
                location = folder.name if relative == Path(".") else f"{folder.name}/{relative.as_posix()}"
                games.setdefault(path.resolve(), Game(path, pretty_title(path, console.titles), location, size))
    ordered = sorted(games.values(), key=lambda game: game.title.casefold())
    return ScanResult(ordered, folders, missing)
