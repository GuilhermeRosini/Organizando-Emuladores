"""Baixa a versão mais recente do emulador recomendado.

Linux: AppImage do GitHub, salvo em Emuladores/<Console>/.
Windows: pacote oficial (.zip/.7z) extraído em Emuladores/<Console>/Windows/.
As duas versões podem ser baixadas de qualquer um dos sistemas (dá para
preparar o pendrive para Windows estando no Linux, e vice-versa).
"""
import json
import re
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from . import big_files
from .paths import IS_WINDOWS, WINDOWS_DIR, WINDOWS_EMULATOR_FOLDER

USER_AGENT = "MyGames-Hub"
SEVEN_ZIP_URL = "https://www.7-zip.org/a/7zr.exe"  # 7-Zip oficial, versão de linha de comando (~600 KB)
RETROARCH_STABLE = "https://buildbot.libretro.com/stable/"
RETROARCH_CORES = "https://buildbot.libretro.com/nightly/windows/x86_64/latest/"


def _request(url):
    """Monta o pedido HTTP com um nome de cliente genérico."""
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})


def _json(url):
    """Baixa e lê um JSON."""
    with urllib.request.urlopen(_request(url), timeout=20) as response:
        return json.load(response)


def latest_asset(repo, pattern, emulator):
    """Procura na versão mais recente do GitHub o arquivo que combina com o padrão."""
    release = _json(f"https://api.github.com/repos/{repo}/releases/latest")
    compiled = re.compile(pattern)
    for asset in release.get("assets", []):
        if compiled.search(asset["name"]):
            return asset["name"], asset["browser_download_url"], asset.get("size", 0), release.get("tag_name", "")
    raise ValueError(f"Não encontrei o arquivo do {emulator} na versão mais recente.")


def _fetch(url, target, progress, label, size=0):
    """Baixa um arquivo mostrando o progresso (grava num .part e troca no final)."""
    temporary = Path(str(target) + ".part")
    with urllib.request.urlopen(_request(url), timeout=30) as response, open(temporary, "wb") as output:
        total = int(response.headers.get("Content-Length") or size or 0)
        received = 0
        while chunk := response.read(256 * 1024):
            output.write(chunk)
            received += len(chunk)
            if progress and total:
                progress(received / total * 0.9, f"Baixando {label}… {received * 100 // total}%")
    temporary.replace(target)
    return target


# ---------------------------------------------------------------------------
# Linux: AppImage
# ---------------------------------------------------------------------------
def download_linux(console, progress=None):
    """Baixa o AppImage para Emuladores/<Console>/ e remove versões antigas do mesmo emulador."""
    download = console.download
    if not download.repo:
        raise ValueError(f"O {download.emulator} deste console é atualizado manualmente no Linux.")
    name, url, size, tag = latest_asset(download.repo, download.asset_pattern, download.emulator)
    folder = console.emulator_dir
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    stored = big_files.stored_size(target)  # o arquivo pode estar guardado em partes
    if stored is not None and (not size or stored == size):
        if progress:
            progress(1.0, f"{download.emulator} {tag} já está atualizado.")
        return target
    _fetch(url, target, progress, f"{download.emulator} {tag}", size)
    target.chmod(0o755)
    pattern = re.compile(download.asset_pattern)
    for old in folder.iterdir():
        old_name = big_files.original_name(old)  # versões antigas, inteiras ou em partes
        if old_name != name and old.is_file() and pattern.search(old_name):
            Path(old).unlink(missing_ok=True)
            big_files.drop_cached(old_name)
    # Maior que o GitHub aceita (ex.: RMG, ~170 MB): fica guardado em partes.
    big_files.split(target)
    if progress:
        progress(1.0, f"{download.emulator} {tag} instalado.")
    return target


# ---------------------------------------------------------------------------
# Windows: pacotes .zip/.7z extraídos em Emuladores/<Console>/Windows
# ---------------------------------------------------------------------------
def _seven_zip():
    """Programa 7-Zip para extrair .7z: o do sistema (Linux) ou o 7zr.exe do hub (Windows)."""
    if not IS_WINDOWS:
        for name in ("7z", "7zz", "7za", "7zr"):
            found = shutil.which(name)
            if found:
                return found
        raise ValueError("Para extrair .7z no Linux instale o 7-Zip: sudo apt install 7zip")
    tool = WINDOWS_DIR / "7zr.exe"
    if not tool.exists():
        tool.parent.mkdir(parents=True, exist_ok=True)
        _fetch(SEVEN_ZIP_URL, tool, None, "7-Zip")
    return str(tool)


