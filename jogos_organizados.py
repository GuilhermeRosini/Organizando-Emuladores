#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("GioUnix", "2.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GioUnix, GLib, Gtk, Pango


ROOT = Path(__file__).resolve().parent
CONSOLES = {
    "ps2": {
        "name": "PlayStation 2",
        "folder": "ps2",
        "image": "console.png",
    },
    "super-nintendo": {
        "name": "Super Nintendo",
        "folder": "super nintendo",
        "image": "console.png",
    },
    "gameboy-advance": {
        "name": "Game Boy Advance",
        "folder": "gameboy advance",
        "image": "console.png",
    },
    "gamecube": {
        "name": "GameCube",
        "folder": "gamecube",
        "image": "console.png",
    },
    "nintendo-64": {
        "name": "Nintendo 64",
        "folder": "nintendo 64",
        "image": "console.png",
    },
}
EMULATOR_CONFIG = ROOT / "emulators.json"
GAMES_CONFIG = ROOT / "games.json"
GAME_FILE_EXTENSIONS = {
    ".3ds", ".7z", ".a26", ".a52", ".a78", ".bin", ".cdi", ".cia", ".ciso",
    ".chd", ".cue", ".cso", ".d64", ".d88", ".dol", ".elf", ".gcm", ".gdi",
    ".gcz", ".gba", ".gbc", ".gb", ".gen", ".gg", ".iso", ".md", ".m3u",
    ".n64", ".nds", ".nes", ".nro", ".nsp", ".pbp", ".pce", ".pkg", ".rom",
    ".rvz", ".sfc", ".smc", ".sms", ".v64", ".wad", ".wbfs", ".wia", ".wud",
    ".wux", ".xci", ".z64", ".zip", ".rar",
}
CAROUSEL_COPIES = 7
CENTER_COPY = CAROUSEL_COPIES // 2


def console_path(console_id):
    console = CONSOLES.get(console_id)
    if console is None:
        raise ValueError("Console desconhecido.")
    path = (ROOT / console["folder"]).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError("Pasta do console inválida.")
    return path


def flatpak_app_id_from_data_folder(folder):
    flatpak_data_root = (Path.home() / ".var" / "app").resolve()
    try:
        relative_folder = Path(folder).resolve().relative_to(flatpak_data_root)
    except ValueError:
        return None
    if len(relative_folder.parts) != 1:
        return None
    return relative_folder.parts[0]


def emulator_launcher_in_folder(folder):
    folder = Path(folder)
    search_directories = (
        folder,
        folder / "bin",
        folder / "usr" / "bin",
        folder / "usr" / "share" / "applications",
    )
    candidates = []
    for directory in search_directories:
        if not directory.is_dir():
            continue
        try:
            entries = directory.iterdir()
            for entry in entries:
                if not entry.is_file():
                    continue
                is_desktop_launcher = entry.suffix.casefold() == ".desktop"
                if is_desktop_launcher or os.access(entry, os.X_OK):
                    candidates.append(entry.resolve())
        except OSError as error:
            raise ValueError(f"Não foi possível ler a pasta do emulador: {error}") from error

    candidates = list(dict.fromkeys(candidates))
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        matching = [
            candidate
            for candidate in candidates
            if candidate.stem.casefold() == folder.name.casefold()
        ]
        if len(matching) == 1:
            return matching[0]
        raise ValueError(
            "A pasta contém mais de um programa executável. Escolha a pasta "
            "do emulador que contenha apenas o inicializador dele."
        )
    raise ValueError(
        "Não encontrei um inicializador na pasta. Escolha a pasta de dados "
        "do Flatpak ou uma pasta com o executável/AppImage do emulador."
    )


