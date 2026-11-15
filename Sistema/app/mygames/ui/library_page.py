"""Página com a lista de jogos de um console."""
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango

from ..library import pretty_size
from .widgets import icon_button, open_folder, run_in_thread

ART_WIDTH, ART_HEIGHT = 340, 340


class LibraryPage(Gtk.Box):
    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=28)
        self.app = app
        self.console = None
        self.scan_token = 0
        self.set_margin_top(6)
        self.set_margin_bottom(18)
        self.set_margin_start(28)
        self.set_margin_end(28)

        # ---- Coluna da esquerda: arte e ações do console ----
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        left.set_size_request(ART_WIDTH, -1)
        left.set_hexpand(False)
        left.set_valign(Gtk.Align.CENTER)
        self.append(left)

        back = Gtk.Button()
        back.add_css_class("flat-icon")
        back.set_tooltip_text("Voltar aos consoles (Esc)")
        back_content = Gtk.Box(spacing=6)
        back_content.append(Gtk.Image.new_from_icon_name("go-previous-symbolic"))
        back_content.append(Gtk.Label(label="Consoles"))
        back.set_child(back_content)
        back.connect("clicked", lambda *_: app.show_home())
        back.set_halign(Gtk.Align.START)
        left.append(back)

        # Imagem já reduzida, para a coluna não crescer com o tamanho original da foto.
        self.art = Gtk.Image()
        self.art.add_css_class("library-art")
        self.art.set_pixel_size(ART_WIDTH)
        left.append(self.art)

        self.title = Gtk.Label(xalign=0)
        self.title.add_css_class("library-title")
        left.append(self.title)
        self.subtitle = Gtk.Label(xalign=0, wrap=True)
        self.subtitle.add_css_class("library-sub")
        left.append(self.subtitle)
        self.status_chip = Gtk.Label(xalign=0)
        self.status_chip.set_halign(Gtk.Align.START)
        self.status_chip.add_css_class("chip")
        left.append(self.status_chip)

        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        actions.set_margin_top(6)
        self.open_emulator = Gtk.Button(label="Abrir emulador sem jogo")
        self.open_emulator.connect("clicked", lambda *_: app.launch(self.console, None))
        actions.append(self.open_emulator)
        open_games = Gtk.Button(label="Abrir pasta de jogos")
        open_games.connect("clicked", lambda *_: open_folder(self.console.games_dir))
        actions.append(open_games)
        left.append(actions)

        # ---- Coluna da direita: busca e lista ----
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        panel.add_css_class("panel")
        panel.set_hexpand(True)
        panel.set_vexpand(True)
        self.append(panel)

        top = Gtk.Box(spacing=10)
        self.search = Gtk.SearchEntry(placeholder_text="Buscar jogo…")
        self.search.set_hexpand(True)
        self.search.connect("search-changed", lambda *_: self._refilter())
        self.search.connect("activate", lambda *_: self._activate_first_visible())
        self.search.connect("stop-search", lambda *_: app.show_home())
        top.append(self.search)
        refresh = icon_button("view-refresh-symbolic", "Procurar jogos de novo", lambda *_: self.reload())
        top.append(refresh)
        panel.append(top)

        self.count_label = Gtk.Label(xalign=0)
        self.count_label.add_css_class("hint")
        panel.append(self.count_label)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_vexpand(True)
        panel.append(self.stack)

        self.list = Gtk.ListBox()
        self.list.add_css_class("games")
        self.list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list.set_activate_on_single_click(True)
        self.list.set_filter_func(self._filter)
        self.list.connect("row-activated", self._on_row_activated)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.list)
        self.stack.add_named(scroller, "list")

        loading = Gtk.Spinner(spinning=True)
        loading.set_size_request(32, 32)
        loading.set_halign(Gtk.Align.CENTER)
        loading.set_valign(Gtk.Align.CENTER)
        self.stack.add_named(loading, "loading")

        self.empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.empty.set_valign(Gtk.Align.CENTER)
        self.empty.set_halign(Gtk.Align.CENTER)
        empty_icon = Gtk.Image.new_from_icon_name("folder-open-symbolic")
        empty_icon.set_pixel_size(56)
        empty_icon.set_opacity(0.5)
        self.empty.append(empty_icon)
        self.empty_title = Gtk.Label()
        self.empty_title.add_css_class("empty-title")
        self.empty.append(self.empty_title)
        self.empty_text = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=60)
        self.empty_text.add_css_class("empty-text")
        self.empty.append(self.empty_text)
        empty_actions = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        empty_open = Gtk.Button(label="Abrir pasta Adicionar Jogos")
        empty_open.connect("clicked", lambda *_: open_folder(self.console.games_dir))
        self.empty_open = empty_open
        empty_actions.append(empty_open)
        empty_settings = Gtk.Button(label="Usar outra pasta…")
        empty_settings.add_css_class("primary")
        empty_settings.connect("clicked", lambda *_: app.show_settings(self.console.id))
        empty_actions.append(empty_settings)
        self.empty.append(empty_actions)
        self.stack.add_named(self.empty, "empty")

        self.search.set_key_capture_widget(self)

    # ---- Dados ---------------------------------------------------------------
    def show_console(self, console):
        self.console = console
        self.search.set_text("")
        self.title.set_text(console.name)
        if console.art.is_file():
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(console.art), ART_WIDTH, ART_HEIGHT, True)
            self.art.set_from_paintable(Gdk.Texture.new_for_pixbuf(pixbuf))
        self.refresh_header()
        self.reload()

    def refresh_header(self):
        if self.console is None:
            return
        state = self.app.emulator_state(self.console)
        self.status_chip.set_text(state.label)
        for css in ("ok", "warn", "danger"):
            self.status_chip.remove_css_class(css)
        self.status_chip.add_css_class(state.css)
        self.open_emulator.set_sensitive(state.ready)
        self.subtitle.set_text(f"{self.console.maker} · {self.console.year}")

    def reload(self):
        console = self.console
        self.scan_token += 1
        token = self.scan_token
        self.stack.set_visible_child_name("loading")
        self.count_label.set_text("Procurando jogos…")
        config = self.app.config

        def done(result, error):
            if token != self.scan_token:
                return
            if error:
                self._show_empty("Não consegui ler as pastas", str(error))
                return
            self._fill(result)
            self.app.update_game_count(console, len(result.games))

        run_in_thread(lambda: self.app.scan(console, config), done)

    def _fill(self, result):
        while (row := self.list.get_row_at_index(0)) is not None:
            self.list.remove(row)
        missing = ""
        if result.missing:
            missing = " · " + ", ".join(f"{m.name} não encontrada (pendrive desconectado?)" for m in result.missing)
        if not result.games:
            folders = "\n".join(
                f"• {folder}" for folder in [f"Emuladores/{self.console.folder}/Adicionar Jogos"] + [str(m) for m in result.missing]
            )
            self._show_empty(
                "Nenhum jogo encontrado",
                f"Coloque os jogos de {self.console.name} em uma destas pastas:\n{folders}",
            )
            self.count_label.set_text("0 jogos" + missing)
            return
        for game in result.games:
            self.list.append(self._make_row(game))
        total = len(result.games)
        self.count_label.set_text(f"{total} jogo{'s' if total != 1 else ''}" + missing)
        self.stack.set_visible_child_name("list")
        first = self.list.get_row_at_index(0)
        self.list.select_row(first)
        if not self.search.get_text():
            GLib.idle_add(first.grab_focus)

    def _show_empty(self, title, text):
        self.empty_title.set_text(title)
        self.empty_text.set_text(text)
        self.stack.set_visible_child_name("empty")

    def _make_row(self, game):
        row = Gtk.ListBoxRow()
        row.add_css_class("game")
        row.game = game
        row.set_tooltip_text(str(game.path))
        box = Gtk.Box(spacing=14)
        badge = Gtk.Label(label=game.kind)
        badge.add_css_class("badge")
        box.append(badge)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_valign(Gtk.Align.CENTER)
        text.set_hexpand(True)
        name = Gtk.Label(label=game.title, xalign=0)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.add_css_class("game-title")
        text.append(name)
        meta = Gtk.Label(label=f"{game.location} · {pretty_size(game.size)}", xalign=0)
        meta.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        meta.add_css_class("game-meta")
        text.append(meta)
        box.append(text)
        play = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
        play.add_css_class("play-icon")
        play.set_pixel_size(20)
        box.append(play)
        row.set_child(box)
        return row

    # ---- Busca e navegação ---------------------------------------------------
    def _filter(self, row):
        query = self.search.get_text().strip().casefold()
        return not query or query in row.game.title.casefold()

    def _refilter(self):
        self.list.invalidate_filter()
        rows = self._visible_rows()
        if rows:
            self.list.select_row(rows[0])

    def _visible_rows(self):
        rows, index = [], 0
        while (row := self.list.get_row_at_index(index)) is not None:
            if row.get_child_visible() and self._filter(row):
                rows.append(row)
            index += 1
        return rows

    def _activate_first_visible(self):
        selected = self.list.get_selected_row()
        rows = self._visible_rows()
        target = selected if selected in rows else (rows[0] if rows else None)
        if target is not None:
            self._on_row_activated(self.list, target)

    def move(self, step):
        rows = self._visible_rows()
        if not rows:
            return
        selected = self.list.get_selected_row()
        index = rows.index(selected) + step if selected in rows else 0
        row = rows[max(0, min(index, len(rows) - 1))]
        self.list.select_row(row)
        row.grab_focus()

    def activate_selected(self):
        self._activate_first_visible()

    def _on_row_activated(self, _list, row):
        self.app.launch(self.console, row.game.path)
