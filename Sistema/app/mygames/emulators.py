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
"""
import ctypes.util
import os
import shlex
import shutil
import stat
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import controllers
from .paths import CACHE_DIR, GAMES_FOLDER_NAME, LOGS_DIR, display_path

SKIPPED_FOLDERS = {"dados", "bios", "saves", "cores", "autoconfig", "__pycache__", GAMES_FOLDER_NAME.casefold()}
NOT_PROGRAMS = {".txt", ".md", ".ini", ".json", ".png", ".jpg", ".so", ".zsync", ".part", ".log", ".cfg", ".xml"}


# ---------------------------------------------------------------------------
# Perfis: como cada emulador conhecido recebe o jogo e a opção de tela cheia.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Profile:
    key: str
    name: str
    matches: tuple

    def arguments(self, game, fullscreen, data_dir, program):
        return [str(game)] if game else []

    def prepare(self, data_dir, console):
        pass

    def environment(self):
        return {}


# SDL2 em modo joystick cru precisa do caminho evdev para bater com o mapeamento calculado.
RAW_SDL_ENV = {"SDL_JOYSTICK_HIDAPI": "0"}


class PCSX2(Profile):
    def arguments(self, game, fullscreen, data_dir, program):
        if not game:
            return []
        # -batch fecha o PCSX2 quando o jogo termina e o hub volta sozinho.
        return ["-batch"] + (["-fullscreen"] if fullscreen else []) + ["--", str(game)]

    def prepare(self, data_dir, console):
        self._prepare_bios(data_dir, console)
        controllers.configure_pcsx2(data_dir / "config" / "PCSX2" / "inis" / "PCSX2.ini")

    def _prepare_bios(self, data_dir, console):
        bios = console.emulator_dir / "bios"
        bios.mkdir(parents=True, exist_ok=True)
        ini = data_dir / "config" / "PCSX2" / "inis" / "PCSX2.ini"
        # Caminho relativo à pasta de dados do PCSX2: aponta para Emuladores/PlayStation 2/bios.
        relative_bios = "../../../bios"
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
            return
        # Se a BIOS configurada não existir (ex.: pasta fora do pendrive), usa a da pasta do hub.
        lines = ini.read_text(encoding="utf-8", errors="replace").splitlines()
        section, folder_ok, changed = "", True, False
        for line in lines:
            if line.strip().startswith("["):
                section = line.strip()
            elif section == "[Folders]" and line.split("=")[0].strip() == "Bios":
                value = line.split("=", 1)[1].strip()
                folder = Path(value) if Path(value).is_absolute() else ini.parent.parent / value
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


class Snes9x(Profile):
    def prepare(self, data_dir, console):
        conf = data_dir / "config" / "snes9x" / "snes9x.conf"
        pads = controllers.gamepads()
        if pads:
            controllers.configure_snes9x(conf, pads[0])
        else:
            controllers.configure_snes9x_keyboard(conf)

    def environment(self):
        return RAW_SDL_ENV


class MGBA(Profile):
    def arguments(self, game, fullscreen, data_dir, program):
        if not game:
            return []
        return (["-f"] if fullscreen else []) + [str(game)]

    def prepare(self, data_dir, console):
        pads = controllers.gamepads()
        if pads:
            controllers.configure_mgba(data_dir / "config" / "mgba" / "config.ini", pads[0])

    def environment(self):
        return RAW_SDL_ENV


class Dolphin(Profile):
    def arguments(self, game, fullscreen, data_dir, program):
        arguments = ["-u", str(data_dir / "dolphin")]
        if game:
            arguments += ["-b"]
            if fullscreen:
                arguments += ["-C", "Dolphin.Display.Fullscreen=True"]
            arguments += ["-e", str(game)]
        return arguments

    def prepare(self, data_dir, console):
        config_dir = data_dir / "dolphin" / "Config"
        pads = controllers.gamepads()
        if pads:
            controllers.configure_dolphin(config_dir, pads[0])
        else:
            controllers.restore_dolphin_keyboard(config_dir)


class RMG(Profile):
    def arguments(self, game, fullscreen, data_dir, program):
        if not game:
            return []
        return (["--fullscreen"] if fullscreen else []) + ["--quit-after-emulation", str(game)]

    def prepare(self, data_dir, console):
        controllers.configure_rmg(data_dir / "config" / "RMG" / "mupen64plus.cfg")


class MAME(Profile):
    """MAME (Neo Geo/arcade): o jogo é identificado pelo nome do arquivo, ex.: kof98.zip."""

    def arguments(self, game, fullscreen, data_dir, program):
        console_dir = program.parent
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

    def prepare(self, data_dir, console):
        (console.emulator_dir / "bios").mkdir(parents=True, exist_ok=True)
        for name in ("ini", "cfg", "nvram", "sta", "snap", "diff"):
            (data_dir / name).mkdir(parents=True, exist_ok=True)


class RetroArch(Profile):
    """RetroArch portátil. No Neo Geo: Geolith para .neo e FBNeo para romsets (kof98.zip).

    O RetroArch já reconhece centenas de controles sozinho (pasta autoconfig).
    """

    def arguments(self, game, fullscreen, data_dir, program):
        arguments = ["--config", str(data_dir / "retroarch.cfg")]
        if not game:
            return arguments
        game, core = self._content_and_core(Path(game), program.parent / "cores")
        if core is not None:
            arguments += ["-L", str(core)]
        if fullscreen:
            arguments.append("-f")
        return arguments + [str(game)]

    @staticmethod
    def _content_and_core(game, cores_dir):
        cores = {path.name.split("_libretro")[0]: path for path in sorted(cores_dir.glob("*_libretro.so"))}
        suffix = game.suffix.casefold()
        if suffix == ".zip":
            neo = _neo_inside_zip(game)
            if neo is not None:
                return neo, cores.get("geolith")
        if suffix in (".neo", ".chd", ".cue") and "geolith" in cores:
            return game, cores["geolith"]
        return game, cores.get("fbneo") or next(iter(cores.values()), None)

    def prepare(self, data_dir, console):
        emulator_dir = console.emulator_dir
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
        q = lambda path: f'"{path}"'
        _set_cfg(data_dir / "retroarch.cfg", {
            "system_directory": q(bios),
            "libretro_directory": q(emulator_dir / "cores"),
            "joypad_autoconfig_dir": q(emulator_dir / "autoconfig"),
            "savefile_directory": q(data_dir / "saves"),
            "savestate_directory": q(data_dir / "states"),
            "screenshot_directory": q(data_dir / "screenshots"),
            "cache_directory": q(data_dir / "cache"),
            "core_options_path": q(data_dir / "retroarch-core-options.cfg"),
            "global_core_options": '"true"',
            "input_autodetect_enable": '"true"',
            "input_joypad_driver": '"udev"',
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
    kind: str  # "file", "desktop" ou "flatpak"
    target: object  # Path ou ID do Flatpak
    profile: Profile
    automatic: bool

    @property
    def name(self):
        return self.profile.name

    @property
    def description(self):
        if self.kind == "flatpak":
            return f"Flatpak {self.target}"
        return display_path(self.target)


def _is_elf(path):
    try:
        with open(path, "rb") as handle:
            return handle.read(4) == b"\x7fELF"
    except OSError:
        return False


def is_program(path):
    path = Path(path)
    if not path.is_file() or path.suffix.casefold() in NOT_PROGRAMS:
        return False
    if path.suffix.casefold() in (".appimage", ".desktop"):
        return True
    # Em FAT32 não existe bit de execução, então olhamos o cabeçalho do arquivo.
    return _is_elf(path) or (os.access(path, os.X_OK) and path.suffix in ("", ".sh"))


def programs_in(folder):
    folder = Path(folder)
    if not folder.is_dir():
        return []
    found = []
    for entry in sorted(folder.iterdir()):
        if entry.is_dir() and entry.name.casefold() not in SKIPPED_FOLDERS and not entry.name.startswith("."):
            found += [child for child in sorted(entry.iterdir()) if is_program(child)]
        elif is_program(entry):
            found.append(entry)
    return found


def _rank(path, preferred):
    profile = profile_for(path)
    return (
        profile.name.casefold() == preferred.casefold(),
        path.suffix.casefold() == ".appimage",
        path.stat().st_mtime,
    )


def detect(console):
    candidates = programs_in(console.emulator_dir)
    if not candidates:
        return None
    return max(candidates, key=lambda path: _rank(path, console.download.emulator))


def flatpak_id_from_folder(folder):
    root = (Path.home() / ".var" / "app").resolve()
    try:
        relative = Path(folder).resolve().relative_to(root)
    except ValueError:
        return None
    return relative.parts[0] if len(relative.parts) == 1 else None


def find_emulator(console, config):
    override = config.emulator_override(console.id)
    if isinstance(override, str):
        app_id = override.split(":", 1)[1]
        return Emulator("flatpak", app_id, profile_for(app_id), False)
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
    return bool(ctypes.util.find_library("fuse"))


@lru_cache(maxsize=32)
def _appimage_needs_libfuse2(path, _mtime):
    try:
        with open(path, "rb") as handle:
            return b"libfuse.so.2" in handle.read(3 * 1024 * 1024)
    except OSError:
        return False


def needs_extract_and_run(path):
    path = Path(path)
    if path.suffix.casefold() != ".appimage" or has_libfuse2():
        return False
    return _appimage_needs_libfuse2(str(path), path.stat().st_mtime)


def runnable_copy(path):
    """Garante permissão de execução; se o disco não permitir, usa uma cópia no cache."""
    path = Path(path)
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


def _desktop_command(desktop_file, game):
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
        emulator.profile.prepare(data_dir, console)
    except (OSError, ValueError, IndexError) as error:
        # Um problema ao preparar o controle não deve impedir o jogo de abrir.
        print(f"Aviso ao preparar {emulator.name}: {error}")
    env.update(emulator.profile.environment())
    # Modo portátil: configurações e saves ficam dentro de Emuladores/<Console>/dados.
    env["XDG_CONFIG_HOME"] = str(data_dir / "config")
    env["XDG_DATA_HOME"] = str(data_dir / "data")
    if needs_extract_and_run(program):
        env["APPIMAGE_EXTRACT_AND_RUN"] = "1"

    executable = runnable_copy(program)
    if custom_arguments is not None:
        arguments = _apply_custom_arguments(custom_arguments, game)
    else:
        arguments = emulator.profile.arguments(game, fullscreen, data_dir, program)
    return [str(executable)] + arguments, env, str(program.parent)


def launch(console, emulator, game, fullscreen, custom_arguments=None):
    argv, env, cwd = build_command(console, emulator, game, fullscreen, custom_arguments)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"{console.id}.log"
    log = open(log_path, "w", encoding="utf-8", errors="replace")
    log.write("$ " + shlex.join(argv) + "\n\n")
    log.flush()
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log.close()
    return process, log_path
