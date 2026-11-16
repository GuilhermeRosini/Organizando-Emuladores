"""Teste: a caixa em perspectiva com vários métodos e renderizadores do GTK."""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Sistema" / "app"))
OUT = ROOT / "saida"
OUT.mkdir(exist_ok=True)

import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Gdk, Graphene, Gsk, Gtk  # noqa: E402

from mygames.ui import boxart  # noqa: E402

art = ROOT / "Sistema" / "app" / "assets" / "consoles" / "ps2" / "console.png"
surface = boxart.cover_surface(art, 640)
tex = boxart.texture(surface)
P, UNIT = 1500, 1000.0
W, H = 1280, 720
vx, vy = W / 2, H * 0.45
unit = Graphene.Rect().init(0, 0, UNIT, UNIT)
STRIPS = 24


def slices(surf, n):
    """Corta a imagem em n faixas verticais (texturas separadas)."""
    w, h = surf.get_width(), surf.get_height()
    out = []
    for i in range(n):
        x0, x1 = round(w * i / n), round(w * (i + 1) / n)
        part = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, x1 - x0), h)
        cr = cairo.Context(part)
        cr.set_source_surface(surf, -x0, 0)
        cr.paint()
        out.append(boxart.texture(part))
    return out


strip_tex = slices(surface, STRIPS)


def matrix_method(s, a, b, y_top, y_bottom):
    (ax, az), (bx, bz) = a, b
    dx, dz, dy = (bx - ax) / UNIT, (bz - az) / UNIT, (y_bottom - y_top) / UNIT
    w0 = P - az
    m = Graphene.Matrix().init_from_float([
        P * dx - vx * dz, -vy * dz, 0, -dz, 0, P * dy, 0, 0, 0, 0, 1, 0,
        vx * w0 + P * ax, vy * w0 + P * y_top, 0, w0])
    s.save()
    s.transform_matrix(m)
    s.append_scaled_texture(tex, Gsk.ScalingFilter.LINEAR, unit)
    s.restore()


def strips_method(s, a, b, y_top, y_bottom):
    """Cada faixa é um paralelogramo (transformação 2D): funciona em qualquer renderizador."""
    (ax, az), (bx, bz) = a, b

    def project(t):
        x, z = ax + (bx - ax) * t, az + (bz - az) * t
        w = P - z
        return vx + P * x / w, vy + P * y_top / w, vy + P * y_bottom / w

    pts = [project(i / STRIPS) for i in range(STRIPS + 1)]
    for i, piece in enumerate(strip_tex):
        (x0, t0, b0), (x1, t1, b1) = pts[i], pts[i + 1]
        width = x1 - x0
        if abs(width) < 0.01:
            continue
        height = ((b0 - t0) + (b1 - t1)) / 2
        slope = (t1 - t0) / width
        s.save()
        s.translate(Graphene.Point().init(x0, t0))
        s.transform(Gsk.Transform().skew(0, math.degrees(math.atan(slope))))
        # +0.6 px de sobra para não aparecer fresta entre faixas
        s.append_scaled_texture(piece, Gsk.ScalingFilter.LINEAR, Graphene.Rect().init(0, 0, width + 0.6, height))
        s.restore()


def gtk3d_method(s, a, b, y_top, y_bottom):
    """Perspectiva do próprio GTK (perspective + rotate_3d)."""
    (ax, az), (bx, bz) = a, b
    cx, cz = (ax + bx) / 2, (az + bz) / 2
    length = math.hypot(bx - ax, bz - az)
    angle = math.degrees(math.atan2(-(bz - az), bx - ax))
    s.save()
    s.translate(Graphene.Point().init(vx, vy))
    s.perspective(P)
    s.translate_3d(Graphene.Point3D().init(cx, 0, cz))
    s.rotate_3d(angle, Graphene.Vec3.y_axis())
    s.translate(Graphene.Point().init(-length / 2, y_top))
    s.append_scaled_texture(tex, Gsk.ScalingFilter.LINEAR, Graphene.Rect().init(0, 0, length, y_bottom - y_top))
    s.restore()


methods = {"matriz": matrix_method, "faixas": strips_method, "gtk3d": gtk3d_method}
cases = {"frente": ((-200, 0), (200, 0), -300, 200), "girada": ((-200, 120), (200, -120), -300, 200)}


def scene(method, a, b, t, bt):
    s = Gtk.Snapshot()
    s.append_color(Gdk.RGBA(red=0.05, green=0.05, blue=0.08, alpha=1), Graphene.Rect().init(0, 0, W, H))
    method(s, a, b, t, bt)
    return s.to_node()


def pink(out):
    d = Gdk.TextureDownloader.new(out)
    d.set_format(Gdk.MemoryFormat.R8G8B8A8)
    raw = d.download_bytes()[0].get_data()
    return sum(1 for i in range(0, len(raw), 4 * 97) if raw[i] > 240 and 90 < raw[i + 1] < 130 and raw[i + 2] > 160)


for rname in ("cairo", "vulkan"):
    r = Gsk.CairoRenderer() if rname == "cairo" else Gsk.VulkanRenderer()
    try:
        r.realize_for_display(Gdk.Display.get_default())
    except Exception as e:  # noqa: BLE001
        print("NAO REALIZOU", rname, e)
        continue
    for mname, method in methods.items():
        for cname, (a, b, t, bt) in cases.items():
            out = r.render_texture(scene(method, a, b, t, bt), Graphene.Rect().init(0, 0, W, H))
            out.save_to_png(str(OUT / f"r-{rname}-{mname}-{cname}.png"))
            print("RES", rname, mname, cname, "rosa:", pink(out))
    r.unrealize()
