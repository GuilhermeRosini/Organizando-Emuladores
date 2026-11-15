"""Cria o atalho do My Games no menu de aplicativos do computador atual."""
import os
import shutil
from pathlib import Path

from .paths import APP_ICON, ROOT

LAUNCHER = ROOT / "Abrir My Games"


def install_menu_shortcut():
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    icon_dir = data_home / "icons" / "hicolor" / "512x512" / "apps"
    icon_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(APP_ICON, icon_dir / "my-games.png")

    desktop = data_home / "applications" / "my-games.desktop"
    desktop.parent.mkdir(parents=True, exist_ok=True)
    launcher = str(LAUNCHER).replace('"', '\\"')
    desktop.write_text(
        "[Desktop Entry]\n"
        "Version=1.0\n"
        "Type=Application\n"
        "Name=My Games\n"
        "Comment=Hub portátil de emulação\n"
        f'Exec="{launcher}"\n'
        "Icon=my-games\n"
        "Terminal=false\n"
        "Categories=Game;Emulator;\n"
        "StartupWMClass=com.mygames.Library\n"
        "StartupNotify=true\n",
        encoding="utf-8",
    )
    desktop.chmod(0o755)
    return desktop