def _extract(archive, destination):
    """Extrai .zip ou .7z numa pasta."""
    destination.mkdir(parents=True, exist_ok=True)
    if archive.name.casefold().endswith(".zip"):
        with zipfile.ZipFile(archive) as package:
            package.extractall(destination)
        return
    flags = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if IS_WINDOWS else {}
    result = subprocess.run(
        [_seven_zip(), "x", "-y", f"-o{destination}", str(archive)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        **flags,
    )
    if result.returncode != 0:
        raise ValueError(f"Não consegui extrair {archive.name}: {result.stderr.decode(errors='replace')[-200:]}")


def _install_over(source, target, skip=()):
    """Copia a versão nova por cima da antiga, sem apagar configurações e saves que já estão lá.

    Se o pacote tem uma única pasta dentro (ex.: Dolphin-x64/), entra nela.
    Pastas em skip (não usadas pelo hub) não são copiadas.
    """
    entries = [entry for entry in source.iterdir()]
    if len(entries) == 1 and entries[0].is_dir():
        source = entries[0]
    target.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns(*skip) if skip else None
    for entry in source.iterdir():
        if entry.name in skip:
            continue
        if entry.is_dir():
            shutil.copytree(entry, target / entry.name, dirs_exist_ok=True, ignore=ignore)
        else:
            shutil.copy2(entry, target / entry.name)


def _windows_package(console):
    """(nome, url, tamanho, versão) do pacote Windows mais recente do emulador do console."""
    download = console.download
    source = download.windows
    if source.source == "github":
        return latest_asset(source.repo, source.asset_pattern, download.emulator)
    if source.source == "dolphin":
        release = _json("https://dolphin-emu.org/update/latest/beta")
        for artifact in release.get("artifacts", []):
            if artifact.get("system") == "Windows x64":
                url = artifact["url"]
                return url.rsplit("/", 1)[-1], url, 0, release.get("shortrev", "")
        raise ValueError("Não encontrei o Dolphin para Windows no site oficial.")
    if source.source == "retroarch":
        with urllib.request.urlopen(_request(RETROARCH_STABLE), timeout=20) as response:
            listing = response.read().decode(errors="replace")
        versions = sorted(
            set(re.findall(r"/stable/(\d+\.\d+\.\d+)/", listing)), key=lambda v: tuple(int(x) for x in v.split("."))
        )
        if not versions:
            raise ValueError("Não encontrei a versão mais recente do RetroArch.")
        version = versions[-1]
        url = f"{RETROARCH_STABLE}{version}/windows/x86_64/RetroArch.7z"
        return "RetroArch.7z", url, 0, version
    raise ValueError(f"Fonte desconhecida: {source.source}")


def download_windows(console, progress=None):
    """Baixa e instala a versão Windows do emulador em Emuladores/<Console>/Windows."""
    download = console.download
    if download.windows is None:
        raise ValueError(f"Não há versão para Windows cadastrada para {console.name}.")
    name, url, size, version = _windows_package(console)
    target = console.emulator_dir / WINDOWS_EMULATOR_FOLDER
    stamp = target / ".versao-hub"
    label = f"{download.emulator} {version} (Windows)"
    if stamp.exists() and stamp.read_text(encoding="utf-8").strip() == name:
        if progress:
            progress(1.0, f"{label} já está atualizado.")
        return target
    with tempfile.TemporaryDirectory(prefix="mygames-") as work:
        work = Path(work)
        archive = _fetch(url, work / name, progress, label, size)
        if progress:
            progress(0.92, f"Extraindo {label}…")
        _extract(archive, work / "pacote")
        _install_over(work / "pacote", target, download.windows.skip)
        for core in download.windows.cores:
            if progress:
                progress(0.96, f"Baixando núcleo {core}…")
            core_zip = _fetch(f"{RETROARCH_CORES}{core}_libretro.dll.zip", work / f"{core}.zip", None, core)
            _extract(core_zip, target / "cores")
    stamp.write_text(name, encoding="utf-8")
    if progress:
        progress(1.0, f"{label} instalado.")
    return target


def download_emulator(console, progress=None, windows=None):
    """Baixa o emulador recomendado para o sistema atual (ou para o Windows, se windows=True).

    progress(fração, texto) é chamado de outra thread.
    """
    if windows if windows is not None else IS_WINDOWS:
        return download_windows(console, progress)
    return download_linux(console, progress)
