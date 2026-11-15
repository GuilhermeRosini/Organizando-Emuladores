"""Caminhos do hub.

Tudo é calculado a partir da pasta onde o hub está, para que ele funcione
igual em qualquer computador ou ponto de montagem de pendrive.
"""
import os
from pathlib import Path

# Estrutura do hub:
#   <raiz>/Abrir My Games          ← o único arquivo para clicar
#   <raiz>/Emuladores/<Console>/   ← emulador + "Adicionar Jogos"
#   <raiz>/Sistema/                ← programa, configuração e registros
APP_DIR = Path(__file__).resolve().parent.parent
SYSTEM_DIR = APP_DIR.parent
ROOT = SYSTEM_DIR.parent
ASSETS_DIR = APP_DIR / "assets"
EMULATORS_DIR = ROOT / "Emuladores"
GAMES_FOLDER_NAME = "Adicionar Jogos"
LOGS_DIR = SYSTEM_DIR / "logs"
CONFIG_FILE = SYSTEM_DIR / "config.json"
APP_ICON = ASSETS_DIR / "my-games.png"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "my-games"


def same_device(first, second):
    try:
        return os.stat(first).st_dev == os.stat(second).st_dev
    except OSError:
        return False


def store_path(path):
    """Guarda um caminho escolhido pelo usuário de forma portátil.

    Salva o caminho completo e, quando ele está no mesmo disco do hub, também
    o caminho relativo. Assim, num pendrive montado em outro lugar, o relativo
    continua valendo; e no computador original o completo continua valendo.
    """
    path = Path(path).expanduser().resolve()
    stored = {"caminho": str(path)}
    if same_device(path, ROOT):
        stored["relativo"] = os.path.relpath(path, ROOT)
    return stored


def resolve_stored(value):
    """Converte o que foi salvo por store_path (ou um texto antigo) num Path."""
    if not value:
        return None
    if isinstance(value, str):
        path = Path(value).expanduser()
        return (path if path.is_absolute() else ROOT / path).resolve()
    candidates = []
    if value.get("relativo"):
        candidates.append((ROOT / value["relativo"]).resolve())
    if value.get("caminho"):
        candidates.append(Path(value["caminho"]).expanduser())
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else None


def display_path(path):
    """Mostra caminhos dentro do hub de forma curta."""
    if path is None:
        return ""
    path = Path(path)
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        pass
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)
