"""Detecção e inicialização dos emuladores portáteis.

Regras principais:
- O emulador de cada console é procurado em Emuladores/<Console>/ (AppImage ou
  executável). Nada precisa ser instalado no sistema.
- As configurações, saves e memory cards de cada emulador ficam em
  Emuladores/<Console>/dados, então viajam junto com o pendrive.
- Antes de abrir o jogo, o controle conectado é configurado no emulador
  (veja controllers.py).
- Pendrives FAT32 não guardam permissão de execução; nesses casos o binário é
  copiado para o cache local e executado de lá.
- AppImages antigos precisam de libfuse2. Se ela não existir, o hub usa o modo
  APPIMAGE_EXTRACT_AND_RUN, que funciona sem FUSE.
- Se mesmo assim o AppImage não conseguir montar (FUSE ausente ou bloqueado),
  ele é extraído uma única vez no cache local deste computador e passa a abrir
  de lá (extract_appimage).
- Se a pasta do hub foi movida/copiada/descompactada em outro lugar, os caminhos
  antigos gravados nas configurações dos emuladores são corrigidos antes de
  abrir (veja portable.py).
"""
import ctypes.util
import os
import re
import shlex
import shutil
import stat
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import big_files, controllers, portable
from .paths import CACHE_DIR, GAMES_FOLDER_NAME, IS_WINDOWS, LOGS_DIR, WINDOWS_EMULATOR_FOLDER, display_path

SKIPPED_FOLDERS = {"dados", "bios", "saves", "cores", "autoconfig", "__pycache__", GAMES_FOLDER_NAME.casefold()}
NOT_PROGRAMS = {".txt", ".md", ".ini", ".json", ".png", ".jpg", ".so", ".zsync", ".part", ".log", ".cfg", ".xml"}


# ---------------------------------------------------------------------------
# Perfis: como cada emulador conhecido recebe o jogo e a opção de tela cheia.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Profile:
    """Jeito de abrir um emulador: argumentos, preparação antes de abrir e variáveis de ambiente."""

    key: str
    name: str
    matches: tuple

    def arguments(self, game, fullscreen, data_dir, program):
        """Argumentos da linha de comando para abrir o jogo (padrão: só o caminho do jogo)."""
        return [str(game)] if game else []

    def prepare(self, data_dir, console, program):
        """Ajustes antes de abrir (BIOS, controle...). O padrão não faz nada."""
        pass

    def environment(self):
        """Variáveis de ambiente extras para o emulador."""
        return {}


# SDL2 em modo joystick cru precisa do caminho evdev para bater com o mapeamento calculado (Linux).
RAW_SDL_ENV = {} if IS_WINDOWS else {"SDL_JOYSTICK_HIDAPI": "0"}


def _relative(target, start):
    """Caminho relativo com "/" (vale no Linux e no Windows), para configs portáteis."""
    return os.path.relpath(target, start).replace("\\", "/")


def _mark_portable(program, name):
    """Cria o arquivo-sinal que deixa o emulador no modo portátil (dados ao lado do .exe)."""
    marker = Path(program).parent / name
    if not marker.exists():
        marker.write_text("", encoding="utf-8")


