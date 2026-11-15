"""Fundo animado: degradê na cor do console, ondas de luz e partículas.

Desempenho: ondas e partículas são desenhadas uma única vez (numa thread)
como imagens que se repetem na horizontal/vertical. A cada quadro só
deslizamos essas imagens, o que é quase de graça para a GPU. Desenhar as
curvas a cada quadro custava ~55 ms e travava a interface.
"""
import math
import random
import threading

import cairo
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk

from . import boxart
from .theme import rgb, theme_for

FADE_US = 600_000
FRAME_US = 16_000  # o fundo se move devagar: ~60 quadros/s são de sobra
# Camadas de ondas: (velocidade em px/s, [(altura, amplitude, ciclos, fase, ciclos2, brilho)])
WAVE_LAYERS = [
    (14, [(0.30, 0.050, 1, 0.0, 3, 0.55), (0.50, 0.075, 1, 4.4, 2, 0.35), (0.66, 0.040, 2, 2.6, 3, 0.30)]),
    (-10, [(0.36, 0.070, 1, 1.7, 2, 0.40), (0.58, 0.055, 2, 0.9, 3, 0.55)]),
    (20, [(0.44, 0.045, 2, 3.1, 3, 0.70), (0.72, 0.065, 1, 5.2, 2, 0.45)]),
]
PARTICLE_LAYERS = [(-9, 26, 1.0), (-15, 18, 1.6)]  # (velocidade, quantidade, tamanho)


def _rgba(color, alpha=1.0):
    """Converte (r, g, b) em cor do GTK."""
    rgba = Gdk.RGBA()
    rgba.red, rgba.green, rgba.blue = color
    rgba.alpha = alpha
    return rgba


def _stop(offset, color, alpha=1.0):
    """Um ponto de cor de um degradê."""
    stop = Gsk.ColorStop()
    stop.offset = offset
    stop.color = _rgba(color, alpha)
    return stop


def _mix(a, b, t):
    """Mistura duas cores (t=0 primeira, t=1 segunda)."""
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def _draw_waves(cr, width, height, curves):
    """Desenha as ondas de luz (com brilho) de uma camada."""
    for base, amplitude, cycles, phase, cycles2, brightness in curves:
        # Ciclos inteiros: a curva emenda perfeitamente quando a imagem se repete.
        steps = 160
        for step in range(steps + 1):
            u = step / steps
            y = height * (
                base
                + amplitude * math.sin(u * 2 * math.pi * cycles + phase)
                + amplitude * 0.35 * math.sin(u * 2 * math.pi * cycles2 - phase)
            )
            (cr.move_to if step == 0 else cr.line_to)(u * width, y)
        path = cr.copy_path()
        for line_width, alpha in ((9, 0.035), (3, 0.07), (1.1, 0.42)):
            cr.new_path()
            cr.append_path(path)
            cr.set_line_width(line_width)
            cr.set_source_rgba(1, 1, 1, alpha * brightness)
            cr.stroke()


def _draw_particles(cr, width, height, count, size, rng):
    """Desenha os pontinhos de luz de uma camada."""
    cr.set_source_rgba(1, 1, 1, 0.35)
    for _ in range(count):
        cr.arc(rng.random() * width, rng.random() * height, size * rng.uniform(0.6, 1.3), 0, 2 * math.pi)
        cr.fill()


