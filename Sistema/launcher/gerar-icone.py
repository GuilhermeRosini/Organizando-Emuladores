#!/usr/bin/env python3
"""Gera my-games.ico (ícone do .exe do Windows) a partir de Sistema/app/assets/my-games.png.

O .ico guarda o mesmo desenho em vários tamanhos (16 a 256 px), cada um em PNG,
para o Windows escolher o melhor no Explorer, na barra de tarefas e na área de trabalho.
"""
import io
import struct
from pathlib import Path

import cairo

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "app" / "assets" / "my-games.png"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def scaled_png(source, size):
    """O ícone redimensionado para size x size, em PNG."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.scale(size / source.get_width(), size / source.get_height())
    pattern = cairo.SurfacePattern(source)
    pattern.set_filter(cairo.FILTER_BEST)
    cr.set_source(pattern)
    cr.paint()
    buffer = io.BytesIO()
    surface.write_to_png(buffer)
    return buffer.getvalue()


def main():
    """Escreve my-games.ico ao lado deste script."""
    source = cairo.ImageSurface.create_from_png(str(SOURCE))
    images = [scaled_png(source, size) for size in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, data = b"", b""
    for size, png in zip(SIZES, images):
        side = 0 if size == 256 else size  # no formato .ico, 0 significa 256
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    (HERE / "my-games.ico").write_bytes(header + entries + data)
    print("Pronto: my-games.ico")


if __name__ == "__main__":
    main()