class PCSX2(Profile):
    """PlayStation 2 (PCSX2): BIOS da pasta do hub e controle configurados automaticamente.

    Linux: dados em Emuladores/PlayStation 2/dados/config/PCSX2.
    Windows: modo portátil (dados ao lado do pcsx2-qt.exe), mas memory cards e
    saves rápidos apontam para a pasta do Linux: o progresso é o mesmo nos dois.
    """

    def arguments(self, game, fullscreen, data_dir, program):
        """Abre o jogo direto (-batch fecha o PCSX2 quando o jogo termina)."""
        if not game:
            return []
        # -batch fecha o PCSX2 quando o jogo termina e o hub volta sozinho.
        return ["-batch"] + (["-fullscreen"] if fullscreen else []) + ["--", str(game)]

    @staticmethod
    def _root(data_dir, program):
        """Pasta de dados do PCSX2 neste sistema."""
        return Path(program).parent if IS_WINDOWS else data_dir / "config" / "PCSX2"

    def prepare(self, data_dir, console, program):
        """Aponta a BIOS (e no Windows os memory cards compartilhados) e liga o controle."""
        root = self._root(data_dir, program)
        ini = root / "inis" / "PCSX2.ini"
        if IS_WINDOWS:
            _mark_portable(program, "portable.ini")
            linux_root = data_dir / "config" / "PCSX2"
            for name in ("memcards", "sstates"):
                (linux_root / name).mkdir(parents=True, exist_ok=True)
            ini.parent.mkdir(parents=True, exist_ok=True)
            if not ini.exists():
                ini.write_text("[Folders]\n", encoding="utf-8")
            portable.set_keys(ini, "Folders", {
                "MemoryCards": _relative(linux_root / "memcards", root),
                "Savestates": _relative(linux_root / "sstates", root),
            }, separator=" = ")
        self._prepare_bios(root, ini, console)
        controllers.configure_pcsx2(ini)  # vínculos SDL: servem no Linux e no Windows

    @staticmethod
    def _prepare_bios(root, ini, console):
        """Faz o PCSX2 usar a BIOS de Emuladores/PlayStation 2/bios (caminho relativo, portátil)."""
        bios = console.emulator_dir / "bios"
        bios.mkdir(parents=True, exist_ok=True)
        relative_bios = _relative(bios, root)
        bios_files = sorted(
            path.name for path in bios.iterdir()
            if path.suffix.casefold() == ".bin" and path.stat().st_size >= 4 * 1024 * 1024
        )
        if not ini.exists():
            ini.parent.mkdir(parents=True, exist_ok=True)
            text = f"[Folders]\nBios = {relative_bios}\n"
            if bios_files:
                text += f"\n[Filenames]\nBIOS = {bios_files[0]}\n"
            ini.write_text(text, encoding="utf-8")
            return
        if not bios_files:
            # Sem BIOS ainda: pelo menos aponta a pasta, para o PCSX2 achar quando ela for colocada.
            text = ini.read_text(encoding="utf-8", errors="replace")
            if not re.search(r"^\s*Bios\s*=", text, re.MULTILINE):
                portable.set_keys(ini, "Folders", {"Bios": relative_bios}, separator=" = ")
            return
        # Se a BIOS configurada não existir (ex.: pasta fora do pendrive), usa a da pasta do hub.
        lines = ini.read_text(encoding="utf-8", errors="replace").splitlines()
        section, folder_ok, has_bios_line, changed = "", False, False, False
        for line in lines:
            if line.strip().startswith("["):
                section = line.strip()
            elif section == "[Folders]" and line.split("=")[0].strip() == "Bios":
                has_bios_line = True
                value = line.split("=", 1)[1].strip()
                folder = Path(value) if Path(value).is_absolute() else root / value
                folder_ok = folder.resolve() == bios.resolve()
        for index, line in enumerate(lines):
            if line.strip().startswith("["):
                section = line.strip()
            key = line.split("=")[0].strip() if "=" in line else ""
            if section == "[Folders]" and key == "Bios" and not folder_ok:
                lines[index], changed = f"Bios = {relative_bios}", True
            elif section == "[Filenames]" and key == "BIOS":
                current = line.split("=", 1)[1].strip()
                if not folder_ok or current not in bios_files:
                    lines[index], changed = f"BIOS = {bios_files[0]}", True
        if changed:
            ini.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if not has_bios_line:
            portable.set_keys(ini, "Folders", {"Bios": relative_bios}, separator=" = ")
        if "[Filenames]" not in ini.read_text(encoding="utf-8", errors="replace"):
            portable.set_keys(ini, "Filenames", {"BIOS": bios_files[0]}, separator=" = ")


class Snes9x(Profile):
    """Super Nintendo (Snes9x): controle reconfigurado a cada abertura.

    No Windows o Snes9x já é portátil (snes9x.conf ao lado do .exe) e lê os
    controles pelo WinMM; no Linux, pelo SDL2.
    """

    def prepare(self, data_dir, console, program):
        """Configura o controle conectado (ou volta para o teclado se não houver)."""
        if IS_WINDOWS:
            conf = Path(program).parent / "snes9x.conf"
            joysticks = controllers.winmm_joysticks()
            if joysticks:
                controllers.configure_snes9x_windows(conf, joysticks[0])
            else:
                controllers.configure_snes9x_windows_keyboard(conf)
            return
        conf = data_dir / "config" / "snes9x" / "snes9x.conf"
        pads = controllers.gamepads()
        if pads:
            controllers.configure_snes9x(conf, pads[0])
        else:
            controllers.configure_snes9x_keyboard(conf)

    def environment(self):
        """Usa o modo joystick cru do SDL para bater com o mapeamento calculado."""
        return RAW_SDL_ENV


class MGBA(Profile):
    """Game Boy Advance (mGBA): controle reconfigurado a cada abertura; portátil no Windows."""

    def arguments(self, game, fullscreen, data_dir, program):
        """Abre o jogo, em tela cheia se configurado (-f)."""
        if not game:
            return []
        return (["-f"] if fullscreen else []) + [str(game)]

    def prepare(self, data_dir, console, program):
        """Configura o controle conectado (Linux) ou o modo portátil e os perfis de controle (Windows)."""
        if IS_WINDOWS:
            _mark_portable(program, "portable.ini")
            controllers.configure_mgba_windows(Path(program).parent / "config.ini")
            return
        pads = controllers.gamepads()
        if pads:
            controllers.configure_mgba(data_dir / "config" / "mgba" / "config.ini", pads[0])

    def environment(self):
        """Linux: modo joystick cru do SDL. Windows: controles Xbox fora do driver HIDAPI (ordem fixa)."""
        if IS_WINDOWS:
            # Assim todo controle tipo Xbox chega ao mGBA na ordem do XInput (a da seção geral).
            return {"SDL_JOYSTICK_HIDAPI_XBOX": "0", "SDL_JOYSTICK_HIDAPI_XBOX_360": "0",
                    "SDL_JOYSTICK_HIDAPI_XBOX_ONE": "0"}
        return RAW_SDL_ENV


