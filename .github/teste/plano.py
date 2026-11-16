"""Teste: textura simples, sem perspectiva, no renderizador cairo."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Sistema" / "app"))
import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

from mygames.ui import boxart  # noqa: E402

art = ROOT / "Sistema" / "app" / "assets" / "consoles" / "ps2" / "console.png"
s = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 200)
cr = cairo.Context(s)
cr.set_source_rgb(0, 0.5, 1)
cr.paint()
raw = bytes(s.get_data())
texs = {
    "arquivo": Gdk.Texture.new_from_filename(str(art)),
    "memoria-hub": boxart.texture(boxart.cover_surface(art, 640)),
    "memoria-azul": boxart.texture(s),
    "memoria-azul-RGBA": Gdk.MemoryTexture.new(200, 200, Gdk.MemoryFormat.B8G8R8A8, GLib.Bytes.new(raw), s.get_stride()),
}
r = Gsk.CairoRenderer()
r.realize_for_display(Gdk.Display.get_default())
for name, t in texs.items():
    snap = Gtk.Snapshot()
    snap.append_texture(t, Graphene.Rect().init(0, 0, 200, 200))
    out = r.render_texture(snap.to_node(), Graphene.Rect().init(0, 0, 200, 200))
    d = Gdk.TextureDownloader.new(out)
    d.set_format(Gdk.MemoryFormat.R8G8B8A8)
    data, _ = d.download_bytes()
    px = data.get_data()[(100 * 200 + 100) * 4:(100 * 200 + 100) * 4 + 4]
    print("PLANO", name, type(t).__name__, "formato", t.get_format(), "pixel do meio RGBA:", list(px))