def _new_surface(width, height, scale):
    """Cria uma imagem transparente do tamanho da tela (já na escala do monitor)."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, round(width * scale)), max(1, round(height * scale)))
    cr = cairo.Context(surface)
    cr.scale(scale, scale)
    return surface, cr


def _render_layers(width, height, scale):
    """Roda numa thread: desenha as camadas que depois só deslizam."""
    waves = []
    for speed, curves in WAVE_LAYERS:
        surface, cr = _new_surface(width, height, scale)
        _draw_waves(cr, width, height, curves)
        waves.append((speed, boxart.texture(surface)))
    particles = []
    rng = random.Random(7)
    for speed, count, size in PARTICLE_LAYERS:
        surface, cr = _new_surface(width, height, scale)
        _draw_particles(cr, width, height, count, size, rng)
        particles.append((speed, boxart.texture(surface)))
    return waves, particles


def _elliptic(cr, cx, cy, rx, ry, stops):
    """Pinta um degradê redondo/oval (brilho atrás da prateleira e vinheta)."""
    gradient = cairo.RadialGradient(0, 0, 0, 0, 0, 1)
    gradient.set_matrix(cairo.Matrix(xx=1 / rx, yy=1 / ry, x0=-cx / rx, y0=-cy / ry))
    for offset, color, alpha in stops:
        gradient.add_color_stop_rgba(offset, *color, alpha)
    cr.set_source(gradient)
    cr.paint()


def _render_static(width, height, scale, colors):
    """Modo leve: o fundo inteiro vira uma única imagem parada (barato até sem GPU)."""
    top, bottom, glow = colors
    surface, cr = _new_surface(width, height, scale)
    linear = cairo.LinearGradient(0, 0, 0, height)
    linear.add_color_stop_rgb(0, *top)
    linear.add_color_stop_rgb(0.55, *_mix(top, bottom, 0.55))
    linear.add_color_stop_rgb(1, *bottom)
    cr.set_source(linear)
    cr.paint()
    _elliptic(cr, width * 0.5, height * 0.48, width * 0.55, height * 0.42, [(0, glow, 0.42), (0.6, glow, 0.10), (1, glow, 0)])
    for _speed, curves in WAVE_LAYERS:
        _draw_waves(cr, width, height, curves)
    rng = random.Random(7)
    for _speed, count, size in PARTICLE_LAYERS:
        _draw_particles(cr, width, height, count, size, rng)
    _elliptic(cr, width * 0.5, height * 0.45, width * 0.75, height * 0.75, [(0.55, (0, 0, 0), 0), (1, (0, 0, 0), 0.55)])
    return boxart.texture(surface)


class Backdrop(Gtk.Widget):
    """Fundo animado da janela, na cor do console da aba atual."""

    def __init__(self):
        """Começa com as cores da aba Início e liga a animação."""
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_can_target(False)
        self.colors = self._colors_of("home")
        self.from_colors = self.target_colors = self.colors
        self.fade_started = 0
        self.last_frame = 0
        self.animated = True
        self.layers = None  # (largura, altura, escala, ondas, partículas)
        self.wanted_size = None
        self.resize_source = 0
        self.render_generation = 0
        self.static = None  # (tamanho, cores, textura) no modo leve
        self.static_wanted = None
        self.tick_id = self.add_tick_callback(self._tick)

    @staticmethod
    def _colors_of(key):
        """Cores do fundo (alto, base e brilho) de uma aba."""
        theme = theme_for(key)
        return tuple(rgb(value) for value in (theme.top, theme.bottom, theme.glow))

    def set_theme(self, key):
        """Troca as cores do fundo para as de outra aba (com transição suave, exceto no modo leve)."""
        self.from_colors = self.colors
        self.target_colors = self._colors_of(key)
        if not self.animated:
            # Modo leve: troca direto, sem transição (cada passo exigiria redesenhar a imagem).
            self.colors = self.target_colors
            self.queue_draw()
            return
        self.fade_started = GLib.get_monotonic_time()
        self._ensure_tick()

    def set_animated(self, animated):
        """Modo leve: fundo parado, sem redesenhar a tela inteira o tempo todo."""
        self.animated = animated
        if not animated:
            self.fade_started = 0
            self.colors = self.target_colors
        self._ensure_tick()
        self.queue_draw()

    def _ensure_tick(self):
        """Liga o relógio de animação, se estiver desligado."""
        if not self.tick_id:
            self.tick_id = self.add_tick_callback(self._tick)

    def _tick(self, _widget, clock):
        """A cada quadro: avança a troca de cor e redesenha (no máximo ~60 vezes por segundo)."""
        now = clock.get_frame_time()
        fading = bool(self.fade_started)
        if fading:
            progress = min((now - self.fade_started) / FADE_US, 1)
            eased = 1 - (1 - progress) ** 3
            self.colors = tuple(_mix(a, b, eased) for a, b in zip(self.from_colors, self.target_colors))
            if progress >= 1:
                self.fade_started = 0
        if fading or (self.animated and now - self.last_frame >= FRAME_US):
            self.last_frame = now
            self.queue_draw()
        if not self.animated and not self.fade_started:
            self.tick_id = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    # ---- Camadas pré-desenhadas ------------------------------------------------
    def _request_layers(self, width, height):
        """Pede as camadas de ondas/partículas para o tamanho atual (espera o redimensionamento parar)."""
        scale = self.get_scale_factor()
        size = (width, height, scale)
        if self.wanted_size == size:
            return
        self.wanted_size = size
        if self.resize_source:
            GLib.source_remove(self.resize_source)
        # Espera o redimensionamento terminar antes de redesenhar as camadas.
        delay = 0 if self.layers is None else 250
        self.resize_source = GLib.timeout_add(delay, self._start_render, size)

    def _start_render(self, size):
        """Desenha as camadas numa thread."""
        self.resize_source = 0
        self.render_generation += 1
        generation = self.render_generation

        def work():
            """Roda numa thread: desenha as camadas."""
            waves, particles = _render_layers(*size)
            GLib.idle_add(self._layers_ready, generation, size, waves, particles)

        threading.Thread(target=work, daemon=True).start()
        return GLib.SOURCE_REMOVE

    def _layers_ready(self, generation, size, waves, particles):
        """Camadas prontas: passa a usá-las (se ainda forem do tamanho atual)."""
        if generation == self.render_generation:
            self.layers = (size[0], size[1], waves, particles)
            self.queue_draw()
        return GLib.SOURCE_REMOVE

    def _request_static(self, width, height):
        """Modo leve: pede a imagem parada do fundo para o tamanho e as cores atuais."""
        wanted = (width, height, self.get_scale_factor(), self.colors)
        if self.static_wanted == wanted:
            return
        self.static_wanted = wanted
        self.render_generation += 1
        generation = self.render_generation

        def work():
            """Roda numa thread: desenha o fundo parado."""
            texture = _render_static(*wanted)
            GLib.idle_add(self._static_ready, generation, wanted, texture)

        threading.Thread(target=work, daemon=True).start()

    def _static_ready(self, generation, wanted, texture):
        """Fundo parado pronto: passa a usá-lo."""
        if generation == self.render_generation:
            self.static = (wanted, texture)
            self.queue_draw()
        return GLib.SOURCE_REMOVE

    # ---- Desenho -----------------------------------------------------------------
    def do_snapshot(self, snapshot):
        """Desenha o fundo: degradês, ondas e partículas deslizando, e a vinheta."""
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0:
            return
        if not self.animated:
            self._request_static(width, height)
            if self.static is not None:
                snapshot.append_texture(self.static[1], Graphene.Rect().init(0, 0, width, height))
                return
        else:
            self._request_layers(width, height)
        top, bottom, glow = self.colors
        bounds = Graphene.Rect().init(0, 0, width, height)

        snapshot.append_linear_gradient(
            bounds,
            Graphene.Point().init(0, 0),
            Graphene.Point().init(0, height),
            [_stop(0, top), _stop(0.55, _mix(top, bottom, 0.55)), _stop(1, bottom)],
        )
        snapshot.append_radial_gradient(
            bounds,
            Graphene.Point().init(width * 0.5, height * 0.48),
            width * 0.55,
            height * 0.42,
            0,
            1,
            [_stop(0, glow, 0.42), _stop(0.6, glow, 0.10), _stop(1, glow, 0)],
        )

        if self.layers is not None:
            layer_width, layer_height, waves, particles = self.layers
            seconds = GLib.get_monotonic_time() / 1_000_000 if self.animated else 0
            for speed, texture in waves:
                offset = (seconds * speed) % layer_width
                for x in (-offset, layer_width - offset):
                    snapshot.append_texture(texture, Graphene.Rect().init(x, 0, layer_width, layer_height))
            for speed, texture in particles:
                offset = (seconds * speed) % layer_height
                for y in (-offset, layer_height - offset):
                    snapshot.append_texture(texture, Graphene.Rect().init(0, y, layer_width, layer_height))

        # Vinheta escura nas bordas, como uma sala de exposição.
        snapshot.append_radial_gradient(
            bounds,
            Graphene.Point().init(width * 0.5, height * 0.45),
            width * 0.75,
            height * 0.75,
            0,
            1,
            [_stop(0.55, (0, 0, 0), 0), _stop(1, (0, 0, 0), 0.55)],
        )