def configured_emulator_path(value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def emulator_config_value(path):
    resolved_path = Path(path).resolve()
    try:
        return str(resolved_path.relative_to(ROOT))
    except ValueError:
        return str(resolved_path)


def flatpak_command_for_app(app_id):
    flatpak_command = shutil.which("flatpak")
    if flatpak_command is None:
        raise ValueError("O comando Flatpak não está instalado neste computador.")
    try:
        result = subprocess.run(
            [flatpak_command, "info", "--show-ref", app_id],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("O Flatpak demorou demais para verificar o emulador.") from error
    if result.returncode != 0:
        raise ValueError(
            f"O aplicativo Flatpak '{app_id}' não está instalado ou não está disponível."
        )
    return flatpak_command


def load_emulators():
    if not EMULATOR_CONFIG.exists():
        return {}
    with EMULATOR_CONFIG.open(encoding="utf-8") as config_file:
        settings = json.load(config_file)
    if not isinstance(settings, dict):
        raise ValueError("emulators.json precisa conter um objeto JSON.")

    emulators = {}
    for console_id, executable in settings.items():
        if console_id not in CONSOLES:
            raise ValueError(f"Console desconhecido na configuração de emuladores: {console_id}")
        # Aceita também a configuração antiga em forma de lista e usa o caminho do executável.
        if isinstance(executable, list) and executable:
            executable = executable[0]
        if not isinstance(executable, str) or not executable:
            raise ValueError(f"O emulador de {console_id} precisa indicar um arquivo executável.")
        emulators[console_id] = executable
    return emulators


def load_game_folders():
    if not GAMES_CONFIG.exists():
        return {}
    with GAMES_CONFIG.open(encoding="utf-8") as config_file:
        settings = json.load(config_file)
    if not isinstance(settings, dict):
        raise ValueError("games.json precisa conter um objeto JSON.")

    game_folders = {}
    for console_id, folder in settings.items():
        if console_id not in CONSOLES:
            raise ValueError(f"Console desconhecido na configuração de jogos: {console_id}")
        if not isinstance(folder, str) or not folder:
            raise ValueError(f"A pasta de jogos de {console_id} não é válida.")
        game_folders[console_id] = folder
    return game_folders


def save_json_config(config_path, settings):
    # Salva primeiro em um arquivo temporário para não perder a configuração se a gravação falhar.
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=config_path.parent,
            prefix=f".{config_path.stem}-",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(settings, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.write("\n")
        os.replace(temporary_path, config_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def save_emulators(emulators):
    save_json_config(EMULATOR_CONFIG, emulators)


def save_game_folders(game_folders):
    save_json_config(GAMES_CONFIG, game_folders)


def games_in_folder(folder):
    folder_path = Path(folder).expanduser().resolve()
    if not folder_path.is_dir():
        raise ValueError("A pasta de jogos selecionada não existe mais.")
    try:
        return sorted(
            (
                path
                for path in folder_path.rglob("*")
                if path.is_file()
                and not any(part.startswith(".") for part in path.relative_to(folder_path).parts)
                and path.suffix.casefold() in GAME_FILE_EXTENSIONS
            ),
            key=lambda path: path.relative_to(folder_path).as_posix().casefold(),
        )
    except OSError as error:
        raise ValueError(f"Não foi possível ler a pasta de jogos: {error}") from error


CSS = b"""
window {
  background-image: linear-gradient(125deg, #281044 0%, #6326a3 52%, #32115f 100%);
  color: #f4f4f5;
  font-family: Sans;
}
headerbar {
  background-color: transparent;
  background-image: none;
  border: none;
  box-shadow: none;
  min-height: 48px;
}
headerbar windowcontrols button,
headerbar button.titlebutton {
  background-color: transparent;
  background-image: none;
  border: none;
  outline: none;
  box-shadow: none;
  min-width: 32px;
  min-height: 32px;
  padding: 6px;
}
headerbar windowcontrols button:hover,
headerbar button.titlebutton:hover {
  background-color: rgba(255, 255, 255, 0.14);
  border: none;
  outline: none;
  box-shadow: none;
}
label { color: #ffffff; }
.app-title { font-size: 23px; font-weight: bold; }
.settings-window {
  background-image: linear-gradient(145deg, #281044 0%, #512080 100%);
}
button {
  color: #ffffff;
  background-color: rgba(255, 255, 255, 0.1);
  background-image: none;
  border: 1px solid rgba(255, 255, 255, 0.28);
  border-radius: 12px;
  padding: 10px 14px;
}
button:hover {
  background-color: rgba(255, 255, 255, 0.2);
  border-color: rgba(255, 255, 255, 0.8);
}
.settings-button {
  background-color: transparent;
  background-image: none;
  border: 1px solid transparent;
  border-radius: 50%;
  outline: none;
  box-shadow: none;
  min-width: 28px;
  min-height: 28px;
  padding: 2px;
}
.settings-button:hover {
  background-color: transparent;
  background-image: none;
  border-color: #ffffff;
  outline: none;
  box-shadow: 0 0 5px rgba(255, 255, 255, 0.75);
}
.settings-button:focus:not(:hover),
.settings-button:active:not(:hover) {
  background-color: transparent;
  background-image: none;
  border-color: transparent;
  outline: none;
  box-shadow: none;
}
.carousel {
  background-color: transparent;
  background-image: none;
  border: 0;
  box-shadow: none;
}
.carousel viewport {
  background-color: transparent;
  background-image: none;
}
.console-card {
  background-color: transparent;
  background-image: none;
  border: none;
  outline: none;
  box-shadow: none;
  min-width: 270px;
  min-height: 360px;
  padding: 12px;
  opacity: 0.74;
  transition: 180ms ease;
}
.console-card.selected {
  background-color: transparent;
  border: none;
  box-shadow: none;
  opacity: 1;
  transform: scale(1.04);
}
.console-card:hover {
  background-color: transparent;
  border-color: transparent;
  box-shadow: none;
  transform: scale(1.07);
}
.console-card.selected:hover {
  box-shadow: none;
  transform: scale(1.1);
}
.console-artwork, .console-outline {
  background-color: transparent;
  background-image: none;
  border: none;
  box-shadow: none;
}
.console-outline {
  transition: opacity 180ms ease;
}
.settings-heading { font-size: 20px; font-weight: bold; }
.settings-description { color: rgba(255, 255, 255, 0.72); }
.console-setting {
  background-color: rgba(255, 255, 255, 0.075);
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 12px;
  padding: 12px;
}
.emulator-path { color: rgba(255, 255, 255, 0.72); font-size: 12px; }
.settings-status { color: #ffffff; }
.game-row {
  background-color: transparent;
  background-image: none;
  border: none;
  border-radius: 0;
  outline: none;
  box-shadow: none;
  padding: 12px 16px;
}
.game-row:hover,
.game-row:focus,
.game-row:active {
  background-color: transparent;
  background-image: none;
  border: none;
  outline: none;
  box-shadow: none;
}
.game-name {
  color: #d7c9eb;
  font-size: 17px;
  font-weight: bold;
  transition: color 180ms ease, font-size 180ms ease;
}
.game-name-hovered {
  color: #ffffff;
  font-size: 19px;
}
.game-path {
  color: rgba(255, 255, 255, 0.62);
  font-size: 13px;
  transition: color 180ms ease;
}
.game-path-hovered { color: #ffffff; }
.library-console-art {
  background-color: transparent;
  background-image: none;
  border: none;
  box-shadow: none;
}
.library-game-panel {
  background-color: transparent;
  background-image: none;
}
.library-back-button {
  min-width: 42px;
  min-height: 42px;
  padding: 8px;
}
scrolledwindow, viewport, list, row {
  background-color: transparent;
  background-image: none;
  border: none;
  box-shadow: none;
}
"""


class MyGamesApp:
    def __init__(self, application):
        self.application = application
        self.window = None
        self.consoles = list(CONSOLES.items())
        self.selected_index = 0
        self.selected_copy = CENTER_COPY
        self.cards = []
        self.outline_images = []
        self.emulators = {}
        self.emulator_process = None
        self.emulator_pid = None
        self.wheel_locked = False
        self.drag_start_value = 0
        self.drag_moved = False
        self.carousel_animation_source = 0
        self.carousel_animation_started = 0
        self.carousel_animation_from = 0
        self.carousel_animation_target = 0
        self.carousel_center_source = 0
        self.settings_window = None
        self.settings_status = None
        self.main_stack = None
        self.library_console_id = None
        self.library_game_list = None
        self.library_heading = None
        self.library_art = None
        self.emulator_path_labels = {}
        self.game_path_labels = {}
        self.status_timeout = 0

    def activate(self):
        if self.window is not None:
            self.window.present()
            return

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.window = Gtk.ApplicationWindow(
            application=self.application, title="My Games"
        )
        self.window.set_icon_name("my-games")
        self.window.set_default_size(1200, 760)
        self.window.set_size_request(800, 570)

        titlebar = Gtk.HeaderBar()
        titlebar.set_show_title_buttons(True)
        title_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        icon_path = ROOT / "my-games.png"
        if icon_path.is_file():
            icon = Gtk.Image.new_from_file(str(icon_path))
            icon.set_pixel_size(32)
            title_content.append(icon)
        title = Gtk.Label(label="My Games")
        title.add_css_class("app-title")
        title_content.append(title)
        titlebar.set_title_widget(title_content)

        # A barra integrada usa o fundo roxo da janela e mantém os controles sem contorno.
        self.window.set_titlebar(titlebar)

        self._build_window()
        self.window.present()
        self.select_console(0)

    def _build_window(self):
        window = self.window
        if window is None:
            raise RuntimeError("A janela principal ainda não foi criada.")

        # A tela principal fica propositalmente limpa: nome do app, configurações e consoles.
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.set_margin_top(18)
        root.set_margin_bottom(22)
        root.set_margin_start(28)
        root.set_margin_end(28)
        window.set_child(root)

        # Este botão abre as configurações de emuladores e pastas de jogos.
        settings_button = Gtk.Button.new_from_icon_name("preferences-system-symbolic")
        settings_button.add_css_class("settings-button")
        settings_button.set_has_frame(False)
        settings_icon = settings_button.get_child()
        if isinstance(settings_icon, Gtk.Image):
            settings_icon.set_pixel_size(18)
        settings_button.set_tooltip_text("Configurar emuladores")
        settings_button.connect("clicked", lambda _button: self._show_emulator_settings())
        titlebar = window.get_titlebar()
        if not isinstance(titlebar, Gtk.HeaderBar):
            raise RuntimeError("A barra integrada do aplicativo não foi inicializada.")
        titlebar.pack_end(settings_button)

        stage = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        stage.set_hexpand(True)
        stage.set_vexpand(True)
        stage.set_valign(Gtk.Align.CENTER)
        root.append(stage)

        self.main_stack = Gtk.Stack()
        self.main_stack.set_hexpand(True)
        self.main_stack.set_vexpand(True)
        self.main_stack.set_hhomogeneous(False)
        self.main_stack.set_vhomogeneous(False)
        self.main_stack.set_transition_type(
            Gtk.StackTransitionType.SLIDE_LEFT_RIGHT
        )
        self.main_stack.set_transition_duration(480)
        stage.append(self.main_stack)

        # A faixa horizontal mostra os vizinhos enquanto mantém o selecionado no centro.
        self.carousel = Gtk.ScrolledWindow()
        self.carousel.add_css_class("carousel")
        self.carousel.set_hexpand(True)
        self.carousel.set_vexpand(True)
        self.carousel.set_min_content_height(430)
        self.carousel.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        self.carousel.set_kinetic_scrolling(False)
        self.track = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self.carousel.set_child(self.track)
        self.main_stack.add_named(self.carousel, "consoles")

        # Repetir os cartões mantém consoles vizinhos à vista até nas pontas do carrossel.
        for copy_index in range(CAROUSEL_COPIES):
            for console_index, (console_id, console) in enumerate(self.consoles):
                button = self._make_console_card(
                    console_id, console, copy_index == CENTER_COPY
                )
                button.connect(
                    "clicked", self._on_card_clicked, console_index, copy_index
                )
                double_click = Gtk.GestureClick.new()
                double_click.set_button(Gdk.BUTTON_PRIMARY)
                double_click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
                double_click.connect(
                    "pressed",
                    self._on_card_double_clicked,
                    console_index,
                    copy_index,
                )
                button.add_controller(double_click)
                self.track.append(button)
                self.cards.append(button)

        # Os controladores GTK nativos interpretam a roda e o gesto de arrastar.
        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.BOTH_AXES
        )
        scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll.connect("scroll", self._on_wheel)
        self.carousel.add_controller(scroll)

        drag = Gtk.GestureDrag.new()
        drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        drag.set_button(Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.carousel.add_controller(drag)
        self.carousel.set_cursor_from_name("grab")
        keys = Gtk.EventControllerKey.new()
        keys.connect("key-pressed", self._on_key_pressed)
        self.carousel.add_controller(keys)
        self.carousel.connect(
            "notify::width", lambda *_args: GLib.idle_add(self._center_selected_console)
        )

        self.main_stack.add_named(self._build_game_library_page(), "games")

        # As mensagens temporárias só aparecem quando há algo que o usuário precisa saber.
        self.status_revealer = Gtk.Revealer()
        self.status_revealer.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        self.status_label = Gtk.Label(label="", xalign=0)
        self.status_label.add_css_class("settings-status")
        self.status_revealer.set_child(self.status_label)
        self.status_revealer.set_halign(Gtk.Align.CENTER)
        root.append(self.status_revealer)

    def _make_console_card(self, console_id, console, is_center_copy):
        button = Gtk.Button()
        button.add_css_class("console-card")
        button.set_focusable(is_center_copy)
        button.set_overflow(Gtk.Overflow.VISIBLE)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_overflow(Gtk.Overflow.VISIBLE)
        image_path = console_path(console_id) / console["image"]
        if image_path.is_file():
            # A camada de brilho usa a mesma transparência da silhueta do console, não a do cartão.
            art = Gtk.Overlay()
            outline_path = image_path.parent / "console-outline.png"
            picture = Gtk.Picture.new_for_filename(str(image_path))
            picture.add_css_class("console-artwork")
            picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            picture.set_can_shrink(True)
            art.set_child(picture)

            outline = Gtk.Picture.new_for_filename(
                str(outline_path if outline_path.is_file() else image_path)
            )
            outline.add_css_class("console-outline")
            outline.set_content_fit(Gtk.ContentFit.CONTAIN)
            outline.set_can_shrink(True)
            art.add_overlay(outline)
            art.set_size_request(236, 286)
            art.set_overflow(Gtk.Overflow.VISIBLE)
            content.append(art)
            self.outline_images.append(outline)
        else:
            missing_image = Gtk.Label(label="Imagem do console indisponível")
            missing_image.set_size_request(236, 286)
            content.append(missing_image)
            self.outline_images.append(None)

        name = Gtk.Label(label=console["name"])
        name.add_css_class("console-name")
        content.append(name)
        button.set_child(content)
        return button

    def _on_card_clicked(self, _button, console_index, copy_index):
        self.select_console(
            console_index, launch_emulator=False, target_copy=copy_index
        )

    def _on_card_double_clicked(
        self, _gesture, press_count, _x, _y, console_index, copy_index
    ):
        if press_count != 2:
            return
        self.select_console(console_index, target_copy=copy_index)
        self._show_game_library(self.consoles[console_index][0])

    def _center_selected_console(self, animate=False):
        if not self.cards:
            return GLib.SOURCE_REMOVE
        card_index = self.selected_copy * len(self.consoles) + self.selected_index
        card = self.cards[card_index]
        has_bounds, bounds = card.compute_bounds(self.track)
        if not has_bounds:
            return GLib.SOURCE_REMOVE
        adjustment = self.carousel.get_hadjustment()
        target = (
            bounds.get_x()
            + bounds.get_width() / 2
            - adjustment.get_page_size() / 2
        )
        maximum = max(
            adjustment.get_lower(),
            adjustment.get_upper() - adjustment.get_page_size(),
        )
        target = min(max(target, adjustment.get_lower()), maximum)
        if animate:
            self._animate_carousel_to(target)
        else:
            self._stop_carousel_animation()
            adjustment.set_value(target)
        return GLib.SOURCE_REMOVE

    def _carousel_cycle_width(self):
        consoles_per_copy = len(self.consoles)
        unselected_console = (self.selected_index + 1) % consoles_per_copy
        first_card = self.cards[CENTER_COPY * consoles_per_copy + unselected_console]
        next_copy_card = self.cards[
            (CENTER_COPY + 1) * consoles_per_copy + unselected_console
        ]
        has_first_bounds, first_bounds = first_card.compute_bounds(self.track)
        has_next_bounds, next_bounds = next_copy_card.compute_bounds(self.track)
        if not has_first_bounds or not has_next_bounds:
            return 0
        return next_bounds.get_x() - first_bounds.get_x()

    def _recenter_selected_copy(self, copy_index):
        if 1 <= copy_index < CAROUSEL_COPIES - 1:
            return copy_index
        cycle_width = self._carousel_cycle_width()
        if cycle_width <= 0:
            return copy_index
        shift = CENTER_COPY - copy_index
        adjustment = self.carousel.get_hadjustment()
        adjustment.set_value(adjustment.get_value() + shift * cycle_width)
        return CENTER_COPY

    def _update_selected_card(self, index, copy_index):
        self.selected_index = index % len(self.consoles)
        self.selected_copy = copy_index
        selected_card_index = copy_index * len(self.consoles) + self.selected_index
        for card_index, card in enumerate(self.cards):
            is_selected = card_index == selected_card_index
            if is_selected:
                card.add_css_class("selected")
            else:
                card.remove_css_class("selected")
            outline = self.outline_images[card_index]
            if outline is not None:
                outline.set_opacity(1 if is_selected else 0)

    def _nearest_card_to_center(self):
        adjustment = self.carousel.get_hadjustment()
        center = adjustment.get_value() + adjustment.get_page_size() / 2
        closest_index = None
        closest_distance = float("inf")
        for card_index, card in enumerate(self.cards):
            has_bounds, bounds = card.compute_bounds(self.track)
            if not has_bounds:
                continue
            distance = abs(bounds.get_x() + bounds.get_width() / 2 - center)
            if distance < closest_distance:
                closest_index = card_index
                closest_distance = distance
        if closest_index is None:
            return self.selected_index, self.selected_copy
        copy_index, console_index = divmod(closest_index, len(self.consoles))
        return console_index, copy_index

    def _recenter_drag_position(self, value):
        adjustment = self.carousel.get_hadjustment()
        cycle_width = self._carousel_cycle_width()
        if cycle_width <= 0:
            return value

        lower = adjustment.get_lower()
        maximum = max(lower, adjustment.get_upper() - adjustment.get_page_size())
        while value >= maximum - cycle_width:
            value -= cycle_width
            self.drag_start_value -= cycle_width
        while value <= lower + cycle_width:
            value += cycle_width
            self.drag_start_value += cycle_width
        return min(max(value, lower), maximum)

    def _stop_carousel_animation(self):
        if self.carousel_animation_source:
            GLib.source_remove(self.carousel_animation_source)
            self.carousel_animation_source = 0

    def _animate_carousel_to(self, target):
        self._stop_carousel_animation()
        self.carousel_animation_from = self.carousel.get_hadjustment().get_value()
        self.carousel_animation_target = target
        self.carousel_animation_started = GLib.get_monotonic_time()
        self.carousel_animation_source = GLib.timeout_add(
            16, self._step_carousel_animation
        )

    def _step_carousel_animation(self):
        # A curva de desaceleração leva o console ao centro sem um salto ao soltar.
        duration = 380_000
        elapsed = GLib.get_monotonic_time() - self.carousel_animation_started
        progress = min(elapsed / duration, 1)
        eased = 1 - (1 - progress) ** 3
        value = self.carousel_animation_from + (
            self.carousel_animation_target - self.carousel_animation_from
        ) * eased
        self.carousel.get_hadjustment().set_value(value)
        if progress >= 1:
            self.carousel_animation_source = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def select_console(self, index, launch_emulator=False, target_copy=None):
        if not self.consoles:
            return
        copy_offset, console_index = divmod(index, len(self.consoles))
        target_copy = (
            self.selected_copy + copy_offset if target_copy is None else target_copy
        )
        target_copy = self._recenter_selected_copy(target_copy)
        self._stop_carousel_animation()
        if self.carousel_center_source:
            GLib.source_remove(self.carousel_center_source)
            self.carousel_center_source = 0
        self._update_selected_card(console_index, target_copy)
        self.carousel_center_source = GLib.timeout_add(60, self._center_after_selection)
        console_id = self.consoles[console_index][0]
        if launch_emulator:
            if self._has_emulator(console_id):
                self._launch_emulator(console_id)
            else:
                self._set_status(
                    f"Configure o emulador de {CONSOLES[console_id]['name']} "
                    "pelo ícone de configurações."
                )

    def _on_wheel(self, _controller, delta_x, delta_y):
        delta = delta_x if abs(delta_x) > abs(delta_y) else delta_y
        if abs(delta) < 0.05 or self.wheel_locked:
            return True
        self.wheel_locked = True
        self.select_console(
            self.selected_index + (1 if delta > 0 else -1),
        )
        GLib.timeout_add(260, self._unlock_wheel)
        return True

    def _on_key_pressed(self, _controller, keyval, _keycode, _state):
        if keyval == Gdk.KEY_Left:
            self.select_console(self.selected_index - 1)
            return True
        if keyval == Gdk.KEY_Right:
            self.select_console(self.selected_index + 1)
            return True
        return False

    def _unlock_wheel(self):
        self.wheel_locked = False
        return GLib.SOURCE_REMOVE

    def _center_after_selection(self):
        self.carousel_center_source = 0
        return self._center_selected_console(True)

    def _on_drag_begin(self, gesture, _start_x, _start_y):
        # A sequência só é reivindicada após o movimento superar o limiar de clique.
        self._stop_carousel_animation()
        if self.carousel_center_source:
            GLib.source_remove(self.carousel_center_source)
            self.carousel_center_source = 0
        self.drag_start_value = self.carousel.get_hadjustment().get_value()
        self.drag_moved = False
        self.carousel.set_cursor_from_name("grabbing")

    def _on_drag_update(self, gesture, offset_x, _offset_y):
        if abs(offset_x) < 4:
            return
        if not self.drag_moved and not gesture.set_state(Gtk.EventSequenceState.CLAIMED):
            return
        self.drag_moved = True
        adjustment = self.carousel.get_hadjustment()
        value = self.drag_start_value - offset_x
        value = self._recenter_drag_position(value)
        adjustment.set_value(value)
        selected_index, selected_copy = self._nearest_card_to_center()
        self._update_selected_card(selected_index, selected_copy)

    def _on_drag_end(self, _gesture, offset_x, _offset_y):
        if self.drag_moved:
            selected_index, selected_copy = self._nearest_card_to_center()
            self.select_console(
                selected_index,
                target_copy=selected_copy,
            )
        else:
            GLib.idle_add(self._center_selected_console, True)
        self.drag_moved = False
        self.carousel.set_cursor_from_name("grab")

    def _show_emulator_settings(self):
        if self.settings_window is not None:
            self.settings_window.present()
            return

        # Cada console permite escolher ou trocar a pasta do emulador e dos jogos.
        self.settings_window = Gtk.Window(
            application=self.application, title="Configurar emuladores"
        )
        self.settings_window.add_css_class("settings-window")
        self.settings_window.set_transient_for(self.window)
        self.settings_window.set_modal(True)
        self.settings_window.set_default_size(760, 620)
        self.settings_window.set_icon_name("my-games")
        self.settings_window.connect(
            "close-request", self._close_settings_window
        )

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_top(24)
        content.set_margin_bottom(20)
        content.set_margin_start(24)
        content.set_margin_end(24)
        self.settings_window.set_child(content)

        heading = Gtk.Label(label="Emuladores", xalign=0)
        heading.add_css_class("settings-heading")
        description = Gtk.Label(
            label=(
                "Escolha a pasta do emulador e a pasta dos jogos para cada console. "
                "A escolha do emulador fica salva e pode ser alterada quando quiser."
            ),
            xalign=0,
            wrap=True,
        )
        description.add_css_class("settings-description")
        content.append(heading)
        content.append(description)

        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for console_id, console in self.consoles:
            rows.append(self._make_settings_row(console_id, console["name"]))
        rows_scroll = Gtk.ScrolledWindow()
        rows_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        rows_scroll.set_vexpand(True)
        rows_scroll.set_child(rows)
        content.append(rows_scroll)

        self.settings_status = Gtk.Label(label="", xalign=0, wrap=True)
        self.settings_status.add_css_class("settings-status")
        content.append(self.settings_status)

        close_button = Gtk.Button(label="Concluir")
        close_button.set_halign(Gtk.Align.END)
        close_button.connect("clicked", lambda _button: self.settings_window.close())
        content.append(close_button)

        self._refresh_settings_paths()
        self.settings_window.present()

    def _close_settings_window(self, _window):
        self.settings_window = None
        self.settings_status = None
        self.emulator_path_labels.clear()
        self.game_path_labels.clear()
        return False

    def _make_settings_row(self, console_id, console_name):
        expander = Gtk.Expander(label=console_name)
        expander.add_css_class("console-setting")
        options = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        options.set_margin_top(10)

        emulator_row, emulator_path = self._make_folder_setting_row(
            "Emulador", "Selecionar emulador", "Nenhum emulador selecionado"
        )
        self.emulator_path_labels[console_id] = emulator_path
        choose_emulator = emulator_row.get_last_child()
        choose_emulator.connect(
            "clicked", self._choose_emulator_folder, console_id, console_name
        )
        remove_emulator = Gtk.Button(label="Remover")
        remove_emulator.connect("clicked", self._remove_emulator, console_id)
        emulator_row.append(remove_emulator)
        options.append(emulator_row)

        games_row, games_path = self._make_folder_setting_row(
            "Jogos", "Selecionar pasta de jogos", "Nenhuma pasta de jogos selecionada"
        )
        self.game_path_labels[console_id] = games_path
        choose_games = games_row.get_last_child()
        choose_games.connect(
            "clicked", self._choose_games_folder, console_id, console_name
        )
        remove_games = Gtk.Button(label="Remover")
        remove_games.connect("clicked", self._remove_games_folder, console_id)
        games_row.append(remove_games)
        options.append(games_row)

        expander.set_child(options)
        return expander

    def _make_folder_setting_row(self, title, button_label, empty_label):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        label = Gtk.Label(label=title, xalign=0)
        label.set_size_request(90, -1)
        row.append(label)

        path_label = Gtk.Label(label=empty_label, xalign=0)
        path_label.set_hexpand(True)
        path_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        path_label.add_css_class("emulator-path")
        row.append(path_label)

        choose_button = Gtk.Button(label=button_label)
        if button_label:
            row.append(choose_button)
        return row, path_label

    def _refresh_settings_paths(self):
        try:
            self.emulators = load_emulators()
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self._set_settings_status(f"Não foi possível ler a configuração: {error}")
            self.emulators = {}
        try:
            game_folders = load_game_folders()
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self._set_settings_status(f"Não foi possível ler a pasta de jogos: {error}")
            game_folders = {}
        for console_id, label in self.emulator_path_labels.items():
            configured = self.emulators.get(console_id)
            configured_path = configured_emulator_path(configured) if configured else None
            if configured_path:
                status = (
                    str(configured_path)
                    if configured_path.exists()
                    else f"{configured_path} (não encontrado)"
                )
                label.set_text(status)
                label.set_tooltip_text(str(configured_path))
            else:
                label.set_text("Nenhum emulador selecionado")
                label.set_tooltip_text(None)
        for console_id, label in self.game_path_labels.items():
            path = game_folders.get(console_id)
            label.set_text(path if path else "Nenhuma pasta de jogos selecionada")
            label.set_tooltip_text(path or None)

    def _choose_emulator_folder(self, _button, console_id, console_name):
        chooser = Gtk.FileChooserNative.new(
            f"Escolha o emulador para {console_name}",
            self.settings_window,
            Gtk.FileChooserAction.SELECT_FOLDER,
            "Selecionar",
            "Cancelar",
        )
        chooser.connect(
            "response", self._on_emulator_folder_chosen, console_id, console_name
        )
        chooser.show()

    def _on_emulator_folder_chosen(
        self, chooser, response, console_id, console_name
    ):
        try:
            if response != Gtk.ResponseType.ACCEPT:
                return
            selected_file = chooser.get_file()
            path = Path(selected_file.get_path()) if selected_file else None
            if path is None or not path.is_dir():
                raise ValueError("Selecione uma pasta de emulador existente.")

            app_id = flatpak_app_id_from_data_folder(path)
            if app_id:
                flatpak_command_for_app(app_id)
            else:
                launcher = emulator_launcher_in_folder(path)
                if launcher.suffix.casefold() == ".desktop":
                    if GioUnix.DesktopAppInfo.new_from_filename(str(launcher)) is None:
                        raise ValueError(
                            "O atalho .desktop encontrado na pasta não é válido."
                        )
                elif not os.access(launcher, os.X_OK):
                    raise ValueError(
                        "O inicializador encontrado na pasta não pode ser executado."
                    )

            self.emulators[console_id] = emulator_config_value(path)
            save_emulators(self.emulators)
            self._refresh_settings_paths()
            self._set_settings_status(f"Emulador de {console_name} salvo.")
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            subprocess.SubprocessError,
            GLib.Error,
        ) as error:
            self._set_settings_status(f"Não foi possível salvar: {error}")
        finally:
            chooser.destroy()

    def _choose_games_folder(self, _button, console_id, console_name):
        chooser = Gtk.FileChooserNative.new(
            f"Escolha a pasta de jogos para {console_name}",
            self.settings_window,
            Gtk.FileChooserAction.SELECT_FOLDER,
            "Selecionar",
            "Cancelar",
        )
        chooser.connect(
            "response", self._on_games_folder_chosen, console_id, console_name
        )
        chooser.show()

    def _on_games_folder_chosen(self, chooser, response, console_id, console_name):
        try:
            if response != Gtk.ResponseType.ACCEPT:
                return
            selected_file = chooser.get_file()
            path = Path(selected_file.get_path()) if selected_file else None
            if path is None or not path.is_dir():
                raise ValueError("Selecione uma pasta de jogos existente.")
            game_folders = load_game_folders()
            game_folders[console_id] = str(path.resolve())
            save_game_folders(game_folders)
            self._refresh_settings_paths()
            self._set_settings_status(f"Pasta de jogos de {console_name} salva.")
        except (OSError, ValueError, json.JSONDecodeError, GLib.Error) as error:
            self._set_settings_status(f"Não foi possível salvar: {error}")
        finally:
            chooser.destroy()

    def _remove_emulator(self, _button, console_id):
        console_name = CONSOLES[console_id]["name"]
        try:
            self.emulators = load_emulators()
            self.emulators.pop(console_id, None)
            save_emulators(self.emulators)
            self._refresh_settings_paths()
            self._set_settings_status(f"Emulador de {console_name} removido.")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self._set_settings_status(f"Não foi possível salvar: {error}")

    def _remove_games_folder(self, _button, console_id):
        console_name = CONSOLES[console_id]["name"]
        try:
            game_folders = load_game_folders()
            game_folders.pop(console_id, None)
            save_game_folders(game_folders)
            self._refresh_settings_paths()
            self._set_settings_status(f"Pasta de jogos de {console_name} removida.")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self._set_settings_status(f"Não foi possível salvar: {error}")

    def _build_game_library_page(self):
        page = Gtk.Overlay()
        page.set_margin_top(22)
        page.set_margin_bottom(20)
        page.set_margin_start(28)
        page.set_margin_end(28)

        page_content = Gtk.Box()
        page_content.set_size_request(720, 480)
        page_content.set_hexpand(True)
        page_content.set_vexpand(True)
        page.set_child(page_content)

        self.library_art = Gtk.Picture()
        self.library_art.add_css_class("library-console-art")
        self.library_art.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.library_art.set_can_shrink(True)
        self.library_art.set_halign(Gtk.Align.FILL)
        self.library_art.set_valign(Gtk.Align.FILL)
        page.add_overlay(self.library_art)
        page.set_measure_overlay(self.library_art, False)

        back_button = Gtk.Button.new_from_icon_name("go-previous-symbolic")
        back_button.add_css_class("library-back-button")
        back_button.set_halign(Gtk.Align.START)
        back_button.set_valign(Gtk.Align.START)
        back_button.set_tooltip_text("Voltar aos consoles")
        back_button.connect("clicked", lambda _button: self._return_to_consoles())
        back_button.set_margin_start(18)
        back_button.set_margin_top(18)
        page.add_overlay(back_button)
        page.set_measure_overlay(back_button, False)

        game_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        game_panel.add_css_class("library-game-panel")
        game_panel.set_size_request(820, 480)
        game_panel.set_halign(Gtk.Align.CENTER)
        game_panel.set_valign(Gtk.Align.CENTER)

        self.library_heading = Gtk.Label(label="Console", xalign=0.5)
        self.library_heading.add_css_class("settings-heading")
        game_panel.append(self.library_heading)

        self.library_game_list = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=10
        )
        games_scroll = Gtk.ScrolledWindow()
        games_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        games_scroll.set_hexpand(True)
        games_scroll.set_vexpand(True)
        games_scroll.set_child(self.library_game_list)
        game_panel.append(games_scroll)
        page.add_overlay(game_panel)
        page.set_measure_overlay(game_panel, False)
        return page

    def _show_game_library(self, console_id):
        self.library_console_id = console_id
        console = CONSOLES[console_id]
        self.library_heading.set_text(console["name"])
        image_path = console_path(console_id) / console["image"]
        if image_path.is_file():
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(image_path), 1000, 700, True
            )
            self.library_art.set_paintable(Gdk.Texture.new_for_pixbuf(pixbuf))
            self.library_art.set_opacity(0.2)
        else:
            self.library_art.set_paintable(None)
            self.library_art.set_opacity(0)

        child = self.library_game_list.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.library_game_list.remove(child)
            child = next_child

        try:
            game_folders = load_game_folders()
            games_folder = game_folders.get(console_id)
            if not games_folder:
                message = Gtk.Label(
                    label=(
                        "Nenhuma pasta de jogos selecionada. Volte aos consoles e "
                        "escolha uma pasta nas configurações da engrenagem."
                    ),
                    xalign=0,
                    wrap=True,
                )
                message.add_css_class("settings-description")
                self.library_game_list.append(message)
            else:
                games = games_in_folder(games_folder)
                if not games:
                    message = Gtk.Label(
                        label="Não encontrei arquivos de jogos compatíveis nessa pasta.",
                        xalign=0,
                        wrap=True,
                    )
                    message.add_css_class("settings-description")
                    self.library_game_list.append(message)
                else:
                    self._append_games_to_library(console_id, games_folder, games)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            message = Gtk.Label(label=str(error), xalign=0, wrap=True)
            message.add_css_class("status-error")
            self.library_game_list.append(message)
        self.main_stack.set_visible_child_name("games")

    def _append_games_to_library(self, console_id, games_folder, games):
        if not games:
            message = Gtk.Label(
                label="Não encontrei arquivos de jogos compatíveis nessa pasta.",
                xalign=0,
                wrap=True,
            )
            message.add_css_class("settings-description")
            self.library_game_list.append(message)
            return
        for game_path in games:
            self.library_game_list.append(
                self._make_game_row(console_id, games_folder, game_path)
            )

    def _make_game_row(self, console_id, games_folder, game_path):
        row = Gtk.Button()
        row.add_css_class("game-row")
        row.set_has_frame(False)
        row.set_hexpand(True)
        row.set_cursor_from_name("pointer")
        row.set_tooltip_text("Clique para iniciar este jogo")
        row.connect("clicked", self._play_game, console_id, game_path)

        game_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        game_info.set_halign(Gtk.Align.CENTER)
        label = Gtk.Label(label=game_path.name, xalign=0.5)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_tooltip_text(str(game_path.relative_to(games_folder)))
        label.add_css_class("game-name")
        label.set_opacity(0.82)
        hover = Gtk.EventControllerMotion.new()
        hover.connect("enter", self._on_game_name_hover_enter)
        hover.connect("leave", self._on_game_name_hover_leave)
        label.add_controller(hover)
        game_info.append(label)
        relative_path = game_path.parent.relative_to(games_folder)
        if relative_path != Path("."):
            path_label = Gtk.Label(label=relative_path.as_posix(), xalign=0.5)
            path_label.add_css_class("game-path")
            game_info.append(path_label)
        row.set_child(game_info)
        return row

    def _on_game_name_hover_enter(self, controller, _x, _y):
        label = controller.get_widget()
        label.add_css_class("game-name-hovered")
        label.set_opacity(1)
        path_label = label.get_next_sibling()
        if path_label is not None:
            path_label.add_css_class("game-path-hovered")

    def _on_game_name_hover_leave(self, controller):
        label = controller.get_widget()
        label.remove_css_class("game-name-hovered")
        label.set_opacity(0.82)
        path_label = label.get_next_sibling()
        if path_label is not None:
            path_label.remove_css_class("game-path-hovered")

    def _play_game(self, _button, console_id, game_path):
        self._return_to_consoles()
        self._launch_emulator(console_id, game_path)

    def _return_to_consoles(self):
        if self.main_stack is not None:
            self.main_stack.set_visible_child_name("consoles")
        self.library_console_id = None

    def _set_settings_status(self, message):
        if self.settings_status is not None:
            self.settings_status.set_text(message)

    def _has_emulator(self, console_id):
        try:
            self.emulators = load_emulators()
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self._set_status(f"Erro em emulators.json: {error}", is_error=True)
            return False
        configured = self.emulators.get(console_id)
        return bool(configured and configured_emulator_path(configured).exists())

    def _launch_emulator(self, console_id, game_path=None):
        try:
            self.emulators = load_emulators()
            executable = self.emulators.get(console_id)
            if not executable:
                raise ValueError(
                    f"Selecione primeiro a pasta do emulador de "
                    f"{CONSOLES[console_id]['name']} nas configurações."
                )
            emulator_path = configured_emulator_path(executable)
            flatpak_app_id = None
            if emulator_path.is_dir():
                flatpak_app_id = flatpak_app_id_from_data_folder(emulator_path)
                if flatpak_app_id:
                    flatpak_command = flatpak_command_for_app(flatpak_app_id)
                else:
                    emulator_path = emulator_launcher_in_folder(emulator_path)
            is_desktop_launcher = emulator_path.suffix.casefold() == ".desktop"
            if flatpak_app_id is None and (
                not emulator_path.is_file()
                or (
                    not is_desktop_launcher
                    and not os.access(emulator_path, os.X_OK)
                )
            ):
                raise ValueError(
                    f"O inicializador do emulador de {CONSOLES[console_id]['name']} "
                    "não existe mais ou não pode ser aberto. Escolha outra pasta."
                )
            if (
                self.emulator_pid is not None
                or (
                    self.emulator_process is not None
                    and self.emulator_process.poll() is None
                )
            ):
                self.window.present()
                raise ValueError("Um emulador já está aberto.")

            if flatpak_app_id:
                # O Flatpak recebe o ID do aplicativo, não o caminho da pasta de dados.
                arguments = [flatpak_command, "run"]
                if game_path:
                    arguments.extend(
                        [
                            "--file-forwarding",
                            flatpak_app_id,
                            "@@",
                            str(game_path.resolve()),
                            "@@",
                        ]
                    )
                else:
                    arguments.append(flatpak_app_id)
                self.emulator_process = subprocess.Popen(
                    arguments,
                    cwd=console_path(console_id),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                GLib.timeout_add(400, self._check_emulator_process)
            elif is_desktop_launcher:
                # Abre o atalho .desktop pela API nativa, como ao ativá-lo no gerenciador de arquivos.
                desktop_app = GioUnix.DesktopAppInfo.new_from_filename(
                    str(emulator_path)
                )
                if desktop_app is None:
                    raise ValueError("O atalho .desktop do emulador não é mais válido.")
                uris = [game_path.resolve().as_uri()] if game_path else []
                launched = desktop_app.launch_uris_as_manager(
                    uris,
                    None,
                    GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.DO_NOT_REAP_CHILD,
                    None,
                    None,
                    self._on_desktop_emulator_started,
                    console_id,
                )
                if not launched:
                    raise OSError("O sistema não conseguiu abrir o atalho do emulador.")
            else:
                arguments = [str(emulator_path)]
                if game_path:
                    arguments.append(str(game_path.resolve()))

                self.emulator_process = subprocess.Popen(
                    arguments,
                    cwd=console_path(console_id),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                GLib.timeout_add(400, self._check_emulator_process)

            self._set_status(
                f"Emulador aberto para {CONSOLES[console_id]['name']}. "
                "A interface volta quando ele for fechado."
            )
            self.window.set_visible(False)
        except (
            OSError,
            ValueError,
            IndexError,
            json.JSONDecodeError,
            GLib.Error,
        ) as error:
            self._set_status(str(error), is_error=True)

    def _on_desktop_emulator_started(self, _desktop_app, pid, console_id):
        self.emulator_pid = pid
        GLib.child_watch_add(
            GLib.PRIORITY_DEFAULT,
            pid,
            self._on_desktop_emulator_exit,
            console_id,
        )

    def _on_desktop_emulator_exit(self, _pid, status, _console_id):
        self.emulator_pid = None
        self.window.present()
        return_code = os.waitstatus_to_exitcode(status)
        if return_code == 0:
            self._set_status("Emulador fechado. Você voltou para a biblioteca.")
        else:
            self._set_status(
                f"O emulador terminou com código {return_code}.",
                is_error=True,
            )

    def _check_emulator_process(self):
        if self.emulator_process is None:
            return GLib.SOURCE_REMOVE
        return_code = self.emulator_process.poll()
        if return_code is None:
            return GLib.SOURCE_CONTINUE
        self.emulator_process = None
        self.window.present()
        if return_code == 0:
            self._set_status("Emulador fechado. Você voltou para a biblioteca.")
        else:
            self._set_status(
                f"O emulador terminou com código {return_code}.",
                is_error=True,
            )
        return GLib.SOURCE_REMOVE

    def _set_status(self, message, is_error=False):
        self.status_label.set_text(message)
        self.status_label.remove_css_class("status-error")
        self.status_label.add_css_class("status-error" if is_error else "settings-description")
        self.status_revealer.set_reveal_child(True)
        if self.status_timeout:
            GLib.source_remove(self.status_timeout)
        self.status_timeout = GLib.timeout_add(4500, self._hide_status)

    def _hide_status(self):
        self.status_revealer.set_reveal_child(False)
        self.status_timeout = 0
        return GLib.SOURCE_REMOVE


def main():
    application = Gtk.Application(
        application_id="com.mygames.Library",
        flags=Gio.ApplicationFlags.FLAGS_NONE,
    )
    my_games = MyGamesApp(application)
    application.connect("activate", lambda _application: my_games.activate())
    return application.run(None)


if __name__ == "__main__":
    raise SystemExit(main())
