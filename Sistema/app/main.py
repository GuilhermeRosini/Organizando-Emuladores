#!/usr/bin/env python3
"""Ponto de entrada do My Games."""
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # não espalha __pycache__ pelo pendrive
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _log_when_windowless():
    """No Windows o hub roda com pythonw (sem terminal): as mensagens vão para Sistema/logs/hub.log."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    logs = Path(__file__).resolve().parent.parent / "logs"
    try:
        logs.mkdir(parents=True, exist_ok=True)
        log = open(logs / "hub.log", "w", encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        log = open(os.devnull, "w")
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log


def _prepare_windows_runtime():
    """Windows: prepara o runtime (Python + GTK) que vem dentro de Sistema/windows/runtime.

    A lista de leitores de imagem do GTK (loaders.cache) precisa ter o caminho
    real deste computador, então ela é gerada aqui na primeira vez e de novo se
    a pasta do hub mudar de lugar. Fica no cache local (%LOCALAPPDATA%\\my-games).
    """
    import ctypes
    import subprocess

    # Identidade própria na barra de tarefas: o hub não fica agrupado como "Python".
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MyGames.Hub")
    except (AttributeError, OSError):
        pass
    runtime = Path(sys.executable).resolve().parent.parent
    loaders = runtime / "lib" / "gdk-pixbuf-2.0" / "2.10.0" / "loaders"
    query = runtime / "bin" / "gdk-pixbuf-query-loaders.exe"
    if not loaders.is_dir() or not query.exists():
        return
    cache_dir = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "my-games"
    cache = cache_dir / "loaders.cache"
    stamp = cache_dir / "loaders.origem"
    try:
        if not cache.exists() or not stamp.exists() or stamp.read_text(encoding="utf-8") != str(runtime):
            cache_dir.mkdir(parents=True, exist_ok=True)
            env = dict(os.environ, GDK_PIXBUF_MODULEDIR=str(loaders))
            result = subprocess.run(
                [str(query)], env=env, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
            if result.returncode == 0 and result.stdout:
                cache.write_bytes(result.stdout)
                stamp.write_text(str(runtime), encoding="utf-8")
        if cache.exists():
            os.environ["GDK_PIXBUF_MODULE_FILE"] = str(cache)
    except OSError as error:
        print(f"Aviso: não consegui preparar os leitores de imagem: {error}", file=sys.stderr)


def main():
    """Ponto de partida: baixa emuladores pelo terminal (--baixar) ou abre a interface do hub."""
    if len(sys.argv) > 2 and sys.argv[1] in ("--baixar", "--baixar-windows"):
        # Uso: python3 app/main.py --baixar ps2  (ou "todos")
        #      python3 app/main.py --baixar-windows todos   (prepara o hub para Windows, de qualquer sistema)
        from mygames.consoles import CONSOLE_LIST
        from mygames.downloader import download_emulator

        wanted, windows = sys.argv[2], sys.argv[1] == "--baixar-windows"
        for console in CONSOLE_LIST:
            if wanted in ("todos", console.id):
                print(f"{console.name}: ", end="", flush=True)
                try:
                    path = download_emulator(
                        console, lambda _f, text: print(f"\r{console.name}: {text}   ", end="", flush=True), windows
                    )
                    print(f"\n  -> {path}")
                except (OSError, ValueError) as error:
                    print(f"\n  erro: {error}")
        return 0

    # O renderizador OpenGL do GTK ("gl") foi ~3x mais rápido que o Vulkan (o
    # padrão) nos testes com placa NVIDIA. Quem quiser outro pode definir
    # GSK_RENDERER antes de abrir o hub.
    os.environ.setdefault("GSK_RENDERER", "gl")
    _log_when_windowless()
    if sys.platform == "win32":
        _prepare_windows_runtime()
    try:
        import gi

        gi.require_version("Gdk", "4.0")
        gi.require_version("Gtk", "4.0")
        if sys.platform != "win32":
            gi.require_version("GioUnix", "2.0")  # só existe no Linux (atalhos .desktop)
    except (ImportError, ValueError) as error:
        print(
            "O My Games precisa do GTK 4 para Python.\n"
            "No Ubuntu/Debian: sudo apt install python3-gi gir1.2-gtk-4.0\n"
            f"Detalhe: {error}",
            file=sys.stderr,
        )
        return 1

    from mygames.ui.app import run

    return run()


if __name__ == "__main__":
    raise SystemExit(main())
