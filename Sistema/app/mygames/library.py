"""Busca de jogos nas pastas de cada console."""
import os
from dataclasses import dataclass
from pathlib import Path

from .config import Config


@dataclass(frozen=True)
class Game:
    path: Path
    title: str
    location: str
    size: int

    @property
    def kind(self):
        return self.path.suffix.lstrip(".").upper()[:4] or "?"


@dataclass
class ScanResult:
    games: list
    folders: list
    missing: list


def pretty_size(size):
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit in ("B", "KB") else f"{value:.1f} {unit}".replace(".", ",")
        value /= 1024
    return ""


def pretty_title(path, titles=None):
    if titles and path.stem.casefold() in titles:
        return titles[path.stem.casefold()]
    return path.stem.replace("_", " ").strip() or path.name


def game_folders_for(console, config):
    """Pasta interna do hub + pastas extras escolhidas pelo usuário."""
    folders = [console.games_dir]
    for folder in config.game_folders(console.id):
        if folder not in folders:
            folders.append(folder)
    return folders


def scan_console(console, config=None):
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
