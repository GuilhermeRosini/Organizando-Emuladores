"""Gera as imagens das caixas: capas de reserva, lombadas, brilho e reflexo.

Tudo aqui pode rodar fora da interface (em threads): as funções devolvem
superfícies cairo, e texture() as transforma em texturas para a GPU.
"""
import cairo
import gi

gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Pango, PangoCairo  # noqa: E402

from .theme import rgb  # noqa: E402

SPINE_HEIGHT = 640
COVER_HEIGHT = 560
REFLECTION = 0.45  # parte da caixa refletida no chão
REFLECTION_ALPHA = 0.30


def texture(surface):
    """Transforma uma imagem cairo numa textura para a GPU."""
    surface.flush()
    return Gdk.MemoryTexture.new(
        surface.get_width(),
        surface.get_height(),
        Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
        GLib.Bytes.new(bytes(surface.get_data())),
        surface.get_stride(),
    )


def is_opaque(surface):
    """A imagem não tem nenhum pixel transparente? (pode esconder o que está atrás)"""
    surface.flush()
    return min(memoryview(surface.get_data())[3::4], default=255) == 255


class Strips:
    """A mesma imagem cortada em faixas verticais, cada uma numa textura.

    A prateleira desenha as faces em perspectiva faixa por faixa (ver
    shelf._quad); com cada faixa numa textura própria não é preciso recortar
    nada na hora de desenhar, o que deixa o desenho barato.
    """

    __slots__ = ("pieces",)

    def __init__(self, surface, count):
        """Corta em até `count` faixas: [(início, fim, textura)], com início/fim de 0 a 1."""
        surface.flush()
        width, height = surface.get_width(), surface.get_height()
        count = max(1, min(count, width))
        self.pieces = []
        for i in range(count):
            x0, x1 = round(width * i / count), round(width * (i + 1) / count)
            if x1 <= x0:
                continue
            part = cairo.ImageSurface(cairo.FORMAT_ARGB32, x1 - x0, height)
            cr = cairo.Context(part)
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.set_source_surface(surface, -x0, 0)
            cr.paint()
            self.pieces.append((x0 / width, x1 / width, texture(part)))


def _layout(cr, text, font, width=None, align=Pango.Alignment.LEFT, spacing=0):
    """Prepara um texto (fonte, largura, alinhamento, espaçamento) para desenhar."""
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(Pango.FontDescription.from_string(font))
    if width is not None:
        layout.set_width(int(width * Pango.SCALE))
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    layout.set_alignment(align)
    if spacing:
        attributes = Pango.AttrList()
        attributes.insert(Pango.attr_letter_spacing_new(int(spacing * Pango.SCALE)))
        layout.set_attributes(attributes)
    layout.set_text(text, -1)
    return layout


def _fit(cr, text, family, size, width, max_lines, align=Pango.Alignment.CENTER):
    """Diminui a fonte até o texto caber em max_lines linhas."""
    while True:
        layout = _layout(cr, text, f"{family} {size:.0f}", width, align)
        if layout.get_line_count() <= max_lines or size <= 14:
            layout.set_ellipsize(Pango.EllipsizeMode.END)
            layout.set_height(-max_lines)
            return layout
        size *= 0.88


def cover_surface(path, max_height=COVER_HEIGHT):
    """Lê a capa já reduzida (para não gastar memória com scans gigantes).

    Usa o leitor de imagens do próprio GTK (PNG, JPEG, TIFF...), que funciona
    igual no Linux e no Windows, e reduz o tamanho com o cairo.
    """
    image = Gdk.Texture.new_from_filename(str(path))
    downloader = Gdk.TextureDownloader.new(image)
    downloader.set_format(Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED)
    data, stride = downloader.download_bytes()
    width, height = image.get_width(), image.get_height()
    full = cairo.ImageSurface.create_for_data(bytearray(data.get_data()), cairo.FORMAT_ARGB32, width, height, stride)
    if height <= max_height:
        return full
    scale = max_height / height
    small = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, round(width * scale)), max_height)
    cr = cairo.Context(small)
    cr.scale(scale, scale)
    pattern = cairo.SurfacePattern(full)
    pattern.set_filter(cairo.FILTER_GOOD)
    cr.set_source(pattern)
    cr.paint()
    return small


def add_sheen(surface):
    """Reflexo de luz do plástico, aplicado uma vez na própria capa."""
    width, height = surface.get_width(), surface.get_height()
    cr = cairo.Context(surface)
    cr.set_operator(cairo.OPERATOR_ATOP)
    gradient = cairo.LinearGradient(0, 0, width, height)
    gradient.add_color_stop_rgba(0, 1, 1, 1, 0.16)
    gradient.add_color_stop_rgba(0.45, 1, 1, 1, 0)
    gradient.add_color_stop_rgba(1, 0, 0, 0, 0.12)
    cr.set_source(gradient)
    cr.paint()
    return surface


def reflection(surface, scale=0.5):
    """Parte de baixo da imagem, de cabeça para baixo e sumindo (o reflexo no chão)."""
    width, height = surface.get_width(), surface.get_height()
    out = cairo.ImageSurface(
        cairo.FORMAT_ARGB32, max(1, round(width * scale)), max(1, round(height * REFLECTION * scale))
    )
    cr = cairo.Context(out)
    cr.scale(scale, scale)
    cr.translate(0, height)
    cr.scale(1, -1)
    pattern = cairo.SurfacePattern(surface)
    pattern.set_filter(cairo.FILTER_GOOD)
    cr.set_source(pattern)
    fade = cairo.LinearGradient(0, height, 0, height * (1 - REFLECTION))
    fade.add_color_stop_rgba(0, 0, 0, 0, REFLECTION_ALPHA)
    fade.add_color_stop_rgba(1, 0, 0, 0, 0)
    cr.mask(fade)
    return out


