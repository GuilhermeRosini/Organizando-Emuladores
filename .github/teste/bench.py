"""Mede o custo (Python) de desenhar 1200 faixas de formas diferentes."""
import time

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

tex = Gdk.MemoryTexture.new(64, 64, Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED, GLib.Bytes.new(bytes(64 * 64 * 4)), 256)
F = Gsk.ScalingFilter.LINEAR
N = 1200
R = Graphene.Rect


def snap_clip():
    s = Gtk.Snapshot()
    for i in range(N):
        s.push_clip(R().init(i, -5, 1, 10))
        s.append_scaled_texture(tex, F, R().init(i - 3, -5, 10, 10))
        s.pop()
    s.to_node()


def nodes_clip():
    s = Gtk.Snapshot()
    out = []
    for i in range(N):
        out.append(Gsk.ClipNode.new(Gsk.TextureScaleNode.new(tex, R().init(i - 3, -5, 10, 10), F), R().init(i, -5, 1, 10)))
    s.append_node(Gsk.ContainerNode.new(out))
    s.to_node()


def snap_plain():
    s = Gtk.Snapshot()
    for i in range(N):
        s.append_scaled_texture(tex, F, R().init(i - 3, -5, 10, 10))
    s.to_node()


def nodes_plain():
    s = Gtk.Snapshot()
    s.append_node(Gsk.ContainerNode.new([Gsk.TextureScaleNode.new(tex, R().init(i - 3, -5, 10, 10), F) for i in range(N)]))
    s.to_node()


def math_only():
    t = 0.0
    for i in range(N):
        t += (i * 1.5 - 3) / (i + 2.5) * 0.5 + i / 3
    return t


for name, fn in [("snap_clip", snap_clip), ("nodes_clip", nodes_clip), ("snap_plain", snap_plain),
                 ("nodes_plain", nodes_plain), ("math_only", math_only)]:
    fn()
    start = time.perf_counter()
    for _ in range(10):
        fn()
    print(f"BENCH {name:12} {(time.perf_counter() - start) / 10 * 1000:6.2f} ms para {N} faixas")
