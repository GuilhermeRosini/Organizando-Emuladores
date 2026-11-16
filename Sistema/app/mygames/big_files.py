"""Arquivos grandes demais para o GitHub (ele recusa arquivos com mais de 100 MB).

O AppImage do emulador de Nintendo 64 (RMG) tem uns 170 MB. Para ele ir junto
no repositório (e no ZIP baixado do GitHub), fica guardado em partes:
    Emuladores/Nintendo 64/RMG-...AppImage.parte1
    Emuladores/Nintendo 64/RMG-...AppImage.parte2
Quando o hub procura o emulador, as partes são juntadas uma única vez numa
cópia no cache deste computador (~/.cache/my-games/inteiros), e é ela que roda.
Ao baixar uma versão nova pelo Menu, o arquivo é dividido do mesmo jeito.
"""
import re
import shutil
import threading
from pathlib import Path

from .paths import CACHE_DIR, IS_WINDOWS

PART_SIZE = 90 * 1024 * 1024  # abaixo dos 100 MB que o GitHub aceita por arquivo
WHOLE_DIR = CACHE_DIR / "inteiros"
_PART = re.compile(r"^(?P<name>.+)\.parte(?P<number>[1-9]\d*)$")
_JOIN_LOCK = threading.Lock()
_ORIGINS = {}  # cópia inteira no cache -> onde o arquivo fica no hub (para mostrar)


def is_part(path):
    """O arquivo é um pedaço de um arquivo grande (ex.: RMG.AppImage.parte1)?"""
    return _PART.match(Path(path).name) is not None


def original_name(path):
    """Nome do arquivo inteiro (tira o ".parteN"; outros nomes ficam iguais)."""
    match = _PART.match(Path(path).name)
    return match.group("name") if match else Path(path).name


def _runs_here(name):
    """Só vale juntar o que roda neste sistema (.exe no Windows, o resto no Linux)."""
    return name.casefold().endswith(".exe") == IS_WINDOWS


def _groups(folder, name=None):
    """Arquivos divididos e completos da pasta: {nome do inteiro: [parte1, parte2, ...]}."""
    found = {}
    for entry in Path(folder).iterdir():
        match = _PART.match(entry.name)
        if match and (name is None or match.group("name") == name) and entry.is_file():
            found.setdefault(match.group("name"), {})[int(match.group("number"))] = entry
    return {
        whole: [parts[number] for number in sorted(parts)]
        for whole, parts in found.items()
        if sorted(parts) == list(range(1, len(parts) + 1))  # nenhuma parte faltando
    }


def stored_size(path):
    """Tamanho do arquivo guardado no hub, inteiro ou em partes (None se não existe)."""
    path = Path(path)
    if path.is_file():
        return path.stat().st_size
    if not path.parent.is_dir():
        return None
    parts = _groups(path.parent, path.name).get(path.name)
    return sum(part.stat().st_size for part in parts) if parts else None


def _join(name, parts):
    """Junta as partes no cache (só na primeira vez, ou se as partes mudaram)."""
    size = sum(part.stat().st_size for part in parts)
    target = WHOLE_DIR / name
    with _JOIN_LOCK:
        if target.is_file() and target.stat().st_size == size:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        try:
            with open(temporary, "wb") as output:
                for part in parts:
                    with open(part, "rb") as source:
                        shutil.copyfileobj(source, output, 1024 * 1024)
            temporary.chmod(0o755)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def whole_files(folder):
    """Arquivos divididos da pasta que rodam neste sistema, já inteiros (cópias no cache)."""
    found = []
    for name, parts in _groups(folder).items():
        if _runs_here(name):
            whole = _join(name, parts)
            _ORIGINS[whole] = Path(folder) / name
            found.append(whole)
    return found


def hub_path(path):
    """Onde o arquivo fica no hub, mesmo quando o que roda é a cópia do cache."""
    return _ORIGINS.get(Path(path), Path(path))


def split(path):
    """Divide um arquivo maior que PART_SIZE em partes ao lado dele (para caber no GitHub).

    O arquivo inteiro sai da pasta do hub: se ele roda neste sistema, vira a
    cópia do cache (não precisa juntar de novo); senão é apagado.
    """
    path = Path(path)
    size = path.stat().st_size
    if size <= PART_SIZE:
        return [path]
    for entry in path.parent.iterdir():
        if is_part(entry) and original_name(entry) == path.name:
            entry.unlink()  # partes antigas deste mesmo arquivo
    parts = []
    with open(path, "rb") as source:
        remaining = size
        while remaining > 0:
            part = path.with_name(f"{path.name}.parte{len(parts) + 1}")
            left = min(PART_SIZE, remaining)
            with open(part, "wb") as output:
                while left > 0:
                    chunk = source.read(min(1024 * 1024, left))
                    if not chunk:
                        raise OSError(f"{path.name} mudou de tamanho enquanto era dividido.")
                    output.write(chunk)
                    left -= len(chunk)
                    remaining -= len(chunk)
            parts.append(part)
    if _runs_here(path.name):
        WHOLE_DIR.mkdir(parents=True, exist_ok=True)
        whole = Path(shutil.move(str(path), str(WHOLE_DIR / path.name)))
        whole.chmod(0o755)
        _ORIGINS[whole] = path
    else:
        path.unlink()
    return parts


def drop_cached(name):
    """Apaga do cache a cópia inteira de uma versão antiga (depois de uma atualização)."""
    (WHOLE_DIR / name).unlink(missing_ok=True)
