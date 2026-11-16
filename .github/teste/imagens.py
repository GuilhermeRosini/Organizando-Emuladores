"""Teste: carrega as imagens dos consoles como o hub faz e salva o resultado em saida/."""
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Sistema" / "app"))
OUT = ROOT / "saida"
OUT.mkdir(exist_ok=True)

import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib  # noqa: E402

from mygames.ui import boxart  # noqa: E402

art = ROOT / "Sistema" / "app" / "assets" / "consoles" / "ps2" / "console.png"


def step(name, fn):
    try:
        fn()
        print("OK  ", name)
    except Exception:  # noqa: BLE001
        print("ERRO", name)
        traceback.print_exc()


# 1) Leitura do GTK + download (como cover_surface faz), sem redimensionar
def gtk_full():
    tex = Gdk.Texture.new_from_filename(str(art))
    print("   formato:", tex.get_format(), tex.get_width(), "x", tex.get_height())
    d = Gdk.TextureDownloader.new(tex)
    d.set_format(Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED)
    data, stride = d.download_bytes()
    print("   stride:", stride, "esperado:", tex.get_width() * 4, "bytes:", data.get_size())
    raw = bytearray(data.get_data())
    s = cairo.ImageSurface.create_for_data(raw, cairo.FORMAT_ARGB32, tex.get_width(), tex.get_height(), stride)
    s.write_to_png(str(OUT / "1-gtk-download.png"))
    tex.save_to_png(str(OUT / "1b-gtk-texture-direto.png"))


# 2) cover_surface do hub (com redução)
def hub_cover():
    boxart.cover_surface(art, 640).write_to_png(str(OUT / "2-cover_surface.png"))


# 3) cover_surface -> texture() -> PNG (o que vai para a tela)
def hub_texture():
    boxart.texture(boxart.cover_surface(art, 640)).save_to_png(str(OUT / "3-texture.png"))


# 4) cairo lendo o PNG direto
def cairo_direct():
    cairo.ImageSurface.create_from_png(str(art)).write_to_png(str(OUT / "4-cairo-direto.png"))


# 5) superfície cairo desenhada -> texture() (testa só a conversão para a GPU)
def drawn_texture():
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, 300, 200)
    cr = cairo.Context(s)
    cr.set_source_rgb(0, 0.4, 1)
    cr.rectangle(20, 20, 120, 80)
    cr.fill()
    s.write_to_png(str(OUT / "5a-desenho.png"))
    boxart.texture(s).save_to_png(str(OUT / "5b-desenho-texture.png"))


print("cairo", cairo.version, "| pycairo get_data len test")
for n, f in [("gtk_full", gtk_full), ("hub_cover", hub_cover), ("hub_texture", hub_texture),
             ("cairo_direct", cairo_direct), ("drawn_texture", drawn_texture)]:
    step(n, f)


def compare(a, b):
    """Diferença média por byte entre dois PNGs (0 = idênticos)."""
    sa, sb = cairo.ImageSurface.create_from_png(str(OUT / a)), cairo.ImageSurface.create_from_png(str(OUT / b))
    if (sa.get_width(), sa.get_height()) != (sb.get_width(), sb.get_height()):
        return f"tamanhos diferentes {sa.get_width()}x{sa.get_height()} vs {sb.get_width()}x{sb.get_height()}"
    da, db = bytes(sa.get_data()), bytes(sb.get_data())
    step_ = 97
    diff = sum(abs(da[i] - db[i]) for i in range(0, len(da), step_)) / (len(da) / step_)
    return f"{diff:.2f}"


for a, b in [("1-gtk-download.png", "4-cairo-direto.png"), ("1b-gtk-texture-direto.png", "4-cairo-direto.png"),
             ("3-texture.png", "2-cover_surface.png"), ("5b-desenho-texture.png", "5a-desenho.png")]:
    try:
        print("DIFF", a, "x", b, "=", compare(a, b))
    except Exception as e:  # noqa: BLE001
        print("DIFF", a, b, "erro", e)
