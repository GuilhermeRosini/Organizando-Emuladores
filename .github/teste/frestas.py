"""Teste: frestas entre faixas no renderizador da GPU (Vulkan) para várias formas de inclinar a face."""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Sistema" / "app"))
import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Gdk, Graphene, Gsk, Gtk  # noqa: E402

from mygames.ui import boxart  # noqa: E402

surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 400, 560)
cr = cairo.Context(surf)
cr.set_source_rgb(0.8, 0.1, 0.1)
cr.paint()
strips = boxart.Strips(surf, 48)
W, H = 400, 600
F = Gsk.ScalingFilter.LINEAR
R = Graphene.Rect


def face(s, method, slope, x0=20.3, width=300.0, height=500.0, grow=0.0):
    origin = math.floor(x0)
    start = x0 - origin
    s.save()
    s.translate(Graphene.Point().init(origin, 300))
    sx = 1.0
    if method in ("skew", "skew-escala"):
        s.transform(Gsk.Transform().skew(0, math.degrees(math.atan(slope))))
        if method == "skew-escala":
            sx = math.sqrt(1 + slope * slope)
            s.transform(Gsk.Transform().scale(1 / sx, 1))
    n = len(strips.pieces)
    u1 = start * sx
    for i, (_a, b, tex) in enumerate(strips.pieces):
        u0 = u1
        u1 = (start + width) * sx if i == n - 1 else float(round((start + width * b) * sx))
        h = height + grow * ((u0 + u1) / 2 / sx - start)
        y = -h / 2
        if method == "reto":  # sem inclinação: cada faixa sobe/desce sozinha
            y += slope * ((u0 + u1) / 2 - start)
        s.append_scaled_texture(tex, F, R().init(u0, y, u1 - u0, h))
    s.restore()


r = Gsk.VulkanRenderer()
r.realize_for_display(Gdk.Display.get_default())
for method in ("skew", "skew-escala", "reto"):
    for slope in (0.003, 0.05, 0.15):
        s = Gtk.Snapshot()
        s.append_color(Gdk.RGBA(red=0, green=0, blue=0, alpha=1), R().init(0, 0, W, H))
        face(s, method, slope)
        out = r.render_texture(s.to_node(), R().init(0, 0, W, H))
        d = Gdk.TextureDownloader.new(out)
        d.set_format(Gdk.MemoryFormat.R8G8B8A8)
        px = d.download_bytes()[0].get_data()
        bad = 0
        for y in range(150, 450, 3):  # meio da face, longe das bordas de cima/baixo
            for x in range(26, 315):
                yy = int(y + slope * (x - 20))
                i = (yy * W + x) * 4
                if abs(px[i] - 204) > 10 or px[i + 1] > 40:
                    bad += 1
        print(f"FRESTA {method:12} inclinação {slope:5}: pixels errados {bad}")