def front_surface(theme, label, title, aspect, glyph=None, footer=""):
    """Capa de reserva no estilo do console: faixa no topo e título grande."""
    height = COVER_HEIGHT
    width = round(height * aspect)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    cr = cairo.Context(surface)
    top, bottom, glow, spine = (rgb(c) for c in (theme.top, theme.bottom, theme.glow, theme.spine))

    gradient = cairo.LinearGradient(0, 0, width * 0.4, height)
    gradient.add_color_stop_rgb(0, *top)
    gradient.add_color_stop_rgb(1, *bottom)
    cr.set_source(gradient)
    cr.paint()
    shine = cairo.RadialGradient(width * 0.5, height * 0.55, 0, width * 0.5, height * 0.55, height * 0.6)
    shine.add_color_stop_rgba(0, *glow, 0.55)
    shine.add_color_stop_rgba(1, *glow, 0)
    cr.set_source(shine)
    cr.paint()
    # Linhas finas, lembrando o fundo do hub.
    cr.set_line_width(1.6)
    for index in range(9):
        cr.set_source_rgba(1, 1, 1, 0.05 + 0.02 * (index % 3))
        y = height * (0.25 + index * 0.07)
        cr.move_to(0, y)
        cr.curve_to(width * 0.35, y - 60, width * 0.65, y + 70, width, y - 10)
        cr.stroke()

    band = height * 0.11
    cr.set_source_rgb(*spine)
    cr.rectangle(0, 0, width, band)
    cr.fill()
    cr.set_source_rgba(1, 1, 1, 0.92)
    layout = _layout(cr, label.upper(), f"Sans Bold {band * 0.32:.0f}", spacing=band * 0.06)
    _, logical = layout.get_pixel_extents()
    cr.move_to(width * 0.06, (band - logical.height) / 2)
    PangoCairo.show_layout(cr, layout)

    cr.set_source_rgb(1, 1, 1)
    if glyph:
        layout = _layout(cr, glyph, f"Sans Bold {height * 0.2:.0f}", width, Pango.Alignment.CENTER)
        _, logical = layout.get_pixel_extents()
        cr.move_to(0, height * 0.42 - logical.height / 2)
        PangoCairo.show_layout(cr, layout)
        text_top = height * 0.6
    else:
        text_top = height * 0.36
    layout = _fit(cr, title, "Sans Bold", height * 0.075, width * 0.84, 4)
    _, logical = layout.get_pixel_extents()
    cr.move_to(width * 0.08, text_top)
    PangoCairo.show_layout(cr, layout)
    if footer:
        cr.set_source_rgba(1, 1, 1, 0.65)
        layout = _layout(cr, footer.upper(), f"Sans Bold {height * 0.026:.0f}", width * 0.84, Pango.Alignment.CENTER, 2)
        cr.move_to(width * 0.08, height * 0.9)
        PangoCairo.show_layout(cr, layout)
    return surface


def spine_surface(theme, label, title, depth):
    """Lombada: faixa com a sigla do console no topo e o título na vertical."""
    height = SPINE_HEIGHT
    width = max(24, round(height * depth))
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    cr = cairo.Context(surface)
    spine, text, top = rgb(theme.spine), rgb(theme.spine_text), rgb(theme.top)
    gradient = cairo.LinearGradient(0, 0, width, 0)
    gradient.add_color_stop_rgb(0, *(c * 0.75 for c in spine))
    gradient.add_color_stop_rgb(0.5, *(min(1, c * 1.25 + 0.03) for c in spine))
    gradient.add_color_stop_rgb(1, *(c * 0.75 for c in spine))
    cr.set_source(gradient)
    cr.paint()
    band = height * 0.09
    cr.set_source_rgb(*top)
    cr.rectangle(0, 0, width, band)
    cr.fill()

    font_size = min(width * 0.42, 26)
    cr.save()
    cr.translate(width / 2, band / 2)
    cr.rotate(1.5708)
    cr.set_source_rgba(1, 1, 1, 0.95)
    layout = _layout(cr, label.upper(), f"Sans Bold {font_size * 0.62:.0f}", spacing=1)
    _, logical = layout.get_pixel_extents()
    if logical.width > band * 0.9:
        layout = _layout(cr, label.upper()[:3], f"Sans Bold {font_size * 0.55:.0f}")
        _, logical = layout.get_pixel_extents()
    cr.move_to(-logical.width / 2, -logical.height / 2)
    PangoCairo.show_layout(cr, layout)
    cr.restore()

    cr.save()
    cr.translate(width / 2, band + height * 0.04)
    cr.rotate(1.5708)
    cr.set_source_rgb(*text)
    layout = _layout(cr, title, f"Sans Bold {font_size:.0f}")
    layout.set_width(int((height - band - height * 0.08) * Pango.SCALE))
    layout.set_ellipsize(Pango.EllipsizeMode.END)
    _, logical = layout.get_pixel_extents()
    cr.move_to(0, -logical.height / 2)
    PangoCairo.show_layout(cr, layout)
    cr.restore()
    return surface
