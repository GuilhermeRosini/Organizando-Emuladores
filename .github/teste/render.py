"""Teste: desenha a caixa em perspectiva (como a prateleira) com os renderizadores do GTK."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Sistema" / "app"))
OUT = ROOT / "saida"
OUT.mkdir(exist_ok=True)

import gi  # noqa: E402

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Gdk, Graphene, Gsk, Gtk  # noqa: E402

from mygames.ui import boxart  # noqa: E402

art = ROOT / "Sistema" / "app" / "assets" / "consoles" / "ps2" / "console.png"
tex = boxart.texture(boxart.cover_surface(art, 640))
P, UNIT = 1500, 1000.0
W, H = 1280, 720
vx, vy = W / 2, H * 0.45
unit = Graphene.Rect().init(0, 0, UNIT, UNIT)


def node(a, b, y_top, y_bottom, filt):
    (ax, az), (bx, bz) = a, b
    dx, dz, dy = (bx - ax) / UNIT, (bz - az) / UNIT, (y_bottom - y_top) / UNIT
    w0 = P - az
    m = Graphene.Matrix().init_from_float([
        P * dx - vx * dz, -vy * dz, 0, -dz,
        0, P * dy, 0, 0,
        0, 0, 1, 0,
        vx * w0 + P * ax, vy * w0 + P * y_top, 0, w0,
    ])
    s = Gtk.Snapshot()
    s.append_color(Gdk.RGBA(red=0.05, green=0.05, blue=0.08, alpha=1), Graphene.Rect().init(0, 0, W, H))
    s.save()
    s.transform_matrix(m)
    s.append_scaled_texture(tex, filt, unit)
    s.restore()
    return s.to_node()


cases = {
    "frente": ((-200, 0), (200, 0), -300, 200),       # de frente (sem perspectiva real)
    "girada": ((-200, 60), (200, -60), -300, 200),     # inclinada (perspectiva)
}
renderers = {"cairo": Gsk.CairoRenderer, "gl": getattr(Gsk, "GLRenderer", None), "ngl": getattr(Gsk, "NglRenderer", None)}
for rname, cls in renderers.items():
    if cls is None:
        print("SEM", rname)
        continue
    r = cls()
    try:
        r.realize_for_display(Gdk.Display.get_default())
    except Exception as e:  # noqa: BLE001
        print("NAO REALIZOU", rname, e)
        continue
    for cname, (a, b, t, bt) in cases.items():
        for fname, filt in (("linear", Gsk.ScalingFilter.LINEAR), ("trilinear", Gsk.ScalingFilter.TRILINEAR)):
            out = r.render_texture(node(a, b, t, bt, filt), Graphene.Rect().init(0, 0, W, H))
            out.save_to_png(str(OUT / f"r-{rname}-{cname}-{fname}.png"))
            print("OK", rname, cname, fname)
    r.unrealize()
print("GTK", Gtk.get_major_version(), Gtk.get_minor_version(), Gtk.get_micro_version())
