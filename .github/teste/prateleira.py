"""Teste: o _quad novo da prateleira (faixas) desenhando caixas em perspectiva."""
import sys
import time
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

from mygames.ui import boxart, shelf  # noqa: E402
from mygames.ui.theme import theme_for  # noqa: E402

W, H = 1280, 720


class Fake:
    vx, vy, width = W / 2, H * 0.42, W


fake = Fake()
art = ROOT / "Sistema" / "app" / "assets" / "consoles" / "ps2" / "console.png"
surf = boxart.cover_surface(art, 640)
front = boxart.Strips(surf)
refl = boxart.Strips(boxart.reflection(surf))
theme = theme_for("home")
cover = boxart.Strips(boxart.add_sheen(boxart.front_surface(theme, "PS2", "Jogo de teste", 0.71)))
spine = boxart.Strips(boxart.spine_surface(theme, "PS2", "Jogo de teste", 0.08))
print("faixas: frente", len(front.pieces), "lombada", len(spine.pieces))

s = Gtk.Snapshot()
s.append_color(Gdk.RGBA(red=0.05, green=0.05, blue=0.08, alpha=1), Graphene.Rect().init(0, 0, W, H))
start = time.perf_counter()
calls = 0
# console de frente, com reflexo
shelf.Shelf._quad(fake, s, refl, (-150, 0), (150, 0), 200, 200 + 400 * boxart.REFLECTION, None, 0)
shelf.Shelf._quad(fake, s, front, (-150, 0), (150, 0), -200, 200, None, 0)
calls += 2
# caixas giradas dos lados (frente + lombada), com escurecimento
for k in range(1, 7):
    for side in (-1, 1):
        x = side * (180 + k * 70)
        shelf.Shelf._quad(fake, s, cover, (x - side * 10, -40 - k * 30), (x + side * 60, -260 - k * 30), -160, 160, None, 0.2)
        shelf.Shelf._quad(fake, s, spine, (x - side * 10, -40 - k * 30), (x - side * 30, -10 - k * 30), -160, 160, None, 0)
        calls += 2
# placeholder (imagem ainda carregando)
shelf.Shelf._quad(fake, s, None, (-600, -100), (-450, -300), -100, 100, Gdk.RGBA(red=0.3, green=0.3, blue=0.5, alpha=1), 0)
print(f"python: {calls} faces em {(time.perf_counter() - start) * 1000:.1f} ms")
node = s.to_node()
r = Gsk.CairoRenderer()
r.realize_for_display(Gdk.Display.get_default())
start = time.perf_counter()
out = r.render_texture(node, Graphene.Rect().init(0, 0, W, H))
print(f"cairo: {(time.perf_counter() - start) * 1000:.1f} ms")
out.save_to_png(str(OUT / "prateleira-cairo.png"))