class Dolphin(Profile):
    """GameCube (Dolphin): pasta de usuário própria dentro do hub (-u) e controle automático.

    A mesma pasta dados/dolphin é usada no Linux e no Windows: saves e
    memory cards são os mesmos nos dois sistemas.
    """

    def arguments(self, game, fullscreen, data_dir, program):
        """Usa a pasta de dados do hub e abre o jogo direto (-b -e), em tela cheia se configurado."""
        arguments = ["-u", str(data_dir / "dolphin")]
        if game:
            arguments += ["-b"]
            if fullscreen:
                arguments += ["-C", "Dolphin.Display.Fullscreen=True"]
            arguments += ["-e", str(game)]
        return arguments

    def prepare(self, data_dir, console, program):
        """Configura o controle conectado (XInput ou SDL no Windows, evdev no Linux) ou volta para o teclado."""
        config_dir = data_dir / "dolphin" / "Config"
        if IS_WINDOWS:
            name = None
            if not controllers.xinput_connected():
                # PlayStation, Switch e outros: o nome exato que o SDL do Dolphin vai mostrar,
                # lido pelo SDL3.dll que vem com o emulador de outro console (o RMG).
                for library in sorted(console.emulator_dir.parent.glob(f"*/{WINDOWS_EMULATOR_FOLDER}/SDL3.dll")):
                    names = controllers.sdl3_gamepad_names(library)
                    if names:
                        name = names[0]
                        break
            controllers.configure_dolphin_xinput(config_dir, name)
            return
        pads = controllers.gamepads()
        if pads:
            controllers.configure_dolphin(config_dir, pads[0])
        else:
            controllers.restore_dolphin_keyboard(config_dir)


class RMG(Profile):
    """Nintendo 64 (RMG): controle automático e caminhos de cache corrigidos.

    No Windows o RMG portátil guarda a configuração em Config/ ao lado do RMG.exe;
    os saves apontam para a pasta do Linux, então o progresso é o mesmo nos dois.
    """

    def arguments(self, game, fullscreen, data_dir, program):
        """Abre o jogo e fecha o RMG quando o jogo termina (--quit-after-emulation)."""
        if not game:
            return []
        return (["--fullscreen"] if fullscreen else []) + ["--quit-after-emulation", str(game)]

    def prepare(self, data_dir, console, program):
        """Liga o controle, aponta os saves para o hub e corrige o cache de texturas."""
        saves = data_dir / "data" / "RMG"
        if IS_WINDOWS:
            config = Path(program).parent / "Config"
            (saves / "Save" / "State").mkdir(parents=True, exist_ok=True)
            cfg = config / "mupen64plus.cfg"
            if not cfg.exists():
                cfg.parent.mkdir(parents=True, exist_ok=True)
                cfg.write_text("[Core]\n", encoding="utf-8")
            quote = lambda path: f'"{path}"'  # noqa: E731
            portable.set_keys(cfg, "Core", {
                "SaveStatePath": quote(saves / "Save" / "State"),
                "SaveSRAMPath": quote(saves / "Save" / "State"),
                "ScreenshotPath": quote(saves / "Screenshots"),
            }, separator=" = ")
            controllers.configure_rmg(cfg)
            return
        controllers.configure_rmg(data_dir / "config" / "RMG" / "mupen64plus.cfg")
        # O plugin de vídeo guarda o cache de texturas na pasta ~/.cache de quem
        # usou o hub por último; em outro computador/usuário essa pasta não existe.
        gliden = data_dir / "config" / "RMG" / "GLideN64.ini"
        if gliden.exists():
            cache = Path.home() / ".cache" / "RMG"
            text = gliden.read_text(encoding="utf-8", errors="replace")
            if f"txCachePath={cache}/" not in text or f"txDumpPath={cache}/" not in text:
                portable.set_keys(gliden, "User", {
                    "textureFilter\\txCachePath": str(cache / "cache"),
                    "textureFilter\\txDumpPath": str(cache / "texture_dump"),
                })


