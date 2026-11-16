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

def solid(w, h):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    cr.set_source_rgb(0.8, 0.1, 0.1)
    cr.paint()
    return surf


def padded(surf, count, pad=2):
    """Faixas com `pad` colunas extras de cada lado (vizinhas)."""
    w, h = surf.get_width(), surf.get_height()
    out = []
    for i in range(count):
        x0, x1 = round(w * i / count), round(w * (i + 1) / count)
        p0, p1 = max(0, x0 - pad), min(w, x1 + pad)
        part = cairo.ImageSurface(cairo.FORMAT_ARGB32, p1 - p0, h)
        c = cairo.Context(part)
        c.set_operator(cairo.OPERATOR_SOURCE)
        c.set_source_surface(surf, -p0, 0)
        c.paint()
        out.append((x0 / w, x1 / w, (x0 - p0) / (x1 - x0), (p1 - x1) / (x1 - x0), boxart.texture(part)))
    return out


CASES = {"frente": (solid(400, 560), 48), "lombada": (solid(46, 560), 4)}
W, H = 400, 600
F = Gsk.ScalingFilter.LINEAR
R = Graphene.Rect


def face(s, method, slope, strips, x0=20.3, width=300.0, height=500.0, grow=0.0, pstrips=None):
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
    if method in ("pad-sobra", "pad-recorte"):
        s.transform(Gsk.Transform().skew(0, math.degrees(math.atan(slope))))
        n = len(pstrips)
        u1 = start
        for i, (_a, b, left, right, tex) in enumerate(pstrips):
            u0 = u1
            u1 = start + width if i == n - 1 else float(round(start + width * b))
            w = u1 - u0
            h = height
            rect = R().init(u0 - left * w, -h / 2, w * (1 + left + right), h)
            if method == "pad-recorte":
                s.push_clip(R().init(u0, -h, w, 2 * h))
                s.append_scaled_texture(tex, F, rect)
                s.pop()
            else:
                s.append_scaled_texture(tex, F, rect)
        s.restore()
        return
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
for cname, (surf, count) in CASES.items():
  strips = boxart.Strips(surf, count)
  pstrips = padded(surf, count)
  for width in (60.3, 300.0):
   for method in ("skew", "reto", "pad-sobra", "pad-recorte"):
    for slope in (0.003, 0.05):
        s = Gtk.Snapshot()
        s.append_color(Gdk.RGBA(red=0, green=0, blue=0, alpha=1), R().init(0, 0, W, H))
        face(s, method, slope, strips, width=width, pstrips=pstrips)
        out = r.render_texture(s.to_node(), R().init(0, 0, W, H))
        d = Gdk.TextureDownloader.new(out)
        d.set_format(Gdk.MemoryFormat.R8G8B8A8)
        px = d.download_bytes()[0].get_data()
        bad = 0
        for y in range(150, 450, 3):  # meio da face, longe das bordas de cima/baixo
            for x in range(26, int(20 + width) - 5):
                yy = int(y + slope * (x - 20))
                i = (yy * W + x) * 4
                if abs(px[i] - 204) > 10 or px[i + 1] > 40:
                    bad += 1
        print(f"FRESTA {cname:8} largura {width:5} {method:12} inclinação {slope:5}: pixels errados {bad}")
