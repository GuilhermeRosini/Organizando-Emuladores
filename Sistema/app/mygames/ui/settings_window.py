"""Janela de configurações: emuladores, pastas de jogos e preferências."""
from pathlib import Path

from gi.repository import GLib, Gtk, Pango

from .. import downloader
from ..config import ConfigError
from ..consoles import CONSOLE_LIST
from ..desktop import install_menu_shortcut
from ..emulators import has_libfuse2
from ..paths import IS_WINDOWS, ROOT, display_path
from .widgets import icon_button, open_folder, run_in_thread


class SettingsWindow(Gtk.Window):
    """Janela de Configurações: tela cheia, atalho no menu e emulador/pastas de cada console."""

    def __init__(self, app, focus_console=None):
        """Monta a janela com o cartão geral e um cartão por console."""
        super().__init__(application=app.application, title="Configurações")
        self.app = app
        self.cards = {}
        self.downloading = set()
        self.add_css_class("mygames")
        self.add_css_class("settings")
        self.set_transient_for(app.window)
        self.set_modal(True)
        self.set_default_size(820, 700)
        self.set_icon_name("my-games")

        header = Gtk.HeaderBar()
        title = Gtk.Label(label="Configurações")
        title.add_css_class("app-title")
        header.set_title_widget(title)
        self.set_titlebar(header)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_child(scroller)
        self.scroller = scroller
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.set_margin_top(10)
        content.set_margin_bottom(28)
        content.set_margin_start(28)
        content.set_margin_end(28)
        scroller.set_child(content)

        content.append(self._section("GERAL"))
        content.append(self._general_card())
        content.append(self._section("CONSOLES"))
        for console in CONSOLE_LIST:
            holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.cards[console.id] = holder
            content.append(holder)
            self._rebuild(console)

        if focus_console in self.cards:
            GLib.timeout_add(200, self._scroll_to, focus_console)

    def _scroll_to(self, console_id):
        """Rola até o cartão de um console."""
        ok, point = self.cards[console_id].compute_point(self.scroller.get_child(), _point(0, 0))
        if ok:
            self.scroller.get_vadjustment().set_value(point.y - 10)
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _section(text):
        """Título de uma seção da janela."""
        label = Gtk.Label(label=text, xalign=0)
        label.add_css_class("section-title")
        label.set_margin_top(10)
        return label

    # ---- Geral ---------------------------------------------------------------
    def _general_card(self):
        """Cartão geral: tela cheia nos emuladores, som de clique, atalho no menu e pasta do hub."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.add_css_class("card")

        row = Gtk.Box(spacing=12)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        texts.append(_label("Abrir jogos em tela cheia", "card-title"))
        texts.append(_label("Vale para todos os emuladores.", "note"))
        row.append(texts)
        switch = Gtk.Switch(active=self.app.config.fullscreen, valign=Gtk.Align.CENTER)
        switch.connect("notify::active", self._on_fullscreen)
        row.append(switch)
        card.append(row)

        row = Gtk.Box(spacing=12)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        texts.append(_label("Som de clique ao navegar", "card-title"))
        texts.append(_label("Toca um clique a cada jogo ou console que passa.", "note"))
        row.append(texts)
        sound = Gtk.Switch(active=self.app.config.click_sound, valign=Gtk.Align.CENTER)
        sound.connect("notify::active", self._on_click_sound)
        row.append(sound)
        card.append(row)

        row = Gtk.Box(spacing=12, visible=not IS_WINDOWS)  # atalho do menu: só no Linux
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        texts.append(_label("Atalho no menu de aplicativos", "card-title"))
        texts.append(_label("Cria o ícone do My Games neste computador apontando para esta pasta.", "note"))
        row.append(texts)
        shortcut = Gtk.Button(label="Criar atalho", valign=Gtk.Align.CENTER)
        shortcut.connect("clicked", self._on_shortcut)
        row.append(shortcut)
        card.append(row)

        row = Gtk.Box(spacing=12)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        texts.append(_label("Pasta do hub", "card-title"))
        path = _label(str(ROOT), "path")
        path.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        texts.append(path)
        row.append(texts)
        open_button = Gtk.Button(label="Abrir", valign=Gtk.Align.CENTER)
        open_button.connect("clicked", lambda *_: open_folder(ROOT))
        row.append(open_button)
        card.append(row)

        if not IS_WINDOWS and not has_libfuse2():
            card.append(_label(
                "Aviso: a biblioteca libfuse2 não está instalada. O AppImage do mGBA "
                "vai abrir em modo de extração, que é um pouco mais lento. "
                "Para abrir mais rápido: sudo apt install libfuse2t64",
                "note", wrap=True,
            ))
        self.general_status = _label("", "note", wrap=True)
        card.append(self.general_status)
        return card

    def _on_fullscreen(self, switch, _param):
        """Liga/desliga a tela cheia dos emuladores."""
        try:
            self.app.config.fullscreen = switch.get_active()
        except ConfigError as error:
            self.general_status.set_text(str(error))

    def _on_click_sound(self, switch, _param):
        """Liga/desliga o som de clique da prateleira."""
        self.app.sound.enabled = switch.get_active()
        if switch.get_active():
            self.app.sound.click()  # amostra do som ao ligar
        try:
            self.app.config.click_sound = switch.get_active()
        except ConfigError as error:
            self.general_status.set_text(str(error))

    def _on_shortcut(self, _button):
        """Cria o atalho do My Games no menu de aplicativos."""
        try:
            path = install_menu_shortcut()
            self.general_status.set_text(f"Atalho criado em {display_path(path)}.")
        except OSError as error:
            self.general_status.set_text(f"Não foi possível criar o atalho: {error}")

    # ---- Cartão de cada console ---------------------------------------------
    def _rebuild(self, console, message=""):
        """Refaz o cartão de um console (depois de mudar algo nele)."""
        holder = self.cards[console.id]
        while (child := holder.get_first_child()) is not None:
            holder.remove(child)
        holder.append(self._console_card(console, message))

    def _console_card(self, console, message):
        """Cartão de um console: emulador (baixar, escolher, automático), BIOS e pastas de jogos."""
        state = self.app.emulator_state(console)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.add_css_class("card")

        head = Gtk.Box(spacing=14)
        if console.art.is_file():
            thumb = Gtk.Picture.new_for_filename(str(console.art))
            thumb.set_content_fit(Gtk.ContentFit.CONTAIN)
            thumb.set_size_request(64, 48)
            thumb.add_css_class("mini-thumb")
            head.append(thumb)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        names.append(_label(console.name, "card-title"))
        names.append(_label(f"Recomendado: {console.download.emulator}", "note"))
        head.append(names)
        chip = _label(state.label, "chip")
        chip.add_css_class(state.css)
        chip.set_valign(Gtk.Align.CENTER)
        head.append(chip)
        card.append(head)

        # Emulador
        card.append(_label("EMULADOR", "field-label"))
        emulator = state.emulator
        if emulator is not None:
            origin = "detectado automaticamente" if emulator.automatic else "escolhido manualmente"
            text = f"{emulator.name} — {emulator.description} ({origin})"
        else:
            where = f"Emuladores/{console.folder}/Windows" if IS_WINDOWS else f"Emuladores/{console.folder}"
            kind = "o .exe" if IS_WINDOWS else "um AppImage"
            text = f"Nenhum emulador. Baixe o {console.download.emulator} ou coloque {kind} em {where}."
        path = _label(text, "path", wrap=True)
        card.append(path)

        buttons = Gtk.Box(spacing=8)
        download_label = "Atualizar" if emulator and emulator.automatic else "Baixar"
        download = Gtk.Button(label=f"{download_label} {console.download.emulator}")
        if emulator is None:
            download.add_css_class("primary")
        download.set_sensitive(console.id not in self.downloading)
        download.connect("clicked", self._on_download, console)
        if console.download.windows if IS_WINDOWS else console.download.repo:
            buttons.append(download)
        choose = Gtk.Button(label="Escolher arquivo…")
        choose.connect("clicked", self._on_choose_emulator, console)
        buttons.append(choose)
        if emulator is not None and not emulator.automatic:
            automatic = Gtk.Button(label="Usar automático")
            automatic.set_tooltip_text("Volta a usar o emulador que está em Emuladores/")
            automatic.connect("clicked", self._on_clear_emulator, console)
            buttons.append(automatic)
        buttons.append(icon_button("folder-open-symbolic", f"Abrir Emuladores/{console.folder}", lambda *_: open_folder(console.emulator_dir)))
        card.append(buttons)

        progress = Gtk.ProgressBar(visible=console.id in self.downloading)
        card.append(progress)
        card.progress = progress
        if console.download.note:
            card.append(_label(console.download.note, "note", wrap=True))
        if console.bios_note:
            bios = Gtk.Box(spacing=8)
            bios.append(_label(console.bios_note, "note", wrap=True, hexpand=True))
            bios.append(icon_button("folder-open-symbolic", "Abrir pasta da BIOS", lambda *_: open_folder(console.emulator_dir / "bios")))
            card.append(bios)

        # Pastas de jogos
        card.append(_label("PASTAS DE JOGOS", "field-label"))
        folders = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        folders.append(self._folder_row(console, console.games_dir, removable=False))
        for folder in self.app.config.game_folders(console.id):
            folders.append(self._folder_row(console, folder, removable=True))
        card.append(folders)
        add = Gtk.Button(label="Adicionar pasta de jogos…", halign=Gtk.Align.START)
        add.connect("clicked", self._on_add_folder, console)
        card.append(add)

        status = _label(message, "note", wrap=True)
        status.set_visible(bool(message))
        card.append(status)
        card.status = status
        return card

    def _folder_row(self, console, folder, removable):
        """Linha de uma pasta de jogos, com botões de abrir e remover."""
        row = Gtk.Box(spacing=8)
        exists = Path(folder).is_dir()
        suffix = " (pasta padrão)" if not removable else ("" if exists else " — não encontrada")
        label = _label("• " + display_path(folder) + suffix, "path", hexpand=True)
        label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        label.set_tooltip_text(str(folder))
        row.append(label)
        if exists or not removable:
            row.append(icon_button("folder-open-symbolic", "Abrir pasta", lambda *_: open_folder(folder)))
        if removable:
            row.append(icon_button("user-trash-symbolic", "Remover da lista (não apaga nada)", lambda *_: self._remove_folder(console, folder)))
        return row

    # ---- Ações ---------------------------------------------------------------
    def _changed(self, console, message):
        """Algo mudou num console: refaz o cartão e avisa a janela principal."""
        self._rebuild(console, message)
        self.app.refresh_console(console)

    def _on_choose_emulator(self, _button, console):
        """Escolher manualmente o arquivo do emulador."""
        dialog = Gtk.FileDialog(title=f"Escolha o emulador de {console.name}")
        dialog.set_initial_folder(_gfile(console.emulator_dir))

        def chosen(dialog, result):
            """Arquivo escolhido: salva como emulador do console."""
            try:
                file = dialog.open_finish(result)
            except GLib.Error:
                return
            path = Path(file.get_path())
            try:
                self.app.config.set_emulator(console.id, path)
                self._changed(console, f"Emulador definido: {display_path(path)}")
            except ConfigError as error:
                self._changed(console, str(error))

        dialog.open(self, None, chosen)

    def _on_clear_emulator(self, _button, console):
        """Volta a detectar o emulador automaticamente."""
        try:
            self.app.config.set_emulator(console.id, None)
            self._changed(console, "Voltando a usar o emulador da pasta Emuladores/.")
        except ConfigError as error:
            self._changed(console, str(error))

    def _on_add_folder(self, _button, console):
        """Escolher uma pasta de jogos extra."""
        dialog = Gtk.FileDialog(title=f"Pasta com jogos de {console.name}")

        def chosen(dialog, result):
            """Pasta escolhida: adiciona ao console."""
            try:
                folder = dialog.select_folder_finish(result)
            except GLib.Error:
                return
            try:
                self.app.config.add_game_folder(console.id, folder.get_path())
                self._changed(console, "Pasta adicionada.")
            except ConfigError as error:
                self._changed(console, str(error))

        dialog.select_folder(self, None, chosen)

    def _remove_folder(self, console, folder):
        """Remove uma pasta de jogos extra."""
        try:
            self.app.config.remove_game_folder(console.id, folder)
            self._changed(console, "Pasta removida da lista. Os arquivos não foram apagados.")
        except ConfigError as error:
            self._changed(console, str(error))

    def _on_download(self, _button, console):
        """Baixa a versão mais recente do emulador recomendado, mostrando o progresso."""
        self.downloading.add(console.id)
        self._rebuild(console, f"Procurando a versão mais recente do {console.download.emulator}…")
        card = self.cards[console.id].get_first_child()

        def progress(fraction, text):
            """Chamado pela thread do download: atualiza a barra de progresso."""
            def update():
                """De volta à interface: mostra o progresso."""
                current = self.cards[console.id].get_first_child()
                current.progress.set_visible(True)
                current.progress.set_fraction(fraction)
                current.status.set_visible(True)
                current.status.set_text(text)
                return False

            GLib.idle_add(update)

        def done(path, error):
            """Download terminou: avisa e atualiza o cartão."""
            self.downloading.discard(console.id)
            if error:
                message = f"Falha ao baixar: {error}"
            else:
                message = f"{console.download.emulator} pronto em {display_path(path)}"
            self._changed(console, message)

        card.progress.set_visible(True)
        run_in_thread(lambda: downloader.download_emulator(console, progress), done)


def _label(text, css=None, wrap=False, hexpand=False):
    """Atalho para criar um texto com estilo."""
    label = Gtk.Label(label=text, xalign=0, wrap=wrap, hexpand=hexpand)
    if wrap:
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if css:
        label.add_css_class(css)
    return label


def _gfile(path):
    """Converte um caminho em arquivo do GIO (para os diálogos de escolha)."""
    from gi.repository import Gio

    Path(path).mkdir(parents=True, exist_ok=True)
    return Gio.File.new_for_path(str(path))


def _point(x, y):
    """Atalho para criar um ponto (x, y)."""
    from gi.repository import Graphene

    point = Graphene.Point()
    point.x, point.y = x, y
    return point