class MAME(Profile):
    """MAME (Neo Geo/arcade): o jogo é identificado pelo nome do arquivo, ex.: kof98.zip."""

    def arguments(self, game, fullscreen, data_dir, program):
        """Diz ao MAME onde ficam BIOS, jogos e dados, e qual jogo abrir (pelo nome do romset)."""
        console_dir = data_dir.parent
        rompath = [console_dir / "bios", console_dir / GAMES_FOLDER_NAME]
        if game:
            rompath.insert(0, Path(game).parent)
        arguments = [
            "-rompath", ";".join(dict.fromkeys(str(path) for path in rompath)),
            "-inipath", str(data_dir / "ini"),
            "-cfg_directory", str(data_dir / "cfg"),
            "-nvram_directory", str(data_dir / "nvram"),
            "-state_directory", str(data_dir / "sta"),
            "-snapshot_directory", str(data_dir / "snap"),
            "-diff_directory", str(data_dir / "diff"),
            "-homepath", str(data_dir),
            "-joystick",
            "-skip_gameinfo",
            "-nowindow" if fullscreen else "-window",
        ]
        if game:
            arguments.append(Path(game).stem)
        return arguments

    def prepare(self, data_dir, console, program):
        """Cria as pastas de dados do MAME."""
        (console.emulator_dir / "bios").mkdir(parents=True, exist_ok=True)
        for name in ("ini", "cfg", "nvram", "sta", "snap", "diff"):
            (data_dir / name).mkdir(parents=True, exist_ok=True)


class RetroArch(Profile):
    """RetroArch portátil. No Neo Geo: Geolith para .neo e FBNeo para romsets (kof98.zip).

    O mesmo retroarch.cfg serve aos dois sistemas: as pastas são reescritas a
    cada abertura para o sistema atual. Os núcleos ficam ao lado do programa
    (cores/*.so no Linux, Windows/cores/*.dll no Windows).
    """

    def arguments(self, game, fullscreen, data_dir, program):
        """Usa o retroarch.cfg do hub e escolhe o núcleo certo para o jogo."""
        arguments = ["--config", str(data_dir / "retroarch.cfg")]
        if not game:
            return arguments
        game, core = self._content_and_core(Path(game), Path(program).parent / "cores")
        if core is not None:
            arguments += ["-L", str(core)]
        if fullscreen:
            arguments.append("-f")
        return arguments + [str(game)]

    @staticmethod
    def _content_and_core(game, cores_dir):
        """Escolhe o arquivo e o núcleo: Geolith para .neo, FBNeo para romsets (kof98.zip)."""
        extension = ".dll" if IS_WINDOWS else ".so"
        cores = {path.name.split("_libretro")[0]: path for path in sorted(cores_dir.glob(f"*_libretro{extension}"))}
        suffix = game.suffix.casefold()
        if suffix == ".zip":
            neo = _neo_inside_zip(game)
            if neo is not None:
                return neo, cores.get("geolith")
        if suffix in (".neo", ".chd", ".cue") and "geolith" in cores:
            return game, cores["geolith"]
        return game, cores.get("fbneo") or next(iter(cores.values()), None)

    def prepare(self, data_dir, console, program):
        """Escreve no retroarch.cfg as pastas do hub (BIOS, núcleos, saves) e opções do Neo Geo."""
        emulator_dir = console.emulator_dir
        program_dir = Path(program).parent
        bios = emulator_dir / "bios"
        bios.mkdir(parents=True, exist_ok=True)
        for name in ("saves", "states", "screenshots", "cache"):
            (data_dir / name).mkdir(parents=True, exist_ok=True)
        # O FBNeo procura a BIOS na pasta system/fbneo.
        if (bios / "neogeo.zip").exists():
            (bios / "fbneo").mkdir(exist_ok=True)
            target = bios / "fbneo" / "neogeo.zip"
            if not target.exists() or target.stat().st_size != (bios / "neogeo.zip").stat().st_size:
                shutil.copyfile(bios / "neogeo.zip", target)
        q = lambda path: f'"{path}"'  # noqa: E731
        # Perfis de controle: os que vêm com o RetroArch do Windows, ou a pasta do hub no Linux.
        autoconfig = program_dir / "autoconfig" if IS_WINDOWS else emulator_dir / "autoconfig"
        _set_cfg(data_dir / "retroarch.cfg", {
            "system_directory": q(bios),
            "libretro_directory": q(program_dir / "cores"),
            "joypad_autoconfig_dir": q(autoconfig),
            "savefile_directory": q(data_dir / "saves"),
            "savestate_directory": q(data_dir / "states"),
            "screenshot_directory": q(data_dir / "screenshots"),
            "cache_directory": q(data_dir / "cache"),
            "core_options_path": q(data_dir / "retroarch-core-options.cfg"),
            "global_core_options": '"true"',
            "input_autodetect_enable": '"true"',
            "input_joypad_driver": '"xinput"' if IS_WINDOWS else '"udev"',
            "menu_driver": '"rgui"',
            "input_menu_toggle_gamepad_combo": '"2"',  # L3 + R3 abre o menu do RetroArch
            "input_quit_gamepad_combo": '"0"',  # o hub já trata SELECT + START
            "quit_press_twice": '"false"',
            "config_save_on_exit": '"false"',
            "pause_nonactive": '"false"',
        })
        # Geolith: modo console (aes.zip) ou arcade (neogeo.zip), conforme a BIOS presente.
        system = "aes" if (bios / "aes.zip").exists() or not (bios / "neogeo.zip").exists() else "mvs"
        _set_cfg(data_dir / "retroarch-core-options.cfg", {"geolith_system_type": f'"{system}"'})


