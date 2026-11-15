"""Mantém o hub funcionando depois de mover, copiar ou descompactar a pasta.

Os emuladores gravam caminhos absolutos nos próprios arquivos de configuração
(ex.: o RMG guarda onde ficam os saves do N64). Se a pasta do hub muda de
lugar, esses caminhos passam a apontar para o local antigo: saves somem ou
vão parar no lugar errado.

Antes de abrir um emulador, relocate() procura, nos arquivos de configuração
da pasta "dados" do console, caminhos do tipo
    /qualquer/lugar/Emuladores/<Console>/...
e troca o começo pelo local atual do hub. Só arquivos de texto pequenos e
com extensão de configuração são tocados; saves e memory cards nunca.
"""
import os
import re
import tempfile
from pathlib import Path

from .paths import EMULATORS_DIR

CONFIG_EXTENSIONS = {".ini", ".cfg", ".conf", ".json", ".lpl", ".xml", ".toml", ".yml", ".yaml"}
# Pastas que só têm saves, caches ou imagens: não precisam ser lidas.
SKIPPED_DIRS = {
    "cache", "shadercache", "shaders", "logs", "memcards", "sstates", "snaps", "save", "saves",
    "states", "screenshots", "textures", "hires_texture", "texture_dump", "covers", "dump", "load",
    "wii", "gc", "nvram", "sta", "snap", "diff", "cheats", "patches",
}
MAX_SIZE = 2 * 1024 * 1024


def _pattern(folders):
    """Expressão que acha caminhos antigos .../Emuladores/<Console>, do Linux ou do Windows.

    Linux:   /home/fulano/Jogos/Emuladores/PlayStation 2
    Windows: C:\\Users\\fulano\\Jogos\\Emuladores\\PlayStation 2  (ou com "/" ou "\\\\" em JSON)
    O caminho termina em aspas, "=", ",", ";" ou fim de linha.
    """
    names = "|".join(re.escape(name) for name in sorted(folders, key=len, reverse=True))
    sep = r"(?:\\\\|\\|/)"  # "\\" (JSON), "\" ou "/"
    body = r"[^\n\"';=,<>|]*?"
    windows = r"(?<!\w)[A-Za-z]:" + sep + body + sep + r"emuladores" + sep + r"(?P<wname>" + names + r")"
    # Linux: começa numa "/" que não vem colada numa palavra ("/home", "=/home", "file:///home").
    linux = r"(?<!\w)/(?!/)" + body + r"/emuladores/(?P<lname>" + names + r")"
    # O resto do caminho (ex.: \\Adicionar Jogos\\kof98.zip) também é convertido para "/".
    rest = r"(?P<rest>(?:[/\\][^\n\"';=,<>|]*)?)"
    return re.compile(r"(?:" + windows + r"|" + linux + r")(?=[/\\\"'\n,;]|$)" + rest, re.IGNORECASE)


def _write_atomic(path, text):
    """Grava o arquivo num temporário e troca de uma vez (nunca fica pela metade)."""
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".portable-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape", newline="") as handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def config_files(folder):
    """Arquivos de configuração pequenos dentro da pasta de dados (pula saves e caches)."""
    for directory, subdirectories, files in os.walk(folder):
        subdirectories[:] = [d for d in subdirectories if d.casefold() not in SKIPPED_DIRS]
        for name in files:
            path = Path(directory) / name
            if path.suffix.casefold() in CONFIG_EXTENSIONS:
                try:
                    if path.stat().st_size <= MAX_SIZE:
                        yield path
                except OSError:
                    continue


def relocate(folder, console_folders, emulators_dir=EMULATORS_DIR):
    """Troca caminhos de um local antigo do hub pelo atual. Devolve os arquivos alterados."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    pattern = _pattern(console_folders)
    canonical = {name.casefold(): name for name in console_folders}

    def replace(match):
        """Troca o começo do caminho antigo pela pasta atual do console.

        No Windows o novo caminho usa "/" (o Windows aceita e não quebra arquivos JSON).
        """
        name = canonical[(match.group("wname") or match.group("lname")).casefold()]
        rest = re.sub(r"\\\\|\\", "/", match.group("rest"))
        return (Path(emulators_dir) / name).as_posix() + rest

    changed = []
    for path in config_files(folder):
        try:
            text = path.read_text(encoding="utf-8", errors="surrogateescape")
        except OSError:
            continue
        if "muladores" not in text and "MULADORES" not in text:
            continue
        updated = pattern.sub(replace, text)
        if updated != text:
            try:
                _write_atomic(path, updated)
            except OSError:
                continue
            changed.append(path)
    return changed


def set_keys(path, section, values, separator="="):
    """Atualiza chaves de uma seção de um INI, mantendo todo o resto como está."""
    path = Path(path)
    if not path.exists():
        return False
    lines = path.read_text(encoding="utf-8", errors="surrogateescape").splitlines()
    pending = dict(values)
    current = None
    insert_at = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if current == section and insert_at is None:
                insert_at = index
            current = stripped[1:-1]
            continue
        if current == section and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in pending:
                lines[index] = f"{key}{separator}{pending.pop(key)}"
    if current == section and insert_at is None:
        insert_at = len(lines)
    if pending:
        if insert_at is None:
            lines += ["", f"[{section}]"]
            insert_at = len(lines)
        lines[insert_at:insert_at] = [f"{key}{separator}{value}" for key, value in pending.items()]
    _write_atomic(path, "\n".join(lines) + "\n")
    return True
