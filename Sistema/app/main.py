#!/usr/bin/env python3
"""Ponto de entrada do My Games."""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # não espalha __pycache__ pelo pendrive
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--baixar":
        # Uso: python3 app/main.py --baixar ps2  (ou "todos")
        from mygames.consoles import CONSOLE_LIST
        from mygames.downloader import download_emulator

        wanted = sys.argv[2]
        for console in CONSOLE_LIST:
            if wanted in ("todos", console.id):
                print(f"{console.name}: ", end="", flush=True)
                path = download_emulator(console, lambda _f, text: print(f"\r{console.name}: {text}", end="", flush=True))
                print(f"\n  -> {path}")
        return 0

    try:
        import gi

        gi.require_version("Gdk", "4.0")
        gi.require_version("Gtk", "4.0")
        gi.require_version("GioUnix", "2.0")
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