def _set_cfg(path, values):
    """Atualiza chaves no formato do RetroArch (chave = "valor"), mantendo as outras."""
    path = Path(path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
    pending = dict(values)
    for index, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in pending:
            lines[index] = f"{key} = {pending.pop(key)}"
    lines += [f"{key} = {value}" for key, value in pending.items()]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _neo_inside_zip(path):
    """Extrai (uma vez) o .neo de dentro de um .zip para o cache local."""
    import zipfile

    try:
        with zipfile.ZipFile(path) as archive:
            members = [m for m in archive.infolist() if m.filename.casefold().endswith(".neo")]
            if not members:
                return None
            member = members[0]
            stat_info = path.stat()
            target = CACHE_DIR / "neo" / f"{stat_info.st_size}-{int(stat_info.st_mtime)}-{path.stem}.neo"
            if not target.exists() or target.stat().st_size != member.file_size:
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_suffix(".part")
                with archive.open(member) as source, open(temporary, "wb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                temporary.replace(target)
            return target
    except (OSError, zipfile.BadZipFile):
        return None


PROFILES = [
    PCSX2("pcsx2", "PCSX2", ("pcsx2",)),
    Snes9x("snes9x", "Snes9x", ("snes9x",)),
    Profile("bsnes", "bsnes", ("bsnes",)),
    MGBA("mgba", "mGBA", ("mgba",)),
    Dolphin("dolphin", "Dolphin", ("dolphin",)),
    RMG("rmg", "RMG", ("rmg",)),
    Profile("simple64", "simple64", ("simple64",)),
    Profile("gopher64", "gopher64", ("gopher64",)),
    Profile("mupen64plus", "Mupen64Plus", ("mupen64plus",)),
    RetroArch("retroarch", "RetroArch", ("retroarch",)),
    MAME("mame", "MAME", ("mame",)),
]


def profile_for(path_or_name):
    """Descobre qual perfil usar pelo nome do arquivo do emulador (ex.: "pcsx2" -> PCSX2)."""
    name = Path(str(path_or_name)).name.casefold()
    for profile in PROFILES:
        if any(token in name for token in profile.matches):
            return profile
    stem = Path(str(path_or_name)).stem
    return Profile("generic", stem.split("-")[0].split("_")[0] or stem, ())


# ---------------------------------------------------------------------------
# Descoberta
# ---------------------------------------------------------------------------
@dataclass
class Emulator:
    """Emulador encontrado para um console: arquivo (ou Flatpak/atalho) e perfil."""
    kind: str  # "file", "desktop" ou "flatpak"
    target: object  # Path ou ID do Flatpak
    profile: Profile
    automatic: bool

    @property
    def name(self):
        """Nome amigável do emulador (ex.: "PCSX2")."""
        return self.profile.name

    @property
    def description(self):
        """Onde o emulador está, para mostrar nas Configurações."""
        if self.kind == "flatpak":
            return f"Flatpak {self.target}"
        return display_path(big_files.hub_path(self.target))


def _is_elf(path):
    """O arquivo é um programa do Linux (cabeçalho ELF)?"""
    try:
        with open(path, "rb") as handle:
            return handle.read(4) == b"\x7fELF"
    except OSError:
        return False


# Programas que vêm junto com os emuladores no Windows mas não são o emulador.
WINDOWS_HELPERS = ("unins", "setup", "update", "crash", "helper", "vc_redist", "dxsetup", "qtwebengine", "7z")


def is_program(path):
    """O arquivo parece um emulador que dá para executar neste sistema?

    Linux: AppImage, programa (ELF) ou atalho .desktop.
    Windows: arquivo .exe que não seja instalador/atualizador.
    """
    path = Path(path)
    if not path.is_file() or path.suffix.casefold() in NOT_PROGRAMS or big_files.is_part(path):
        return False
    if IS_WINDOWS:
        name = path.name.casefold()
        return name.endswith(".exe") and not any(helper in name for helper in WINDOWS_HELPERS)
    if path.suffix.casefold() in (".appimage", ".desktop"):
        return True
    # Em FAT32 não existe bit de execução, então olhamos o cabeçalho do arquivo.
    return _is_elf(path) or (os.access(path, os.X_OK) and path.suffix in ("", ".sh"))


def programs_in(folder):
    """Programas dentro da pasta do console (e um nível de subpastas), ignorando dados/BIOS/jogos.

    Emuladores guardados em partes (grandes demais para o GitHub) entram como
    a cópia inteira, juntada no cache local.
    """
    folder = Path(folder)
    if not folder.is_dir():
        return []
    found = []
    skipped = SKIPPED_FOLDERS if IS_WINDOWS else SKIPPED_FOLDERS | {WINDOWS_EMULATOR_FOLDER.casefold()}
    for entry in sorted(folder.iterdir()):
        if entry.is_dir() and entry.name.casefold() not in skipped and not entry.name.startswith("."):
            found += [child for child in sorted(entry.iterdir()) if is_program(child)]
        elif is_program(entry):
            found.append(entry)
    found += [path for path in big_files.whole_files(folder) if is_program(path)]
    return found


def _rank(path, preferred):
    """Nota para escolher entre vários programas: o emulador recomendado e AppImages mais novos primeiro."""
    profile = profile_for(path)
    name = path.stem.casefold()
    return (
        profile.name.casefold() == preferred.casefold(),
        # Versões auxiliares (mgba-sdl, DolphinTool...) perdem para o programa principal.
        not any(extra in name for extra in ("tool", "sdl", "cli", "nogui")),
        path.suffix.casefold() == ".appimage",
        path.stat().st_mtime,
    )


def detect(console):
    """Procura sozinho o emulador na pasta do console."""
    candidates = programs_in(console.emulator_dir)
    if IS_WINDOWS:
        # No Windows só vale um .exe de emulador conhecido (evita abrir ferramentas que vêm junto).
        candidates = [path for path in candidates if profile_for(path).key != "generic"]
    if not candidates:
        return None
    return max(candidates, key=lambda path: _rank(path, console.download.emulator))


def flatpak_id_from_folder(folder):
    """Se a pasta escolhida for de um Flatpak (~/.var/app/<id>), devolve o id dele."""
    root = (Path.home() / ".var" / "app").resolve()
    try:
        relative = Path(folder).resolve().relative_to(root)
    except ValueError:
        return None
    return relative.parts[0] if len(relative.parts) == 1 else None


def _usable_here(path):
    """O emulador escolhido nas Configurações pode rodar neste sistema?"""
    path = Path(path)
    if not path.exists():
        return False
    if IS_WINDOWS:
        return path.is_dir() or path.suffix.casefold() == ".exe"
    return path.suffix.casefold() != ".exe"


def find_emulator(console, config):
    """Emulador do console: o escolhido nas Configurações ou o encontrado na pasta."""
    override = config.emulator_override(console.id)
    if isinstance(override, str):
        app_id = override.split(":", 1)[1]
        return Emulator("flatpak", app_id, profile_for(app_id), False)
    if override is not None and not _usable_here(override):
        override = None  # escolhido no outro sistema (ex.: um AppImage aberto no Windows)
    if override is not None:
        target = override
        if target.is_dir():
            app_id = flatpak_id_from_folder(target)
            if app_id:
                return Emulator("flatpak", app_id, profile_for(app_id), False)
            programs = programs_in(target)
            target = programs[0] if programs else target
        kind = "desktop" if target.suffix.casefold() == ".desktop" else "file"
        return Emulator(kind, target, profile_for(target), False)
    detected = detect(console)
    if detected is None:
        return None
    kind = "desktop" if detected.suffix.casefold() == ".desktop" else "file"
    return Emulator(kind, detected, profile_for(detected), True)


def emulator_problem(emulator):
    """Retorna um texto explicando por que o emulador não vai abrir, ou None."""
    if emulator is None:
        return "Nenhum emulador encontrado."
    if emulator.kind == "flatpak":
        if shutil.which("flatpak") is None:
            return "O Flatpak não está instalado neste computador."
        return None
    if not Path(emulator.target).is_file():
        return f"Arquivo não encontrado: {emulator.description}"
    return None


def missing_bios(console, emulator):
    """Explica qual BIOS falta, antes de o emulador abrir e dar um erro confuso."""
    if emulator is None or emulator.kind != "file":
        return None
    bios = console.emulator_dir / "bios"
    files = {path.name.casefold(): path for path in bios.iterdir()} if bios.is_dir() else {}
    if emulator.profile.key == "pcsx2":
        if not any(name.endswith(".bin") and path.stat().st_size >= 4 * 1024 * 1024 for name, path in files.items()):
            return "Falta a BIOS do PlayStation 2. Coloque o arquivo .bin em Emuladores/PlayStation 2/bios."
    if console.id == "neo-geo" and emulator.profile.key in ("retroarch", "mame"):
        games_dir = console.games_dir
        if not ({"aes.zip", "neogeo.zip"} & set(files)) and not (games_dir / "neogeo.zip").exists():
            return "Falta a BIOS do Neo Geo. Coloque aes.zip ou neogeo.zip em Emuladores/Neo Geo/bios (sem descompactar)."
    return None


# ---------------------------------------------------------------------------
# Preparação para executar
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def has_libfuse2():
    """O computador tem a libfuse2 (necessária para AppImages antigos)?"""
    return bool(ctypes.util.find_library("fuse"))


@lru_cache(maxsize=32)
def _appimage_needs_libfuse2(path, _mtime):
    """O AppImage é do tipo antigo, que precisa da libfuse2?"""
    try:
        with open(path, "rb") as handle:
            return b"libfuse.so.2" in handle.read(3 * 1024 * 1024)
    except OSError:
        return False


def needs_extract_and_run(path):
    """O AppImage precisa abrir no modo sem FUSE (APPIMAGE_EXTRACT_AND_RUN)?"""
    path = Path(path)
    if path.suffix.casefold() != ".appimage" or has_libfuse2():
        return False
    return _appimage_needs_libfuse2(str(path), path.stat().st_mtime)


def runnable_copy(path):
    """Garante permissão de execução; se o disco não permitir, usa uma cópia no cache."""
    path = Path(path)
    if IS_WINDOWS:
        return path  # no Windows não existe permissão de execução
    if os.access(path, os.X_OK):
        return path
    try:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        if os.access(path, os.X_OK):
            return path
    except OSError:
        pass
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    source = path.stat()
    cached = CACHE_DIR / "bin" / f"{source.st_size}-{int(source.st_mtime)}-{path.name}"
    if not cached.exists():
        cached.parent.mkdir(parents=True, exist_ok=True)
        temporary = cached.with_suffix(cached.suffix + ".part")
        shutil.copyfile(path, temporary)
        temporary.chmod(0o755)
        temporary.replace(cached)
    return cached


# ---------------------------------------------------------------------------
# AppImage sem FUSE: extrai uma vez no cache local e abre o AppRun extraído.
# ---------------------------------------------------------------------------
MOUNT_FAILURES = ("Cannot mount AppImage", "fuse: device not found", "fusermount: ")


def _extracted_folder(program):
    """Pasta no cache local onde este AppImage é extraído (muda se o arquivo mudar)."""
    program = Path(program)
    info = program.stat()
    return CACHE_DIR / "appimages" / f"{info.st_size}-{int(info.st_mtime)}-{program.name}"


def extracted_apprun(program):
    """AppRun já extraído deste AppImage neste computador, ou None."""
    program = Path(program)
    if program.suffix.casefold() != ".appimage":
        return None
    try:
        apprun = _extracted_folder(program) / "AppRun"
    except OSError:
        return None
    return apprun if apprun.exists() else None


def appimage_mount_failed(program, log_path):
    """O emulador fechou porque o AppImage não conseguiu montar (FUSE)?"""
    if Path(str(program)).suffix.casefold() != ".appimage" or extracted_apprun(program):
        return False
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")[-4000:]
    except (OSError, TypeError):
        return False
    return any(marker in text for marker in MOUNT_FAILURES)


def extract_appimage(program):
    """Extrai o AppImage no cache local (bloqueante: chame fora da interface)."""
    program = Path(program)
    target = _extracted_folder(program)
    if (target / "AppRun").exists():
        return target / "AppRun"
    work = target.with_name(target.name + ".part")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    env = dict(os.environ)
    for name in ("GTK_PATH", "GTK_EXE_PREFIX", "GTK_MODULES", "GIO_MODULE_DIR", "LOCPATH"):
        env.pop(name, None)
    try:
        result = subprocess.run(
            [str(runnable_copy(program)), "--appimage-extract"],
            cwd=work,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=900,
        )
        root = work / "squashfs-root"
        if not (root / "AppRun").exists():
            detail = result.stderr.decode(errors="replace").strip()[-300:]
            raise OSError(f"Não consegui extrair {program.name}. {detail}")
        shutil.rmtree(target, ignore_errors=True)
        root.rename(target)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    # Versões antigas do mesmo emulador extraídas antes só ocupam espaço.
    for old in target.parent.glob(f"*-{program.name}"):
        if old != target and old.is_dir():
            shutil.rmtree(old, ignore_errors=True)
    return target / "AppRun"


def _desktop_command(desktop_file, game):
    """Monta o comando de um atalho .desktop escolhido como emulador, com o jogo no lugar certo."""
    from gi.repository import GioUnix  # importado aqui para manter o módulo testável sem GTK

    info = GioUnix.DesktopAppInfo.new_from_filename(str(desktop_file))
    if info is None:
        raise ValueError("O atalho .desktop do emulador não é válido.")
    arguments = []
    for part in shlex.split(info.get_commandline() or ""):
        if part in ("%f", "%F", "%u", "%U"):
            if game:
                arguments.append(str(game) if part in ("%f", "%F") else Path(game).as_uri())
        elif not (len(part) == 2 and part.startswith("%")):
            arguments.append(part)
    if not arguments:
        raise ValueError("O atalho .desktop não tem um comando para executar.")
    return arguments


def _apply_custom_arguments(template, game):
    """Aplica os argumentos personalizados, trocando {jogo} pelo caminho do jogo."""
    arguments = []
    for argument in template:
        if "{jogo}" in argument:
            if game:
                arguments.append(argument.replace("{jogo}", str(game)))
        else:
            arguments.append(argument)
    return arguments


def build_command(console, emulator, game, fullscreen, custom_arguments=None):
    """Monta (argv, env, cwd) para abrir o emulador, opcionalmente com um jogo."""
    env = dict(os.environ)
    # Remove variáveis do Snap do VS Code que quebram apps GTK/Qt.
    for name in ("GTK_PATH", "GTK_EXE_PREFIX", "GTK_MODULES", "GIO_MODULE_DIR", "LOCPATH"):
        env.pop(name, None)
    game = Path(game).resolve() if game else None

    if emulator.kind == "flatpak":
        flatpak = shutil.which("flatpak")
        argv = [flatpak, "run"]
        argv += ["--file-forwarding", emulator.target, "@@", str(game), "@@"] if game else [emulator.target]
        return argv, env, str(Path.home())

    program = Path(emulator.target)
    if emulator.kind == "desktop":
        return _desktop_command(program, game), env, str(Path.home())

    data_dir = console.data_dir
    (data_dir / "config").mkdir(parents=True, exist_ok=True)
    (data_dir / "data").mkdir(parents=True, exist_ok=True)
    try:
        # A pasta do hub pode ter mudado de lugar desde a última vez.
        from .consoles import CONSOLE_LIST

        for path in portable.relocate(data_dir, [c.folder for c in CONSOLE_LIST]):
            print(f"Caminhos atualizados para o local atual do hub: {display_path(path)}")
    except OSError as error:
        print(f"Aviso ao atualizar caminhos de {emulator.name}: {error}")
    try:
        emulator.profile.prepare(data_dir, console, program)
    except (OSError, ValueError, IndexError) as error:
        # Um problema ao preparar o controle não deve impedir o jogo de abrir.
        print(f"Aviso ao preparar {emulator.name}: {error}")
    env.update(emulator.profile.environment())
    if custom_arguments is not None:
        arguments = _apply_custom_arguments(custom_arguments, game)
    else:
        arguments = emulator.profile.arguments(game, fullscreen, data_dir, program)
    if IS_WINDOWS:
        # No Windows cada emulador já fica portátil (ver os perfis): abre o .exe direto.
        return [str(program)] + arguments, env, str(program.parent)
    # Modo portátil: configurações e saves ficam dentro de Emuladores/<Console>/dados.
    env["XDG_CONFIG_HOME"] = str(data_dir / "config")
    env["XDG_DATA_HOME"] = str(data_dir / "data")
    apprun = extracted_apprun(program)
    if apprun is not None:
        # Este computador não monta AppImages: usa a cópia já extraída.
        executable = apprun
        env["APPIMAGE"] = str(program)
        env["APPDIR"] = str(apprun.parent)
    else:
        if needs_extract_and_run(program):
            env["APPIMAGE_EXTRACT_AND_RUN"] = "1"
        executable = runnable_copy(program)
    return [str(executable)] + arguments, env, str(program.parent)


def launch(console, emulator, game, fullscreen, custom_arguments=None):
    """Abre o emulador e grava a saída dele em Sistema/logs/<console>.log."""
    argv, env, cwd = build_command(console, emulator, game, fullscreen, custom_arguments)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"{console.id}.log"
    log = open(log_path, "w", encoding="utf-8", errors="replace")
    log.write("$ " + (subprocess.list2cmdline(argv) if IS_WINDOWS else shlex.join(argv)) + "\n\n")
    log.flush()
    # Grupo de processos próprio: assim dá para fechar o emulador e o que ele abrir.
    if IS_WINDOWS:
        group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        group = {"start_new_session": True}
    try:
        process = subprocess.Popen(
            argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **group
        )
    finally:
        log.close()
    return process, log_path


def stop(process, force=False):
    """Fecha o emulador e os processos que ele abriu (force=True não espera ele salvar)."""
    if process is None or process.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            flags = ["/F"] if force else []
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", *flags],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            import signal

            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
    except OSError:
        pass
