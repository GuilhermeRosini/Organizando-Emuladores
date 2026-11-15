"""Baixa a versão mais recente do emulador recomendado direto do GitHub."""
import json
import re
import urllib.request
from pathlib import Path

USER_AGENT = "MyGames-Hub"


def _request(url):
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})


def latest_asset(download):
    url = f"https://api.github.com/repos/{download.repo}/releases/latest"
    with urllib.request.urlopen(_request(url), timeout=20) as response:
        release = json.load(response)
    pattern = re.compile(download.asset_pattern)
    for asset in release.get("assets", []):
        if pattern.search(asset["name"]):
            return asset["name"], asset["browser_download_url"], asset.get("size", 0), release.get("tag_name", "")
    raise ValueError(f"Não encontrei um AppImage de {download.emulator} na versão mais recente.")


def download_emulator(console, progress=None):
    """Baixa para Emuladores/<Console>/ e remove versões antigas do mesmo emulador.

    progress(fração, texto) é chamado de outra thread.
    """
    download = console.download
    if not download.repo:
        raise ValueError(f"O {download.emulator} deste console é atualizado manualmente.")
    name, url, size, tag = latest_asset(download)
    folder = console.emulator_dir
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    if target.exists() and (not size or target.stat().st_size == size):
        if progress:
            progress(1.0, f"{download.emulator} {tag} já está atualizado.")
        return target

    temporary = target.with_name(target.name + ".part")
    with urllib.request.urlopen(_request(url), timeout=30) as response, open(temporary, "wb") as output:
        total = int(response.headers.get("Content-Length") or size or 0)
        received = 0
        while chunk := response.read(256 * 1024):
            output.write(chunk)
            received += len(chunk)
            if progress and total:
                progress(received / total, f"Baixando {download.emulator} {tag}… {received * 100 // total}%")
    temporary.chmod(0o755)
    temporary.replace(target)

    pattern = re.compile(download.asset_pattern)
    for old in folder.iterdir():
        if old != target and old.is_file() and pattern.search(old.name):
            Path(old).unlink(missing_ok=True)
    if progress:
        progress(1.0, f"{download.emulator} {tag} instalado.")
    return target
