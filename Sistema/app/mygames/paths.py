"""Caminhos do hub.

Tudo é calculado a partir da pasta onde o hub está, para que ele funcione
igual em qualquer computador ou ponto de montagem de pendrive.
"""
import os
import sys
from pathlib import Path

# Estrutura do hub:
#   <raiz>/Abrir My Games (Linux)        ← o arquivo para clicar no Linux
#   <raiz>/Abrir My Games (Windows).exe  ← o arquivo para clicar no Windows
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
IS_WINDOWS = sys.platform == "win32"
# Pasta Windows do hub: runtime (Python + GTK) e ferramentas usadas só no Windows.
WINDOWS_DIR = SYSTEM_DIR / "windows"
# Emuladores para Windows ficam numa subpasta de cada console (os de Linux ficam na raiz dela).
WINDOWS_EMULATOR_FOLDER = "Windows"
# Cache local deste computador (cópias executáveis, AppImages extraídos...). Não vai junto com o hub.
if IS_WINDOWS:
    CACHE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "my-games"
else:
    CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "my-games"


def same_device(first, second):
    """Os dois caminhos estão no mesmo disco/pendrive?"""
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
    """Converte o que foi salvo por store_path (ou um texto antigo) num Path.

    Pastas dentro do hub usam o caminho relativo (acompanham o hub quando ele é
    movido). Pastas fora do hub, como ~/Downloads, usam o caminho completo,
    porque elas não se mexem junto com o hub.
    """
    if not value:
        return None
    if isinstance(value, str):
        path = Path(value).expanduser()
        return (path if path.is_absolute() else ROOT / path).resolve()
    relative = value.get("relativo")
    inside = bool(relative) and not Path(relative).parts[:1] == ("..",)
    candidates = []
    if relative and inside:
        candidates.append((ROOT / relative).resolve())
    if value.get("caminho"):
        candidates.append(Path(value["caminho"]).expanduser())
    if relative and not inside:
        candidates.append((ROOT / relative).resolve())
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else None


def from_other_system(value):
    """O caminho salvo é de outro sistema (ex.: /home/... visto no Windows) e fica fora do hub?

    Pastas assim são ignoradas em silêncio: elas existem no outro computador,
    não aqui, então não faz sentido avisar "pasta não encontrada".
    """
    if not isinstance(value, dict):
        return False
    relative = value.get("relativo")
    if relative and Path(relative).parts[:1] != ("..",):
        return False  # está dentro do hub: vale em qualquer sistema
    absolute = str(value.get("caminho") or "")
    windows_style = len(absolute) > 2 and absolute[1] == ":" and absolute[2] in "\\/"
    return windows_style != IS_WINDOWS if absolute else False


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
