"""Integração com a área de trabalho do Linux (ícone do hub e atalho no menu).

Tudo aqui é refeito a cada abertura do hub, então continua certo mesmo depois
de mover/copiar a pasta ou levar para outro computador:
- o arquivo "Abrir My Games (Linux)" ganha o ícone do controle no gerenciador de arquivos;
- o ícone é instalado no tema do usuário (janela e dock mostram o controle);
- se o atalho do menu já existir, o caminho dele é atualizado.

Nada disso é necessário para o hub funcionar: qualquer erro é ignorado.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .paths import APP_ICON, ROOT

APP_ID = "com.mygames.Library"  # mesmo ID do Gtk.Application: liga a janela ao ícone
LAUNCHER = ROOT / "Abrir My Games (Linux)"
OLD_SHORTCUTS = ("my-games.desktop", "My Games.desktop")  # nomes usados por versões antigas


def _data_home():
    """Pasta de dados do usuário (~/.local/share)."""
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def _shortcut_path():
    """Onde fica o atalho do My Games no menu de aplicativos."""
    return _data_home() / "applications" / f"{APP_ID}.desktop"


ICON_SIZES = (48, 64, 96, 128, 256, 512)  # tamanhos que o dock, o menu e a janela procuram


def _refresh_icon_cache(theme):
    """Reconstrói o índice de ícones do usuário.

    Se existe um icon-theme.cache antigo, o GNOME confia nele e não enxerga
    ícones novos (o dock mostrava um ícone genérico por causa disso).
    """
    if not (theme / "icon-theme.cache").exists():
        return  # sem índice o GNOME procura nas pastas direto
    tool = shutil.which("gtk-update-icon-cache") or shutil.which("gtk4-update-icon-cache")
    if tool:
        subprocess.run([tool, "-f", "-t", "-q", str(theme)], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        (theme / "icon-theme.cache").unlink(missing_ok=True)  # índice velho é pior que nenhum


def install_icon():
    """Instala o ícone do controle no tema do usuário (nome: my-games), em vários tamanhos."""
    theme = _data_home() / "icons" / "hicolor"
    changed = False
    source = None
    for size in ICON_SIZES:
        target = theme / f"{size}x{size}" / "apps" / "my-games.png"
        if target.exists() and target.stat().st_mtime >= APP_ICON.stat().st_mtime:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if size == 512:
            shutil.copyfile(APP_ICON, target)
        else:
            import gi

            gi.require_version("GdkPixbuf", "2.0")
            from gi.repository import GdkPixbuf

            source = source or GdkPixbuf.Pixbuf.new_from_file(str(APP_ICON))
            source.scale_simple(size, size, GdkPixbuf.InterpType.HYPER).savev(str(target), "png", [], [])
        changed = True
    cache = theme / "icon-theme.cache"
    if changed or (cache.exists() and cache.stat().st_mtime < (theme / "512x512" / "apps").stat().st_mtime):
        _refresh_icon_cache(theme)
    return theme / "512x512" / "apps" / "my-games.png"


def set_launcher_icon():
    """Mostra o ícone do controle no arquivo "Abrir My Games (Linux)" (Nautilus, Nemo, Caja).

    No Linux um executável não carrega ícone dentro de si; o gerenciador de
    arquivos usa este atributo (guardado pelo GVfs neste computador).
    """
    from gi.repository import Gio

    if not LAUNCHER.exists():
        return
    launcher = Gio.File.new_for_path(str(LAUNCHER))
    icon_uri = Gio.File.new_for_path(str(APP_ICON)).get_uri()
    info = launcher.query_info("metadata::custom-icon", Gio.FileQueryInfoFlags.NONE, None)
    if info.get_attribute_string("metadata::custom-icon") != icon_uri:
        launcher.set_attribute_string("metadata::custom-icon", icon_uri, Gio.FileQueryInfoFlags.NONE, None)


def _write_shortcut(path):
    """Escreve o arquivo .desktop do atalho, apontando para o local atual do hub."""
    launcher = str(LAUNCHER).replace('"', '\\"')
    text = (
        "[Desktop Entry]\n"
        "Version=1.0\n"
        "Type=Application\n"
        "Name=My Games\n"
        "Comment=Hub portátil de emulação\n"
        f'Exec="{launcher}"\n'
        "Icon=my-games\n"
        "Terminal=false\n"
        "Categories=Game;Emulator;\n"
        f"StartupWMClass={APP_ID}\n"
        "StartupNotify=true\n"
    )
    if path.exists() and path.read_text(encoding="utf-8", errors="replace") == text:
        return  # já está certo: não mexe (evita o GNOME pedir confiança de novo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def install_menu_shortcut():
    """Cria (ou atualiza) o atalho do My Games no menu de aplicativos. Usado pelo botão nas Configurações."""
    install_icon()
    shortcut = _shortcut_path()
    _write_shortcut(shortcut)
    # Remove atalhos com nomes antigos para não aparecer "My Games" duas vezes no menu.
    for name in OLD_SHORTCUTS:
        old = shortcut.parent / name
        if old.exists() and "My Games" in old.read_text(encoding="utf-8", errors="replace"):
            old.unlink()
    return shortcut


def _desktop_folder():
    """Pasta da Área de trabalho do usuário (o nome muda conforme o idioma)."""
    try:
        from gi.repository import GLib

        folder = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DESKTOP)
        if folder:
            return Path(folder)
    except Exception:  # noqa: BLE001
        pass
    return Path.home() / "Desktop"


def update_desktop_shortcut():
    """Se existe um atalho "My Games" na Área de trabalho, deixa ele igual ao do menu.

    Assim, aberto por ele, o hub também aparece no dock com o ícone do controle.
    """
    shortcut = _desktop_folder() / "My Games.desktop"
    if not shortcut.exists():
        return
    current = shortcut.read_text(encoding="utf-8", errors="replace")
    if "My Games" not in current:
        return
    _write_shortcut(shortcut)


def sync_desktop_integration():
    """Chamado a cada abertura do hub (numa thread): ícones e atalho sempre atualizados."""
    if not sys.platform.startswith("linux"):
        return
    for step in (install_icon, set_launcher_icon):
        try:
            step()
        except Exception as error:  # noqa: BLE001 - integração é opcional
            print(f"Aviso (ícone): {error}", file=sys.stderr)
    try:
        update_desktop_shortcut()
    except Exception as error:  # noqa: BLE001
        print(f"Aviso (atalho da área de trabalho): {error}", file=sys.stderr)
    try:
        folder = _shortcut_path().parent
        existing = [folder / name for name in (f"{APP_ID}.desktop",) + OLD_SHORTCUTS if (folder / name).exists()]
        if existing:  # só atualiza se o usuário já tinha criado o atalho
            install_menu_shortcut()
    except Exception as error:  # noqa: BLE001
        print(f"Aviso (atalho): {error}", file=sys.stderr)
