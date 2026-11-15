"""Pequenos utilitários de interface."""
import threading
from pathlib import Path

from gi.repository import Gio, GLib, Gtk


def icon_button(icon_name, tooltip, callback, css="flat-icon"):
    """Cria um botão só com ícone."""
    button = Gtk.Button.new_from_icon_name(icon_name)
    button.add_css_class(css)
    button.set_tooltip_text(tooltip)
    button.connect("clicked", callback)
    return button


def open_folder(path):
    """Abre uma pasta no gerenciador de arquivos (criando-a se não existir)."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    Gio.AppInfo.launch_default_for_uri(path.resolve().as_uri(), None)


def run_in_thread(work, done):
    """Executa work() fora da interface e chama done(resultado, erro) na interface."""

    def runner():
        """Roda o trabalho na thread e entrega o resultado (ou o erro) para a interface."""
        try:
            result, error = work(), None
        except Exception as exception:  # noqa: BLE001 - o erro é mostrado ao usuário
            result, error = None, exception
        GLib.idle_add(lambda: done(result, error) and False)

    threading.Thread(target=runner, daemon=True).start()
