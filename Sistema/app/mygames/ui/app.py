"""Janela principal do My Games: prateleira de caixas no estilo de um console."""
import gc
import queue
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from gi.repository import Gdk, Gio, GLib, Gtk

from .. import desktop, emulators
from ..config import Config, ConfigError
from ..consoles import CONSOLE_LIST, CONSOLES
from ..covers import CoverStore
from ..gamepad import Gamepads
from ..library import pretty_size, scan_console
from ..paths import ASSETS_DIR, display_path
from ..sounds import ClickSound
from . import boxart
from .backdrop import Backdrop
from .settings_window import SettingsWindow
from .shelf import Shelf, ShelfItem
from .theme import TAB_NAMES, rgb, theme_for
from .widgets import open_folder, run_in_thread

STYLE = Path(__file__).with_name("style.css")
TABS = ["home"] + [console.id for console in CONSOLE_LIST]
BOOT_SECONDS = 1.6


@dataclass
class EmulatorState:
    """Situação do emulador de um console: se está pronto e o texto mostrado ao usuário."""
    emulator: object
    ready: bool
    label: str
    css: str


class MyGamesApp:
    """A janela principal do hub: abas, prateleira, busca, tela de início do jogo e avisos."""

    def __init__(self, application):
        """Prepara o estado inicial e lê as configurações (sem criar a janela ainda)."""
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
        self.tab = "home"
        self.items = {}  # caixas já montadas de cada aba
        self.games = {}  # jogos encontrados em cada console
        self.selection = {}  # caixa escolhida por último em cada aba
        self.covers = CoverStore()
        self.cover_queue = queue.Queue()
        self.cover_offline = False
        self.query = ""
        self.light_mode = False
        self.pad_connected = False
        self.last_launch = None
        self.emulator_states = {}
        try:
            self.config = Config()
            self.config_error = None
        except ConfigError as error:
            self.config = Config.__new__(Config)
            self.config.data = {"tela_cheia": True, "consoles": {}}
            self.config_error = str(error)
        self.sound = ClickSound(self.config.click_sound)  # clique ao passar pelas caixas
        threading.Thread(target=self._cover_worker, daemon=True).start()

    # ---- Construção ----------------------------------------------------------
    def activate(self):
        """Cria a janela, os controles e abre na aba Início."""
        if self.window is not None:
            self.window.present()
            return
        # A pasta assets entra na busca de ícones: "my-games" vira o ícone do controle
        # na janela e na barra de tarefas (Linux e Windows), sem instalar nada.
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(str(ASSETS_DIR))
        provider = Gtk.CssProvider()
        provider.load_from_path(str(STYLE))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.window = Gtk.ApplicationWindow(application=self.application, title="My Games")
        self.window.add_css_class("mygames")
        self.window.set_icon_name("my-games")
        self.window.set_default_size(1280, 760)
        self.window.set_size_request(960, 600)
        # A barra de título padrão some: o topo da tela é o próprio hub.
        self.window.set_titlebar(Gtk.Box(visible=False))

        overlay = Gtk.Overlay()
        self.window.set_child(overlay)
        self.backdrop = Backdrop()
        overlay.set_child(self.backdrop)

        self.pages = Gtk.Stack()
        self.pages.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.pages.set_transition_duration(450)
        overlay.add_overlay(self.pages)
        self.pages.add_named(self._build_main(), "main")
        self.pages.add_named(self._build_boot(), "boot")
        overlay.add_overlay(self._build_search())
        overlay.add_overlay(self._build_toast())

        keys = Gtk.EventControllerKey.new()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.window.add_controller(keys)
        self.window.connect("notify::fullscreened", self._on_fullscreen_changed)

        self.gamepads = Gamepads(
            on_move=self._pad_move,
            on_confirm=self._pad_confirm,
            on_back=self._pad_back,
            on_menu=lambda: self.show_settings(),
            is_enabled=self._accepts_input,
            on_change=self._on_pad_change,
            on_exit_combo=self._exit_game_from_pad,
            on_tab=self.cycle_tab,
        )
        self._on_pad_change(self.gamepads.connected)

        if self.config.data.get("modo_leve"):
            self._light_mode()
        self.window.maximize()
        self.window.present()
        renderer = self.window.get_renderer()
        if renderer is not None and type(renderer).__name__ == "CairoRenderer":
            # Sem aceleração de vídeo: tudo é desenhado pelo processador.
            self._light_mode(software=True)
        self.switch_tab("home")
        self.shelf.grab_focus()
        self._update_clock()
        GLib.timeout_add_seconds(15, self._update_clock)
        self._count_all_games()
        if self.config_error:
            self.toast(self.config_error, error=True)
        GLib.timeout_add_seconds(2, self._tune_garbage_collector)
        # Ícone do controle no arquivo "Abrir My Games", no tema e no atalho do menu.
        threading.Thread(target=desktop.sync_desktop_integration, daemon=True).start()

    @staticmethod
    def _tune_garbage_collector():
        """Evita pausas do coletor de lixo do Python no meio de uma animação.

        Os objetos criados na abertura (interface, catálogo) são congelados e
        não precisam ser revisados de novo; e as coletas ficam menos frequentes.
        """
        gc.collect()
        gc.freeze()
        gc.set_threshold(20_000, 50, 100)
        return GLib.SOURCE_REMOVE

    def _build_main(self):
        """Página principal: barra do topo, prateleira e barra de comandos."""
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main.append(self._build_topbar())
        self.shelf = Shelf(self._on_shelf_selected, self._on_shelf_activate, self._on_slow_frames)
        self.shelf.on_step = self.sound.click
        main.append(self.shelf)
        main.append(self._build_hints())
        return main

    def _build_topbar(self):
        """Barra do topo: nome do console, abas, relógio, contador e botão Menu."""
        handle = Gtk.WindowHandle()
        bar = Gtk.CenterBox()
        bar.add_css_class("topbar")
        handle.set_child(bar)

        brand = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.START)
        self.wordmark = Gtk.Label(xalign=0)
        self.wordmark.add_css_class("wordmark")
        brand.append(self.wordmark)
        self.kind_label = Gtk.Label(xalign=0)
        self.kind_label.add_css_class("brand-line")
        brand.append(self.kind_label)
        self.count_label = Gtk.Label(xalign=0)
        self.count_label.add_css_class("brand-line")
        brand.append(self.count_label)
        bar.set_start_widget(brand)

        tabs = Gtk.Box(valign=Gtk.Align.START, halign=Gtk.Align.CENTER)
        tabs.add_css_class("tabs")
        self.tab_buttons = {}
        for key in TABS:
            button = Gtk.Button(label=TAB_NAMES.get(key, key))
            button.add_css_class("tab")
            button.set_focusable(False)
            button.connect("clicked", lambda _b, k=key: (self.sound.click(), self.switch_tab(k)))
            tabs.append(button)
            self.tab_buttons[key] = button
        bar.set_center_widget(tabs)

        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.END, valign=Gtk.Align.START)
        status = Gtk.Box(spacing=14, halign=Gtk.Align.END)
        self.pad_icon = Gtk.Image.new_from_icon_name("input-gaming-symbolic")
        self.pad_icon.set_tooltip_text("Controle conectado")
        self.pad_icon.add_css_class("status-icon")
        status.append(self.pad_icon)
        self.date_label = Gtk.Label()
        self.date_label.add_css_class("date")
        status.append(self.date_label)
        self.time_label = Gtk.Label()
        self.time_label.add_css_class("clock")
        status.append(self.time_label)
        self.window_controls = Gtk.WindowControls(side=Gtk.PackType.END)
        status.append(self.window_controls)
        side.append(status)
        self.position_label = Gtk.Label(xalign=1, halign=Gtk.Align.END)
        self.position_label.add_css_class("position")
        side.append(self.position_label)
        menu = Gtk.Button(halign=Gtk.Align.END)
        menu.add_css_class("menu-pill")
        menu.set_focusable(False)
        menu.set_tooltip_text("Configurações")
        menu_content = Gtk.Box(spacing=8)
        menu_content.append(Gtk.Image.new_from_icon_name("open-menu-symbolic"))
        menu_content.append(Gtk.Label(label="Menu"))
        menu.set_child(menu_content)
        menu.connect("clicked", lambda *_: self.show_settings(self.tab if self.tab in CONSOLES else None))
        side.append(menu)
        bar.set_end_widget(side)
        return handle

    def _build_hints(self):
        """Barra de comandos no rodapé (preenchida por _refresh_hints)."""
        self.hints = Gtk.Box(halign=Gtk.Align.CENTER)
        self.hints.add_css_class("hintbar")
        self.hints.set_margin_bottom(22)
        return self.hints

    def _refresh_hints(self):
        """Mostra os botões do controle (se conectado) ou as teclas do teclado na barra de comandos."""
        while (child := self.hints.get_first_child()) is not None:
            self.hints.remove(child)
        # O estado vem do aviso do controle: durante a criação de Gamepads ele já
        # avisa "conectado" antes de self.gamepads existir (isso travava a abertura).
        pad = self.pad_connected
        confirm = "Abrir" if self.tab == "home" else "Jogar"
        if pad:
            entries = [
                ([("LB", "bumper"), ("RB", "bumper")], "Console"),
                ([("◀▶", "dpad")], "Navegar"),
                ([("A", "btn-a")], confirm),
                ([("B", "btn-b")], "Início"),
                ([("☰", "btn-menu")], "Menu"),
                ([("SELECT", "bumper"), ("START", "bumper")], "Sair do jogo"),
            ]
        else:
            entries = [
                ([("Q", "key"), ("E", "key")], "Console"),
                ([("←", "key"), ("→", "key")], "Navegar"),
                ([("Enter", "key")], confirm),
                ([("Esc", "key")], "Início"),
                ([("/", "key")], "Buscar"),
                ([("M", "key")], "Menu"),
                ([("F11", "key")], "Tela cheia"),
            ]
        for glyphs, text in entries:
            group = Gtk.Box(spacing=5)
            group.add_css_class("hint-group")
            for glyph, css in glyphs:
                label = Gtk.Label(label=glyph)
                label.add_css_class("glyph")
                label.add_css_class(css)
                group.append(label)
            caption = Gtk.Label(label=text)
            caption.add_css_class("hint-text")
            group.append(caption)
            self.hints.append(group)

    def _build_boot(self):
        """Tela "Iniciando" mostrada enquanto o emulador abre."""
        page = Gtk.Box(spacing=56, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        page.add_css_class("boot")
        self.boot_cover = Gtk.Picture()
        self.boot_cover.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.boot_cover.set_size_request(320, 420)
        self.boot_cover.add_css_class("boot-cover")
        page.append(self.boot_cover)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER)
        self.boot_kicker = Gtk.Label(xalign=0)
        self.boot_kicker.add_css_class("boot-kicker")
        text.append(self.boot_kicker)
        self.boot_console = Gtk.Label(xalign=0)
        self.boot_console.add_css_class("boot-console")
        text.append(self.boot_console)
        self.boot_game = Gtk.Label(xalign=0, wrap=True, max_width_chars=34)
        self.boot_game.add_css_class("boot-game")
        text.append(self.boot_game)
        self.boot_emulator = Gtk.Label(xalign=0)
        self.boot_emulator.add_css_class("boot-emulator")
        text.append(self.boot_emulator)
        self.boot_progress = Gtk.ProgressBar()
        self.boot_progress.add_css_class("boot-progress")
        self.boot_progress.set_size_request(260, -1)
        self.boot_progress.set_halign(Gtk.Align.START)
        self.boot_progress.set_margin_top(14)
        text.append(self.boot_progress)
        page.append(text)
        return page

    def _build_search(self):
        """Campo de busca que aparece embaixo das abas (tecla /)."""
        self.search_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.START,
        )
        self.search_revealer.set_margin_top(78)
        self.search = Gtk.SearchEntry(placeholder_text="Buscar jogo…")
        self.search.add_css_class("search-pill")
        self.search.set_search_delay(0)
        self.search.set_size_request(420, -1)
        self.search.connect("search-changed", lambda *_: self._apply_search())
        self.search_revealer.set_child(self.search)
        return self.search_revealer

    def _build_toast(self):
        """Aviso flutuante no rodapé (mensagens e erros)."""
        self.toast_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.END,
        )
        self.toast_revealer.set_margin_bottom(84)
        self.toast_revealer.set_can_target(False)
        self.toast_box = Gtk.Box()
        self.toast_box.add_css_class("toast")
        self.toast_label = Gtk.Label(wrap=True, max_width_chars=90)
        self.toast_box.append(self.toast_label)
        self.toast_revealer.set_child(self.toast_box)
        return self.toast_revealer

    # ---- Estado dos consoles -------------------------------------------------
    def emulator_state(self, console):
        """Descobre o emulador do console e se ele está pronto para abrir."""
        try:
            emulator = emulators.find_emulator(console, self.config)
        except OSError as error:
            return EmulatorState(None, False, f"Erro: {error}", "danger")
        if emulator is None:
            return EmulatorState(None, False, "Sem emulador", "warn")
        problem = emulators.emulator_problem(emulator)
        if problem:
            return EmulatorState(emulator, False, "Emulador não encontrado", "danger")
        return EmulatorState(emulator, True, f"{emulator.name} pronto", "ok")

    def scan(self, console, config=None):
        """Procura os jogos de um console."""
        return scan_console(console, config or self.config)

    def _count_all_games(self):
        """Conta os jogos e verifica os emuladores de todos os consoles, fora da interface."""
        config = self.config

        def work():
            """Roda numa thread: conta jogos e detecta emuladores."""
            counts = {console.id: len(scan_console(console, config).games) for console in CONSOLE_LIST}
            states = {console.id: self.emulator_state(console) for console in CONSOLE_LIST}
            return counts, states

        def done(result, _error):
            """De volta à interface: atualiza a aba Início e o topo."""
            if result:
                counts, states = result
                self.game_counts.update(counts)
                self.emulator_states.update(states)
                self._refresh_home_meta()
                self._refresh_topbar()

        run_in_thread(work, done)

    def _games_text(self, count):
        """Texto "N jogos" (ou "contando jogos…" enquanto não sabe)."""
        if count is None:
            return "contando jogos…"
        return f"{count} jogo{'s' if count != 1 else ''}"

    # ---- Caixas --------------------------------------------------------------
    def _home_items(self):
        """Caixas da aba Início: um item por console, com foto e contorno brilhante."""
        theme = theme_for("home")
        items = []
        for console in CONSOLE_LIST:
            if console.art.is_file():
                make_front = lambda c=console: boxart.cover_surface(c.art, 640)  # noqa: E731
            else:
                make_front = lambda c=console: boxart.front_surface(theme, c.maker, c.name, 1.2)  # noqa: E731
            make_glow = None
            if console.art.is_file() and console.outline.is_file():
                make_glow = lambda c=console: boxart.cover_surface(c.outline, 640)  # noqa: E731
            items.append(
                ShelfItem(console.name, "", make_front, payload=("console", console), aspect=1.0, make_glow=make_glow)
            )
        self.items["home"] = items
        self._refresh_home_meta()
        return items

    def _refresh_home_meta(self):
        """Atualiza a linha de informações de cada console na aba Início."""
        for item in self.items.get("home", []):
            console = item.payload[1]
            state = self.emulator_states.get(console.id)
            if state is None:
                item.meta = f"{console.maker} · {console.year} · {self._games_text(self.game_counts.get(console.id))}"
                continue
            item.meta = f"{console.maker} · {console.year} · {self._games_text(self.game_counts.get(console.id))} · {state.label}"
        if self.tab == "home":
            self.shelf.queue_draw()

    def _console_items(self, console, games):
        """Caixas de um console: "Adicionar jogos", "Abrir emulador" e os jogos."""
        theme = theme_for(console.id)
        state = self.emulator_states.get(console.id) or self.emulator_state(console)
        label = TAB_NAMES.get(console.id, console.short)
        emulator_name = state.emulator.name if state.emulator else console.download.emulator
        placeholder = _placeholder(theme)

        def special(title, meta, glyph, footer, action, spine_label):
            """Monta uma caixa especial (pasta de jogos, emulador ou configurações)."""
            return ShelfItem(
                title,
                meta,
                lambda: boxart.front_surface(theme, console.name, title, theme.aspect, glyph=glyph, footer=footer),
                lambda: boxart.spine_surface(theme, spine_label, title, theme.depth),
                theme.depth,
                payload=(action, None),
                aspect=theme.aspect,
                placeholder=placeholder,
            )

        items = [special("Adicionar jogos", "Abre a pasta Adicionar Jogos", "+", "Pasta de jogos", "folder", "+")]
        if state.ready:
            items.append(special(f"Abrir {emulator_name}", f"Emulador · {state.label}", "▶", "Emulador", "emulator", "EMU"))
        else:
            items.append(special("Configurar emulador", state.label, "⚙", "Configurações", "settings", "EMU"))
        for game in games:
            items.append(self._game_item(console, game, theme, label))
        return items

    def _game_item(self, console, game, theme, label):
        """Monta a caixa de um jogo (capa real ou capa gerada no estilo do console)."""
        def make_front():
            """Roda numa thread: lê a capa do jogo ou gera uma capa de reserva."""
            path = self.covers.find(console, game)
            if path:
                try:
                    return boxart.cover_surface(path)
                except GLib.Error:
                    pass
            return boxart.front_surface(theme, console.name, game.title, theme.aspect)

        meta = f"{game.kind} · {pretty_size(game.size)} · {game.location}"
        return ShelfItem(
            game.title,
            meta,
            make_front,
            lambda: boxart.spine_surface(theme, label, game.title, theme.depth),
            theme.depth,
            payload=("game", game),
            aspect=theme.aspect,
            placeholder=_placeholder(theme),
        )

    def _first_game_index(self, items):
        """Posição do primeiro jogo na prateleira (pula as caixas especiais)."""
        return next((i for i, item in enumerate(items) if item.payload[0] == "game"), 0)

    # ---- Abas ----------------------------------------------------------------
    def cycle_tab(self, step):
        """Vai para a aba anterior/seguinte (Q/E, LB/RB)."""
        self.sound.click()
        self.switch_tab(TABS[(TABS.index(self.tab) + step) % len(TABS)])

    def switch_tab(self, key):
        """Troca de aba: cor do fundo, caixas da prateleira e textos do topo."""
        if self.shelf.items:
            self.selection[self.tab] = self.shelf.index
        self._close_search()
        self.tab = key
        self.backdrop.set_theme(key)
        for name, button in self.tab_buttons.items():
            (button.add_css_class if name == key else button.remove_css_class)("active")
        self._refresh_hints()
        if key == "home":
            items = self.items.get("home") or self._home_items()
            self.shelf.set_items(items, self.selection.get("home", 0))
        else:
            console = CONSOLES[key]
            items = self.items.get(key)
            if items is None:
                items = self._console_items(console, [])
            default = self._first_game_index(items)
            self.shelf.set_items(items, self.selection.get(key, default))
            self._rescan(console)
        self._refresh_topbar()

    def _rescan(self, console):
        """Procura os jogos do console de novo (fora da interface) e atualiza a prateleira se mudou."""
        config = self.config

        def done(result, error):
            """De volta à interface: monta as caixas e pede as capas que faltam."""
            if error:
                self.toast(f"Não consegui ler as pastas de {console.name}: {error}", error=True)
                return
            self.game_counts[console.id] = len(result.games)
            old = [game.path for game in self.games.get(console.id, [])]
            if console.id in self.items and old == [game.path for game in result.games]:
                self._refresh_topbar()
                return
            self.games[console.id] = result.games
            items = self._console_items(console, result.games)
            self.items[console.id] = items
            if self.tab == console.id:
                current = self.shelf.selected
                index = self._first_game_index(items)
                if current is not None and current.payload[0] == "game":
                    paths = [item.payload[1].path if item.payload[0] == "game" else None for item in items]
                    if current.payload[1].path in paths:
                        index = paths.index(current.payload[1].path)
                elif current is not None and console.id in self.selection:
                    index = min(self.shelf.index, len(items) - 1)
                self.shelf.set_items(items, index, animate=False)
                self._refresh_topbar()
                if result.missing:
                    names = ", ".join(display_path(m) for m in result.missing)
                    self.toast(f"Pasta não encontrada (pendrive desconectado?): {names}", error=True)
            self._refresh_home_meta()
            for game in result.games:
                if self.covers.find(console, game) is None:
                    self.cover_queue.put((console, game))

        def work():
            """Roda numa thread: detecta o emulador e procura os jogos."""
            # Detectar o emulador lê o disco (lento num pendrive): fica fora da interface.
            self.emulator_states[console.id] = self.emulator_state(console)
            return scan_console(console, config)

        run_in_thread(work, done)

    def _refresh_topbar(self):
        """Atualiza o nome do console, o tipo, a contagem de jogos e o contador "3 / 12"."""
        if self.tab == "home":
            self.wordmark.set_text("MY GAMES")
            self.kind_label.set_text("HUB DE EMULAÇÃO")
            total = sum(self.game_counts.values()) if self.game_counts else None
            self.count_label.set_text(self._games_text(total).upper())
        else:
            console = CONSOLES[self.tab]
            self.wordmark.set_text(TAB_NAMES.get(console.id, console.short).upper())
            self.kind_label.set_text(theme_for(console.id).kind)
            self.count_label.set_text(self._games_text(self.game_counts.get(console.id)).upper())
        items = self.shelf.items
        self.position_label.set_text(f"{self.shelf.index + 1} / {len(items)}" if items else "")

    def _on_slow_frames(self):
        """O computador não acompanhou a animação: desliga os efeitos mais pesados."""
        if not self.light_mode:
            self._light_mode()
            self.toast("Modo leve ativado para manter a navegação fluida.")

    def _light_mode(self, software=False):
        """Modo leve: fundo parado e sem reflexo (software=True também desenha menos caixas)."""
        self.light_mode = True
        self.backdrop.set_animated(False)
        self.shelf.reflections = False
        if software:
            self.shelf.visible = 7
        self.shelf.queue_draw()

    def _update_clock(self):
        """Atualiza a data e a hora do topo."""
        now = GLib.DateTime.new_now_local()
        self.date_label.set_text(now.format("%a, %d %b").capitalize())
        self.time_label.set_text(now.format("%H:%M"))
        return GLib.SOURCE_CONTINUE

    # ---- Capas ---------------------------------------------------------------
    def _cover_worker(self):
        """Thread que baixa as capas que faltam, uma de cada vez."""
        while True:
            console, game = self.cover_queue.get()
            if self.cover_offline:
                continue
            try:
                path = self.covers.download(console, game)
            except OSError:
                # Sem internet: tenta de novo só na próxima vez que o hub abrir.
                self.cover_offline = True
                continue
            except Exception:  # noqa: BLE001 - uma capa ruim não pode parar as outras
                continue
            if path:
                GLib.idle_add(self._cover_ready, console, game, path)

    def _cover_ready(self, console, game, path):
        """Uma capa terminou de baixar: recarrega a caixa daquele jogo."""
        for item in self.items.get(console.id, []):
            if item.payload[0] == "game" and item.payload[1].path == game.path:
                self.shelf.reload_item(item)
        return GLib.SOURCE_REMOVE

    # ---- Busca ---------------------------------------------------------------
    def _open_search(self):
        """Mostra o campo de busca (não existe na aba Início)."""
        if self.tab == "home":
            return
        self.search_revealer.set_reveal_child(True)
        self.search.grab_focus()

    def _close_search(self):
        """Esconde e limpa a busca."""
        if not self.search_revealer.get_reveal_child():
            return
        self.search_revealer.set_reveal_child(False)
        if self.search.get_text():
            self.search.set_text("")
        self.shelf.grab_focus()

    def _apply_search(self):
        """Mostra só os jogos cujo nome contém o texto buscado."""
        if self.tab == "home":
            return
        query = self.search.get_text().strip().casefold()
        items = self.items.get(self.tab, [])
        if query:
            items = [item for item in items if item.payload[0] == "game" and query in item.title.casefold()]
        if items:
            self.shelf.set_items(items, 0 if query else self._first_game_index(items), animate=False)
        self._refresh_topbar()

    # ---- Navegação -----------------------------------------------------------
    def _on_shelf_selected(self, _index):
        """A seleção da prateleira mudou: atualiza o contador do topo."""
        if self.window is not None:
            self._refresh_topbar()

    def _on_shelf_activate(self, item):
        """Clicou/confirmou uma caixa: abre o console, o jogo, o emulador, a pasta ou as configurações."""
        kind, value = item.payload
        self.sound.click()
        if kind == "console":
            self.switch_tab(value.id)
        elif kind == "game":
            self.launch(CONSOLES[self.tab], value.path, item)
        elif kind == "emulator":
            self.launch(CONSOLES[self.tab], None, item)
        elif kind == "folder":
            open_folder(CONSOLES[self.tab].games_dir)
        elif kind == "settings":
            self.show_settings(self.tab)

    def activate_selected(self):
        """Confirma a caixa selecionada (Enter / botão A)."""
        if self.shelf.selected is not None:
            self._on_shelf_activate(self.shelf.selected)

    def go_home(self):
        """Esc / botão B: fecha a busca ou volta para a aba Início."""
        if self.search_revealer.get_reveal_child():
            self._close_search()
            self._apply_search()
        elif self.tab != "home":
            self.sound.click()
            self.switch_tab("home")

    def show_settings(self, console_id=None):
        """Abre a janela de Configurações (focando um console, se informado)."""
        if self.settings_window is not None:
            self.settings_window.present()
            return
        self.settings_window = SettingsWindow(self, console_id)
        self.settings_window.connect("close-request", self._on_settings_closed)
        self.settings_window.present()

    def _on_settings_closed(self, _window):
        """Fechou as Configurações: recarrega tudo, pois emulador ou pastas podem ter mudado."""
        self.settings_window = None
        # Emulador ou pastas podem ter mudado: monta tudo de novo.
        self.items = {key: value for key, value in self.items.items() if key == "home"}
        self.games = {}
        self._refresh_home_meta()
        self._count_all_games()
        if self.tab in CONSOLES:
            self._rescan(CONSOLES[self.tab])
        return False

    # Mantido para a janela de configurações, que avisa quando algo muda.
    def refresh_console(self, console):
        """As Configurações mudaram algo de um console: refaz as caixas dele."""
        self.items.pop(console.id, None)
        self.games.pop(console.id, None)
        self._refresh_home_meta()
        if self.tab == console.id:
            self._rescan(console)

    def _on_key(self, _controller, keyval, _keycode, state):
        """Teclado: setas, Enter, Esc, Q/E, Home/End, /, M e F11."""
        if self.pages.get_visible_child_name() == "boot":
            return True
        searching = self.search.has_focus() or self.search_revealer.get_reveal_child()
        if keyval in (Gdk.KEY_Left, Gdk.KEY_Up):
            self.shelf.move(-1)
        elif keyval in (Gdk.KEY_Right, Gdk.KEY_Down):
            self.shelf.move(1)
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) or (keyval == Gdk.KEY_space and not searching):
            self.activate_selected()
        elif keyval == Gdk.KEY_Escape:
            self.go_home()
        elif keyval in (Gdk.KEY_Page_Up, Gdk.KEY_ISO_Left_Tab) or (keyval in (Gdk.KEY_q, Gdk.KEY_Q) and not searching):
            self.cycle_tab(-1)
        elif keyval in (Gdk.KEY_Page_Down, Gdk.KEY_Tab) or (keyval in (Gdk.KEY_e, Gdk.KEY_E) and not searching):
            self.cycle_tab(1)
        elif keyval == Gdk.KEY_Home:
            self.shelf.select(0)
        elif keyval == Gdk.KEY_End:
            self.shelf.select(len(self.shelf.items) - 1)
        elif keyval == Gdk.KEY_F11:
            (self.window.unfullscreen if self.window.is_fullscreen() else self.window.fullscreen)()
        elif not searching and keyval in (Gdk.KEY_slash, Gdk.KEY_f) and (
            keyval == Gdk.KEY_slash or state & Gdk.ModifierType.CONTROL_MASK
        ):
            self._open_search()
        elif not searching and keyval in (Gdk.KEY_m, Gdk.KEY_M):
            self.show_settings(self.tab if self.tab in CONSOLES else None)
        else:
            return False
        return True

    def _on_fullscreen_changed(self, *_args):
        """Esconde os botões de minimizar/fechar quando o hub está em tela cheia."""
        self.window_controls.set_visible(not self.window.is_fullscreen())

    def _on_pad_change(self, connected):
        """Um controle foi conectado/desconectado: atualiza o ícone e a barra de comandos."""
        self.pad_connected = connected
        self.pad_icon.set_visible(connected)
        self._refresh_hints()

    def _accepts_input(self):
        """O hub deve reagir ao controle agora? (não durante um jogo nem com Configurações abertas)"""
        return (
            self.window is not None
            and self.window.get_visible()
            and self.window.is_active()
            and self.settings_window is None
            and self.pages.get_visible_child_name() == "main"
        )

    def _pad_move(self, dx, dy):
        """Direcional/analógico do controle: move a seleção."""
        self.shelf.move(dx or dy)

    def _pad_confirm(self):
        """Botão A do controle: confirma a caixa selecionada."""
        self.activate_selected()

    def _pad_back(self):
        """Botão B do controle: volta (fecha a busca ou vai para o Início)."""
        self.go_home()

    # ---- Emulador ------------------------------------------------------------
    def launch(self, console, game, item=None):
        """Abre o jogo (ou só o emulador): confere emulador e BIOS, mostra "Iniciando" e esconde o hub."""
        if self.process is not None and self.process.poll() is None:
            self.toast("Um emulador já está aberto. Feche-o para abrir outro jogo.", error=True)
            return
        state = self.emulator_state(console)
        if not state.ready:
            problem = emulators.emulator_problem(state.emulator) if state.emulator else None
            self.toast(
                (problem + " " if problem else f"{console.name} ainda não tem emulador. ")
                + "Abra o Menu para baixar ou escolher um.",
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
        self.last_launch = (console, game, item, state.emulator)
        self._show_boot(console, state.emulator.name, item, game)
        GLib.timeout_add(500, self._watch_process)
        GLib.timeout_add(int(BOOT_SECONDS * 1000), self._hide_while_playing)

    def _show_boot(self, console, emulator_name, item, game):
        """Mostra a tela "Iniciando" com a capa, o console, o jogo e uma barra de progresso."""
        if item is not None:
            self.boot_cover.set_paintable(item.front)  # None enquanto a capa carrega
        self.boot_kicker.set_text(f"INICIANDO  ·  {TAB_NAMES.get(console.id, console.short).upper()}")
        self.boot_console.set_text(console.name)
        self.boot_game.set_text(Path(game).stem if game and item is None else (item.title if game else "Sem jogo"))
        self.boot_emulator.set_text(f"Abrindo no {emulator_name}")
        self.boot_progress.set_fraction(0)
        self.pages.set_visible_child_name("boot")
        started = GLib.get_monotonic_time()

        def step():
            """Avança a barra de progresso da tela "Iniciando"."""
            progress = (GLib.get_monotonic_time() - started) / (BOOT_SECONDS * 1_000_000)
            self.boot_progress.set_fraction(min(1, 1 - (1 - min(progress, 1)) ** 2))
            return progress < 1 and self.pages.get_visible_child_name() == "boot"

        GLib.timeout_add(16, step)

    def _exit_game_from_pad(self):
        """SELECT + START no controle: fecha o emulador e volta para o hub."""
        if self.process is None or self.process.poll() is not None:
            return
        self.toast("Saindo do jogo…")
        self.exit_requested = True
        emulators.stop(self.process)
        process = self.process
        # Se o emulador não fechar sozinho em 5 segundos, força.
        GLib.timeout_add_seconds(5, lambda: (process.poll() is None and _kill(process)) and False)

    def _hide_while_playing(self):
        """Esconde o hub enquanto o jogo roda (libera a tela e o controle para o emulador)."""
        if self.process is not None and self.process.poll() is None:
            self.window.set_visible(False)
        return GLib.SOURCE_REMOVE

    def _watch_process(self):
        """Vigia o emulador: quando ele fecha, o hub volta e avisa se houve erro."""
        if self.process is None:
            return GLib.SOURCE_REMOVE
        code = self.process.poll()
        if code is None:
            return GLib.SOURCE_CONTINUE
        self.process = None
        requested = self.exit_requested
        if code != 0 and not requested and self._retry_without_fuse():
            return GLib.SOURCE_REMOVE
        self.pages.set_visible_child_name("main")
        self.window.set_visible(True)
        self.window.present()
        self.shelf.grab_focus()
        self.exit_requested = False
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

    def _retry_without_fuse(self):
        """AppImage não montou (FUSE ausente/bloqueado): extrai uma vez e tenta de novo."""
        if not self.last_launch or time.monotonic() - self.process_started > 30:
            return False
        console, game, item, emulator = self.last_launch
        if emulator.kind != "file" or not emulators.appimage_mount_failed(emulator.target, self.log_path):
            return False
        self.window.set_visible(True)
        self.pages.set_visible_child_name("boot")
        self.boot_emulator.set_text(f"Preparando o {emulator.name} neste computador (só na primeira vez)…")
        state = {"working": True}

        def pulse():
            """Anima a barra enquanto o emulador é preparado."""
            if state["working"]:
                self.boot_progress.pulse()
            return state["working"]

        GLib.timeout_add(120, pulse)

        def done(_apprun, error):
            """Preparação terminou: abre o jogo de novo (ou avisa o erro)."""
            state["working"] = False
            if error:
                self.pages.set_visible_child_name("main")
                self.toast(f"Não consegui preparar o {emulator.name}: {error}", error=True, seconds=10)
                return
            self.launch(console, game, item)

        run_in_thread(lambda: emulators.extract_appimage(emulator.target), done)
        return True

    # ---- Avisos --------------------------------------------------------------
    def toast(self, message, error=False, seconds=None):
        """Mostra um aviso no rodapé por alguns segundos (vermelho se for erro)."""
        self.toast_label.set_text(message)
        (self.toast_box.add_css_class if error else self.toast_box.remove_css_class)("error")
        self.toast_revealer.set_reveal_child(True)
        if self.toast_source:
            GLib.source_remove(self.toast_source)
        self.toast_source = GLib.timeout_add_seconds(seconds or (7 if error else 4), self._hide_toast)

    def _hide_toast(self):
        """Esconde o aviso do rodapé."""
        self.toast_revealer.set_reveal_child(False)
        self.toast_source = 0
        return GLib.SOURCE_REMOVE


def _placeholder(theme):
    """Cor da caixa enquanto a capa ainda está sendo preparada."""
    color = Gdk.RGBA()
    color.red, color.green, color.blue = rgb(theme.spine)
    color.alpha = 0.85
    return color


def _kill(process):
    """Força o fechamento do emulador (e dos processos que ele abriu)."""
    emulators.stop(process, force=True)
    return False


def _last_log_line(path):
    """Última linha do log do emulador, para mostrar o motivo de um erro."""
    try:
        lines = [line.strip() for line in Path(path).read_text(errors="replace").splitlines()[1:] if line.strip()]
    except (OSError, TypeError):
        return ""
    return lines[-1][:140] if lines else ""


def _show_startup_error(application, error):
    """Se algo der errado na abertura, mostra o motivo em vez de não abrir nada."""
    import traceback

    details = "".join(traceback.format_exception(error))
    print(details, file=sys.stderr, flush=True)
    window = Gtk.ApplicationWindow(application=application, title="My Games")
    window.set_default_size(640, 260)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    for side in ("top", "bottom", "start", "end"):
        getattr(box, f"set_margin_{side}")(24)
    title = Gtk.Label(label="O My Games não conseguiu abrir", xalign=0)
    title.add_css_class("title-2")
    box.append(title)
    text = Gtk.Label(
        label=f"{type(error).__name__}: {error}\n\nOs detalhes foram salvos em Sistema/logs/hub.log.",
        xalign=0,
        wrap=True,
        selectable=True,
    )
    box.append(text)
    window.set_child(box)
    window.present()


def run():
    """Cria o aplicativo GTK e abre o hub (com tela de erro se algo falhar na abertura)."""
    application = Gtk.Application(application_id="com.mygames.Library", flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
    app = MyGamesApp(application)

    def activate(*_args):
        """Abre a janela; se der erro, mostra o motivo em vez de não abrir nada."""
        try:
            app.activate()
        except Exception as error:  # noqa: BLE001 - qualquer falha precisa aparecer na tela
            if app.window is not None:
                app.window.destroy()
                app.window = None
            _show_startup_error(application, error)

    application.connect("activate", activate)
    return application.run(None)
