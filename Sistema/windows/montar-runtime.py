#!/usr/bin/env python3
"""Monta o runtime do My Games para Windows (Python + GTK 4), em Sistema/windows/runtime.

O Windows não vem com Python nem GTK. Este script baixa os pacotes oficiais do
MSYS2 (ambiente UCRT64), com todas as dependências, extrai e remove o que não é
necessário para rodar (testes, documentação, cabeçalhos, bibliotecas estáticas).
Resultado: ~130 MB que viajam junto com o hub; nada é instalado no Windows.

Pode ser rodado no Linux (precisa de: python3, tar com zstd e glib-compile-schemas)
para preparar o pendrive antes de levá-lo a um Windows.

Uso:  python3 Sistema/windows/montar-runtime.py
"""
import concurrent.futures
import io
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNTIME = HERE / "runtime"
MIRROR = "https://mirror.msys2.org/mingw/ucrt64/"
PREFIX = "mingw-w64-ucrt-x86_64-"
USER_AGENT = "MyGames-Hub"

# O que o hub precisa no Windows.
ROOTS = ["python", "python-gobject", "python-cairo", "gtk4", "adwaita-icon-theme", "ca-certificates", "librsvg"]
# Dependências que não são usadas pelo hub (deixam o runtime bem menor).
EXCLUDE = {
    "tcl", "tk", "python-pip", "python-setuptools", "gcc", "binutils", "pkgconf", "gobject-introspection",
    "vulkan-loader", "vulkan-headers", "shaderc", "spirv-tools", "glslang", "python-mako", "python-markdown",
    "meson", "ninja", "cmake", "gtk-update-icon-cache", "python-docutils", "gtk4-media-gstreamer",
    "gstreamer", "gst-plugins-base", "gst-plugins-bad-libs", "ffmpeg", "libcloudproviders", "tinysparql",
}
# Programas que ficam no runtime (o resto da pasta bin é só de desenvolvimento).
KEEP_PROGRAMS = {"python.exe", "pythonw.exe", "gdk-pixbuf-query-loaders.exe"}
# Partes do runtime que não são usadas para rodar o hub.
REMOVE = [
    "include", "share/doc", "share/man", "share/info", "share/gtk-doc", "share/gettext", "share/aclocal",
    "share/pkgconfig", "lib/pkgconfig", "lib/cmake", "share/vala", "share/gir-1.0", "share/installed-tests",
    "share/gdb", "share/bash-completion", "share/zsh", "share/locale", "lib/gettext", "share/terminfo",
    "lib/terminfo", "share/icons/Adwaita/cursors",
]
PYTHON_REMOVE = ["test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3"]


def _request(url):
    """Pedido HTTP com um nome de cliente genérico."""
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT})


def _download(url):
    """Baixa um arquivo inteiro para a memória."""
    with urllib.request.urlopen(_request(url), timeout=60) as response:
        return response.read()


def _zstd_decompress(data):
    """Descompacta .zst usando o programa zstd do sistema."""
    return subprocess.run(["zstd", "-dc"], input=data, capture_output=True, check=True).stdout


def read_database():
    """Lê o índice de pacotes do MSYS2: nome, arquivo e dependências de cada um."""
    raw = _download(MIRROR + "ucrt64.db")
    try:
        raw = _zstd_decompress(raw)
    except (OSError, subprocess.CalledProcessError):
        pass  # alguns espelhos servem o índice já descompactado
    packages, provides = {}, {}
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive.getmembers():
            if not member.name.endswith("/desc"):
                continue
            fields = {}
            for block in archive.extractfile(member).read().decode().strip().split("\n\n"):
                lines = block.split("\n")
                fields[lines[0].strip("%")] = lines[1:]
            name = fields["NAME"][0]
            depends = [re.split(r"[<>=]", d)[0] for d in fields.get("DEPENDS", [])]
            packages[name] = {"file": fields["FILENAME"][0], "deps": depends}
            for item in fields.get("PROVIDES", []):
                provides.setdefault(re.split(r"[<>=]", item)[0], name)
    return packages, provides


def resolve(packages, provides):
    """Lista de pacotes necessários, seguindo as dependências e pulando os excluídos."""
    wanted, stack = set(), [PREFIX + root for root in ROOTS]
    while stack:
        name = stack.pop()
        name = name if name in packages else provides.get(name, name)
        if name in wanted or name not in packages or name[len(PREFIX):] in EXCLUDE:
            continue
        wanted.add(name)
        stack += packages[name]["deps"]
    return sorted(wanted)


def extract(packages, names, target):
    """Baixa e extrai os pacotes (em paralelo) dentro de target/ucrt64."""
    def fetch(name):
        """Baixa um pacote."""
        return name, _download(MIRROR + packages[name]["file"])

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        for count, (name, data) in enumerate(pool.map(fetch, names), 1):
            print(f"\r  {count}/{len(names)} {name[len(PREFIX):]:40s}", end="", flush=True)
            with tarfile.open(fileobj=io.BytesIO(_zstd_decompress(data))) as archive:
                members = [m for m in archive.getmembers() if m.name.startswith("ucrt64/")]
                archive.extractall(target, members=members, filter="tar")
    print()


def prune(root):
    """Remove do runtime o que não é usado para rodar o hub."""
    for relative in REMOVE:
        shutil.rmtree(root / relative, ignore_errors=True)
    for path in root.joinpath("lib").rglob("*.a"):
        path.unlink()
    for python_lib in root.joinpath("lib").glob("python3.*"):
        for name in PYTHON_REMOVE:
            shutil.rmtree(python_lib / name, ignore_errors=True)
        for config in python_lib.glob("config-*"):
            shutil.rmtree(config, ignore_errors=True)
    for cache in list(root.rglob("__pycache__")):
        shutil.rmtree(cache, ignore_errors=True)
    for program in root.joinpath("bin").iterdir():
        if program.suffix.casefold() != ".dll" and program.name.casefold() not in KEEP_PROGRAMS:
            program.unlink()


def compile_schemas(root):
    """Compila as configurações do GTK (o formato é o mesmo no Linux e no Windows)."""
    schemas = root / "share" / "glib-2.0" / "schemas"
    tool = shutil.which("glib-compile-schemas")
    if tool is None:
        print("Aviso: glib-compile-schemas não encontrado; o hub compila no primeiro uso, se possível.")
        return
    subprocess.run([tool, str(schemas)], check=True)


def main():
    """Monta o runtime do zero em Sistema/windows/runtime."""
    print("Lendo o índice de pacotes do MSYS2…")
    packages, provides = read_database()
    names = resolve(packages, provides)
    print(f"{len(names)} pacotes necessários. Baixando e extraindo…")
    with tempfile.TemporaryDirectory(dir=HERE, prefix=".runtime-") as work:
        work = Path(work)
        extract(packages, names, work)
        root = work / "ucrt64"
        prune(root)
        compile_schemas(root)
        shutil.rmtree(RUNTIME, ignore_errors=True)
        root.rename(RUNTIME)
    size = sum(f.stat().st_size for f in RUNTIME.rglob("*") if f.is_file()) / 1e6
    print(f"Pronto: {RUNTIME} ({size:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
