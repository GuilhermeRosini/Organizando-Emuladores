"""Teste: prateleira real (do_snapshot) com o _quad novo x a matriz antiga (referência exata no Vulkan)."""
import importlib.util
import sys
import time
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

Gtk.init()
from mygames.ui import boxart, shelf  # noqa: E402

spec = importlib.util.spec_from_file_location("mygames.ui.shelf_old", ROOT / ".github" / "teste" / "shelf_old.py")
shelf_old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shelf_old)

W, H = 1280, 720
art = ROOT / "Sistema" / "app" / "assets" / "consoles"


def fake_box(w, h, color):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    cr.set_source_rgb(*color)
    cr.paint()
    cr.set_source_rgb(1, 1, 1)
    for i in range(0, w, 37):
        cr.rectangle(i, 0, 3, h)
    for j in range(0, h, 53):
        cr.rectangle(0, j, w, 3)
    cr.fill()
    return surf


def build(module, boxed):
    class Test(module.Shelf):
        def get_width(self):
            return W

        def get_height(self):
            return H

        def _draw_caption(self, *args):
            pass

    sh = Test(lambda i: None, lambda i: None)
    items = []
    if boxed:
        for k in range(30):
            color = ((k * 0.13) % 1, 0.3, 0.7)
            items.append(module.ShelfItem(title="", meta="", make_front=lambda c=color: fake_box(400, 563, c),
                                          make_spine=lambda c=color: fake_box(46, 563, (0.8, 0.1, 0.1))))
    else:
        for d in sorted(p for p in art.iterdir() if (p / "console.png").exists()):
            items.append(module.ShelfItem(title="", meta="", make_front=lambda d=d: boxart.cover_surface(d / "console.png", 640)))
    for it in items:
        result = module._prepare(it)
        for key in ("front", "front_reflection", "spine", "spine_reflection", "glow"):
            setattr(it, key, result.get(key))
        it.aspect = it.shown_aspect = result.get("aspect", it.aspect)
        it.state = module.READY
    sh.items = items
    sh.positions = {id(x): i for i, x in enumerate(items)}
    return sh


def frame(sh, position):
    sh.index = round(position)
    sh.position = position
    s = Gtk.Snapshot()
    s.append_color(Gdk.RGBA(red=0.05, green=0.05, blue=0.08, alpha=1), Graphene.Rect().init(0, 0, W, H))
    start = time.perf_counter()
    for _ in range(20):
        s2 = Gtk.Snapshot()
        sh.do_snapshot(s2)
        s2.to_node()
    took = (time.perf_counter() - start) / 20 * 1000
    sh.do_snapshot(s)
    return s.to_node(), took


def pixels(tex):
    d = Gdk.TextureDownloader.new(tex)
    d.set_format(Gdk.MemoryFormat.R8G8B8A8)
    return d.download_bytes()[0].get_data()


renderers = {}
for name, cls in (("vulkan", Gsk.VulkanRenderer), ("cairo", Gsk.CairoRenderer)):
    r = cls()
    try:
        r.realize_for_display(Gdk.Display.get_default())
        renderers[name] = r
    except Exception as e:  # noqa: BLE001
        print("NAO REALIZOU", name, e)

for boxed in (False, True):
    new, old = build(shelf, boxed), build(shelf_old, boxed)
    for position in (2.0, 2.35, 2.5, 2.8):
        node_new, t_new = frame(new, position)
        shelf._STATS.clear()
        new.do_snapshot(Gtk.Snapshot())
        st = shelf._STATS
        print("FACES", len(st), "faixas", sum(c for c, *_ in st), "maiores:", sorted(st, reverse=True)[:8])
        node_old, t_old = frame(old, position)
        tag = f"{'jogos' if boxed else 'consoles'}-{position}"
        ref = renderers["vulkan"].render_texture(node_old, Graphene.Rect().init(0, 0, W, H))
        ref.save_to_png(str(OUT / f"p-{tag}-ref.png"))
        refpx = pixels(ref)
        for rname, r in renderers.items():
            start = time.perf_counter()
            out = r.render_texture(node_new, Graphene.Rect().init(0, 0, W, H))
            t_render = (time.perf_counter() - start) * 1000
            out.save_to_png(str(OUT / f"p-{tag}-{rname}.png"))
            px = pixels(out)
            diffs = [abs(px[i] - refpx[i]) for i in range(0, len(px), 3)]
            big = sum(1 for d in diffs if d > 40)
            print(f"RES {tag:16} {rname:6} python novo {t_new:5.2f} ms (antigo {t_old:5.2f}) | render {t_render:6.1f} ms"
                  f" | dif media {sum(diffs) / len(diffs):.3f} | pixels muito diferentes {big}")
