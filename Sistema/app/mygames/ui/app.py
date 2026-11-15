"""Janela principal do My Games."""
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path

from gi.repository import Gdk, Gio, GLib, Gtk

from .. import emulators
from ..config import Config, ConfigError
from ..consoles import CONSOLE_LIST
from ..gamepad import Gamepads
from ..library import scan_console
from ..paths import APP_ICON, display_path
from .carousel import ConsoleCarousel
from .library_page import LibraryPage
from .settings_window import SettingsWindow
from .widgets import icon_button, run_in_thread

STYLE = Path(__file__).with_name("style.css")


@dataclass
class EmulatorState:
    emulator: object
    ready: bool
    label: str
    css: str


class MyGamesApp:
    def __init__(self, application):
        self.application = application
        self.window = None
        self.settings_window = None
        self.process = None
        self.process_console = None
        self.exit_requested = False
        self.process_started = 0
        self.log_path = None
        self.toast_source = 0
        self.game_counts = {}
        self.current = 0
        try:
            self.config = Config()
            self.config_error = None
        except ConfigError as error:
            self.config = Config.__new__(Config)
            self.config.data = {"tela_cheia": True, "consoles": {}}
            self.config_error = str(error)

    # ---- Construção ----------------------------------------------------------
    def activate(self):
        if self.window is not None:
            self.window.present()
            return
        provider = Gtk.CssProvider()
        provider.load_from_path(str(STYLE))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.window = Gtk.ApplicationWindow(application=self.application, title="My Games")
        self.window.add_css_class("mygames")
        self.window.set_icon_name("my-games")
        self.window.set_default_size(1240, 780)
        self.window.set_size_request(900, 600)
        self.window.set_titlebar(self._build_header())

        overlay = Gtk.Overlay()
        self.window.set_child(overlay)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_transition_duration(420)
        overlay.set_child(self.stack)
        self.stack.add_named(self._build_home(), "home")
        self.library = LibraryPage(self)
        self.stack.add_named(self.library, "library")
        overlay.add_overlay(self._build_toast())

        keys = Gtk.EventControllerKey.new()
        keys.connect("key-pressed", self._on_key)
        self.window.add_controller(keys)

        self.gamepads = Gamepads(
            on_move=self._pad_move,
            on_confirm=self._pad_confirm,
            on_back=self._pad_back,
            on_menu=lambda: self.show_settings(),
            is_enabled=self._accepts_input,
            on_change=lambda connected: self.pad_icon.set_visible(connected),
            on_exit_combo=self._exit_game_from_pad,
        )
        self.pad_icon.set_visible(self.gamepads.connected)

        self.window.present()
        self.carousel.select(0)
        self.carousel.grab_focus()
        self._show_console_info(0)
        self._count_all_games()
        if self.config_error:
            self.toast(self.config_error, error=True)

    def _build_header(self):
        header = Gtk.HeaderBar()
        brand = Gtk.Box(spacing=10)
        if APP_ICON.is_file():
            icon = Gtk.Image.new_from_file(str(APP_ICON))
            icon.set_pixel_size(30)
            brand.append(icon)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        title = Gtk.Label(label="My Games", xalign=0)
        title.add_css_class("app-title")
        subtitle = Gtk.Label(label="HUB DE EMULAÇÃO", xalign=0)
        subtitle.add_css_class("app-subtitle")
        names.append(title)
        names.append(subtitle)
        brand.append(names)
        header.set_title_widget(brand)

        header.pack_end(icon_button("emblem-system-symbolic", "Configurações", lambda *_: self.show_settings()))
        self.pad_icon = Gtk.Image.new_from_icon_name("input-gaming-symbolic")
        self.pad_icon.set_tooltip_text("Controle conectado")
        self.pad_icon.set_opacity(0.8)
        header.pack_end(self.pad_icon)
        return header

    def _build_home(self):
        home = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        home.set_margin_bottom(26)
        self.carousel = ConsoleCarousel(CONSOLE_LIST, self._show_console_info, self.open_library)
        home.append(self.carousel)

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, halign=Gtk.Align.CENTER)
        self.hero_title = Gtk.Label()
        self.hero_title.add_css_class("hero-title")
        hero.append(self.hero_title)
        self.hero_meta = Gtk.Label()
        self.hero_meta.add_css_class("hero-meta")
        hero.append(self.hero_meta)
        chips = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        chips.set_margin_top(4)
        self.emulator_chip = Gtk.Label()
        self.emulator_chip.add_css_class("chip")
        self.games_chip = Gtk.Label()
        self.games_chip.add_css_class("chip")
        chips.append(self.emulator_chip)
        chips.append(self.games_chip)
        hero.append(chips)

        hints = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        hints.set_margin_top(14)
        for key, text in (("←  →", "navegar"), ("Enter / A", "abrir"), ("SELECT + START", "sair do jogo")):
            key_label = Gtk.Label(label=key)
            key_label.add_css_class("key")
            hints.append(key_label)
            hint = Gtk.Label(label=text)
            hint.add_css_class("hint")
            hint.set_margin_end(12)
            hints.append(hint)
        hero.append(hints)
        home.append(hero)
        return home

    def _build_toast(self):
        self.toast_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.END,
        )
        self.toast_revealer.set_margin_bottom(22)
        self.toast_revealer.set_can_target(False)
        self.toast_box = Gtk.Box()
        self.toast_box.add_css_class("toast")
        self.toast_label = Gtk.Label(wrap=True, max_width_chars=90)
        self.toast_box.append(self.toast_label)
        self.toast_revealer.set_child(self.toast_box)
        return self.toast_revealer

    # ---- Estado dos consoles -------------------------------------------------
    def emulator_state(self, console):
        try:
            emulator = emulators.find_emulator(console, self.config)
        except OSError as error:
            return EmulatorState(None, False, f"● Erro: {error}", "danger")
        if emulator is None:
            return EmulatorState(None, False, "● Sem emulador", "warn")
        problem = emulators.emulator_problem(emulator)
        if problem:
            return EmulatorState(emulator, False, "● Emulador não encontrado", "danger")
        return EmulatorState(emulator, True, f"● {emulator.name} pronto", "ok")

    def scan(self, console, config=None):
        return scan_console(console, config or self.config)

    def _count_all_games(self):
        config = self.config

        def work():
            return {console.id: len(scan_console(console, config).games) for console in CONSOLE_LIST}

        def done(counts, _error):
            if counts:
                self.game_counts.update(counts)
                self._show_console_info(self.current)

        run_in_thread(work, done)

    def update_game_count(self, console, count):
        self.game_counts[console.id] = count
        if CONSOLE_LIST[self.current].id == console.id:
            self._show_console_info(self.current)

    def refresh_console(self, console):
        self._show_console_info(self.current)
        if self.stack.get_visible_child_name() == "library" and self.library.console is console:
            self.library.refresh_header()
            self.library.reload()

    def _show_console_info(self, index):
        self.current = index
        console = CONSOLE_LIST[index]
        self.hero_title.set_text(console.name)
        self.hero_meta.set_text(f"{console.maker.upper()}  ·  {console.year}")
        state = self.emulator_state(console)
        self.emulator_chip.set_text(state.label)
        for css in ("ok", "warn", "danger"):
            self.emulator_chip.remove_css_class(css)
        self.emulator_chip.add_css_class(state.css)
        count = self.game_counts.get(console.id)
        if count is None:
            self.games_chip.set_text("Contando jogos…")
        else:
            self.games_chip.set_text(f"{count} jogo{'s' if count != 1 else ''}")

    # ---- Navegação -----------------------------------------------------------
    def open_library(self, index=None):
        if index is not None:
            self.current = index
        self.library.show_console(CONSOLE_LIST[self.current])
        self.stack.set_visible_child_name("library")

    def show_home(self):
        self.stack.set_visible_child_name("home")
        self._show_console_info(self.current)
        self.carousel.grab_focus()

    def show_settings(self, console_id=None):
        if self.settings_window is not None:
            self.settings_window.present()
            return
        self.settings_window = SettingsWindow(self, console_id)
        self.settings_window.connect("close-request", self._on_settings_closed)
        self.settings_window.present()

    def _on_settings_closed(self, _window):
        self.settings_window = None
        self._show_console_info(self.current)
        self._count_all_games()
        if self.stack.get_visible_child_name() == "library":
            self.library.refresh_header()
            self.library.reload()
        return False

    def _on_key(self, _controller, keyval, _keycode, _state):
        page = self.stack.get_visible_child_name()
        if page == "home":
            if keyval in (Gdk.KEY_Left, Gdk.KEY_a):
                self.carousel.move(-1)
            elif keyval in (Gdk.KEY_Right, Gdk.KEY_d):
                self.carousel.move(1)
            elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
                self.open_library()
            else:
                return False
            return True
        if keyval == Gdk.KEY_Escape or (keyval == Gdk.KEY_BackSpace and not self.library.search.get_text()):
            self.show_home()
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            self.library.move(1 if keyval == Gdk.KEY_Down else -1)
            return True
        return False

    def _accepts_input(self):
        return (
            self.window is not None
            and self.window.get_visible()
            and self.window.is_active()
            and self.settings_window is None
        )

    def _pad_move(self, dx, dy):
        if self.stack.get_visible_child_name() == "home":
            if dx:
                self.carousel.move(dx)
        elif dy:
            self.library.move(dy)

    def _pad_confirm(self):
        if self.stack.get_visible_child_name() == "home":
            self.open_library()
        else:
            self.library.activate_selected()

    def _pad_back(self):
        if self.stack.get_visible_child_name() == "library":
            self.show_home()

    # ---- Emulador ------------------------------------------------------------
    def launch(self, console, game):
        if self.process is not None and self.process.poll() is None:
            self.toast("Um emulador já está aberto. Feche-o para abrir outro jogo.", error=True)
            return
        state = self.emulator_state(console)
        if not state.ready:
            problem = emulators.emulator_problem(state.emulator) if state.emulator else None
            self.toast(
                (problem + " " if problem else f"{console.name} ainda não tem emulador. ")
                + "Abra as configurações (⚙) para baixar ou escolher um.",
                error=True,
            )
            return
        bios_problem = emulators.missing_bios(console, state.emulator)
        if bios_problem and game:
            self.toast(bios_problem, error=True, seconds=10)
            return
        try:
            self.process, self.log_path = emulators.launch(
                console,
                state.emulator,
                game,
                self.config.fullscreen,
                self.config.custom_arguments(console.id),
            )
        except (OSError, ValueError, GLib.Error) as error:
            self.toast(f"Não foi possível abrir o {state.emulator.name}: {error}", error=True)
            return
        self.process_console = console
        self.process_started = time.monotonic()
        name = Path(game).stem if game else console.name
        self.toast(f"Abrindo {name} no {state.emulator.name}…")
        GLib.timeout_add(500, self._watch_process)
        GLib.timeout_add(1500, self._hide_while_playing)

    def _exit_game_from_pad(self):
        if self.process is None or self.process.poll() is not None:
            return
        self.toast("Saindo do jogo…")
        self.exit_requested = True
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except OSError:
            return
        process = self.process
        # Se o emulador não fechar sozinho em 5 segundos, força.
        GLib.timeout_add_seconds(5, lambda: (process.poll() is None and _kill(process)) and False)

    def _hide_while_playing(self):
        if self.process is not None and self.process.poll() is None:
            self.window.set_visible(False)
        return GLib.SOURCE_REMOVE

    def _watch_process(self):
        if self.process is None:
            return GLib.SOURCE_REMOVE
        code = self.process.poll()
        if code is None:
            return GLib.SOURCE_CONTINUE
        self.process = None
        self.window.set_visible(True)
        self.window.present()
        requested, self.exit_requested = self.exit_requested, False
        if code != 0 and not requested:
            detail = _last_log_line(self.log_path)
            self.toast(
                f"O emulador fechou com erro (código {code}). "
                + (f"“{detail}” " if detail else "")
                + f"Detalhes em {display_path(self.log_path)}",
                error=True,
                seconds=9,
            )
        else:
            self.toast("Bem-vindo de volta!")
        return GLib.SOURCE_REMOVE

    # ---- Avisos --------------------------------------------------------------
    def toast(self, message, error=False, seconds=None):
        self.toast_label.set_text(message)
        (self.toast_box.add_css_class if error else self.toast_box.remove_css_class)("error")
        self.toast_revealer.set_reveal_child(True)
        if self.toast_source:
            GLib.source_remove(self.toast_source)
        self.toast_source = GLib.timeout_add_seconds(seconds or (7 if error else 4), self._hide_toast)

    def _hide_toast(self):
        self.toast_revealer.set_reveal_child(False)
        self.toast_source = 0
        return GLib.SOURCE_REMOVE


def _kill(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass
    return False


def _last_log_line(path):
    try:
        lines = [line.strip() for line in Path(path).read_text(errors="replace").splitlines()[1:] if line.strip()]
    except (OSError, TypeError):
        return ""
    return lines[-1][:140] if lines else ""


def run():
    application = Gtk.Application(application_id="com.mygames.Library", flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
    app = MyGamesApp(application)
    application.connect("activate", lambda *_: app.activate())
    return application.run(None)
