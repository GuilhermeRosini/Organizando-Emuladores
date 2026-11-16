"""Prateleira 3D de caixas: a selecionada de frente, as outras mostrando a lombada.

Desempenho:
- A perspectiva é calculada aqui, em Python, e cada face usa só uma
  inclinação 2D (skew) mais faixas verticais recortadas da mesma imagem, com
  bordas em pixels inteiros (sem frestas). Matrizes de projeção 3D saíam
  rosa/embaralhadas no GTK 4.24 do Windows; transformações 2D funcionam em
  todos os renderizadores. Faces de frente usam uma faixa só.
- Capas, lombadas e reflexos são preparados em threads, das caixas mais
  próximas para as mais distantes, e só ficam na memória as caixas por perto.
- O relógio de animação só roda enquanto algo se move.
"""
import itertools
import math
import os
import queue
import threading
from dataclasses import dataclass, field

from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk, Pango

from . import boxart

PERSPECTIVE = 1500
FLOOR = 0.66  # altura do chão (fração da altura da prateleira)
HORIZON = 0.42  # ponto de fuga
VISIBLE = 13  # caixas desenhadas de cada lado
PRELOAD = 20  # caixas preparadas antes de aparecer
KEEP = 32  # caixas mantidas na memória de cada lado
STRIP_ERROR = 1.2  # variação máxima de altura (px) dentro de uma faixa: erro de no máximo ~0,3 px
MAX_STRIPS = 160
SLOW_FRAME_US = 30_000  # quadros acima disso (≈33 fps) contam como lentos
FILTER = Gsk.ScalingFilter.LINEAR

EMPTY, QUEUED, READY = 0, 1, 2


@dataclass(eq=False)
class ShelfItem:
    """Uma caixa da prateleira. As imagens são preparadas fora da interface."""

    title: str
    meta: str
    make_front: object  # função (roda numa thread) que devolve uma superfície cairo
    make_spine: object = None  # None = objeto solto, sem caixa (ex.: console)
    depth: float = 0.08
    payload: object = None
    aspect: float = 0.71  # proporção usada até a imagem chegar
    placeholder: object = None  # cor (Gdk.RGBA) mostrada enquanto carrega
    make_glow: object = None  # contorno branco brilhante mostrado quando selecionado
    glow: object = field(default=None, repr=False)
    front: object = field(default=None, repr=False)
    front_reflection: object = field(default=None, repr=False)
    spine: object = field(default=None, repr=False)
    spine_reflection: object = field(default=None, repr=False)
    state: int = field(default=EMPTY, repr=False)
    token: int = field(default=0, repr=False)
    shown_aspect: float = field(default=0.0, repr=False)

    def __post_init__(self):
        """A proporção mostrada começa igual à proporção esperada."""
        self.shown_aspect = self.aspect

    @property
    def boxed(self):
        """É uma caixa com lombada (jogo) ou um objeto solto (console)?"""
        return self.make_spine is not None


def _prepare(item):
    """Roda numa thread: monta as texturas da caixa."""
    surface = item.make_front()
    if item.boxed:
        boxart.add_sheen(surface)
    result = {
        "front": boxart.texture(surface),
        "front_reflection": boxart.texture(boxart.reflection(surface)),
        "aspect": surface.get_width() / max(1, surface.get_height()),
    }
    if item.make_glow is not None:
        result["glow"] = boxart.texture(item.make_glow())
    if item.boxed:
        spine = item.make_spine()
        result["spine"] = boxart.texture(spine)
        result["spine_reflection"] = boxart.texture(boxart.reflection(spine))
    return result


class TextureLoader:
    """Fila com prioridade: a caixa mais perto da seleção é preparada primeiro."""

    def __init__(self, on_ready):
        """Liga 1 ou 2 threads de carregamento, conforme o processador."""
        self.on_ready = on_ready
        self.queue = queue.PriorityQueue()
        self.counter = itertools.count()
        workers = 2 if (os.cpu_count() or 1) >= 4 else 1
        for _ in range(workers):
            threading.Thread(target=self._work, daemon=True).start()

    def request(self, item, priority):
        """Põe a caixa na fila (prioridade = distância até a seleção)."""
        item.state = QUEUED
        self.queue.put((priority, next(self.counter), item, item.token))

    def _work(self):
        """Thread: prepara as imagens das caixas da fila e entrega para a interface."""
        while True:
            _priority, _order, item, token = self.queue.get()
            if item.token != token or item.state == READY:
                continue
            try:
                result = _prepare(item)
            except Exception:  # noqa: BLE001 - imagem ruim vira caixa sem capa
                result = {}
            GLib.idle_add(self.on_ready, item, token, result)


def _rgba(r, g, b, a=1.0):
    """Cria uma cor do GTK."""
    color = Gdk.RGBA()
    color.red, color.green, color.blue, color.alpha = r, g, b, a
    return color


def _rect(x, y, width, height):
    """Cria um retângulo do GTK."""
    return Graphene.Rect().init(x, y, width, height)


def _lerp_keys(keys, a):
    """Interpola (x, z, ângulo) entre as posições-chave."""
    if a >= keys[-1][0]:
        last = keys[-1]
        return last[1] + (a - last[0]) * last[4], last[2], last[3]
    for (a0, x0, z0, r0, _), (a1, x1, z1, r1, _) in zip(keys, keys[1:]):
        if a <= a1:
            t = (a - a0) / (a1 - a0)
            t = t * t * (3 - 2 * t)
            return x0 + (x1 - x0) * t, z0 + (z1 - z0) * t, r0 + (r1 - r0) * t
    return keys[0][1:4]


class Shelf(Gtk.Widget):
    """A prateleira 3D: desenha as caixas, anima a seleção e responde a clique e roda do mouse."""

    def __init__(self, on_selected, on_activate, on_slow=None):
        """Prepara o estado, o carregador de imagens e os controles de mouse."""
        super().__init__()
        self.on_selected = on_selected
        self.on_activate = on_activate
        self.on_slow = on_slow
        self.on_step = None  # chamado a cada caixa que a seleção passa (som de clique)
        self.items = []
        self.positions = {}
        self.loaded = {}
        self.index = 0
        self.position = 0.0
        self.reveal = 1.0  # entrada suave ao trocar de console
        self.reflections = True
        self.visible = VISIBLE  # caixas desenhadas de cada lado (menos no modo leve)
        self.tick_id = 0
        self.last_time = 0
        self.frame_samples = []
        self.wheel_locked = False
        self.layouts = {}
        self.vx = self.vy = 0.0
        self.width = 0
        self.loader = TextureLoader(self._on_loaded)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_focusable(True)

        # Arrastar com o mouse: a prateleira acompanha o ponteiro.
        self.dragging = False
        self.dragged = False  # o último gesto foi um arraste (não conta como clique)
        self.drag_start = 0.0
        self.drag_step = 1.0  # pixels de arraste que equivalem a uma caixa
        self.drag_samples = []  # (tempo, posição) recentes, para calcular o "arremesso"
        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)
        # Clique seleciona; dois cliques abrem o jogo (ou entram no console).
        click = Gtk.GestureClick.new()
        click.connect("released", self._on_click)
        self.add_controller(click)
        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

    # ---- Dados -----------------------------------------------------------------
    def set_items(self, items, index=0, animate=True):
        """Troca as caixas da prateleira (ao mudar de aba ou buscar)."""
        keep = {id(item) for item in items}
        for item in self.items:
            if id(item) not in keep:
                self._release(item)
        self.items = items
        self.positions = {id(item): i for i, item in enumerate(items)}
        self.index = max(0, min(index, len(items) - 1)) if items else 0
        self.position = float(self.index)
        self.reveal = 0.0 if animate else 1.0
        self._schedule()
        self._animate()
        self.queue_draw()
        if items:
            self.on_selected(self.index)

    def reload_item(self, item):
        """A imagem da caixa mudou (ex.: capa baixada agora)."""
        if id(item) in self.positions:
            self._release(item)
            self._schedule()

    @property
    def selected(self):
        """Caixa selecionada agora."""
        return self.items[self.index] if self.items else None

    def move(self, step):
        """Move a seleção (-1 esquerda, +1 direita)."""
        if not self.items:
            return
        target = max(0, min(self.index + step, len(self.items) - 1))
        if target != self.index:
            self.index = target
            if self.on_step:
                self.on_step()
            self._schedule()
            self._animate()
            self.on_selected(self.index)

    def select(self, index):
        """Seleciona a caixa de uma posição."""
        self.move(index - self.index)

    # ---- Texturas ----------------------------------------------------------------
    def _schedule(self):
        """Pede as imagens das caixas por perto e libera as distantes."""
        count = len(self.items)
        for distance in range(PRELOAD + 1):
            for i in {self.index - distance, self.index + distance}:
                if 0 <= i < count and self.items[i].state == EMPTY:
                    self.loader.request(self.items[i], distance)
        for key, item in list(self.loaded.items()):
            position = self.positions.get(key)
            if position is None or abs(position - self.index) > KEEP:
                self._release(item)

    def _release(self, item):
        """Libera as imagens de uma caixa da memória (refeitas quando ela voltar)."""
        item.token += 1
        item.state = EMPTY
        item.front = item.front_reflection = item.spine = item.spine_reflection = item.glow = None
        self.loaded.pop(id(item), None)

    def _on_loaded(self, item, token, result):
        """Imagens de uma caixa prontas: passa a desenhá-las."""
        if item.token != token:
            return GLib.SOURCE_REMOVE
        item.state = READY
        for key in ("front", "front_reflection", "spine", "spine_reflection", "glow"):
            setattr(item, key, result.get(key))
        item.aspect = result.get("aspect", item.aspect)
        position = self.positions.get(id(item))
        if position is None:
            self._release(item)
            return GLib.SOURCE_REMOVE
        self.loaded[id(item)] = item
        if abs(position - self.position) > VISIBLE + 1:
            item.shown_aspect = item.aspect
        else:
            self.queue_draw()
            self._animate()
        return GLib.SOURCE_REMOVE

    # ---- Animação ----------------------------------------------------------------
    def _animate(self):
        """Liga o relógio de animação (só roda enquanto algo se move)."""
        if not self.tick_id:
            self.last_time = 0
            self.tick_id = self.add_tick_callback(self._tick)

    def _tick(self, _widget, clock):
        """A cada quadro: desliza a prateleira, faz a entrada suave e ajusta proporções."""
        now = clock.get_frame_time()
        if self.last_time:
            self._measure(now - self.last_time)
        dt = min((now - self.last_time) / 1_000_000, 0.05) if self.last_time else 0.016
        self.last_time = now
        busy = False
        if self.dragging:
            pass  # durante o arraste quem manda na posição é o mouse
        elif abs(self.position - self.index) > 0.0005:
            self.position += (self.index - self.position) * (1 - math.exp(-dt * 11))
            busy = True
        else:
            self.position = float(self.index)
        if self.reveal < 1:
            self.reveal = min(1.0, self.reveal + dt * 2.8)
            busy = True
        first = max(0, math.floor(self.position) - VISIBLE)
        for item in self.items[first : math.ceil(self.position) + VISIBLE + 1]:
            if abs(item.shown_aspect - item.aspect) > 0.002:
                item.shown_aspect += (item.aspect - item.shown_aspect) * (1 - math.exp(-dt * 14))
                busy = True
            else:
                item.shown_aspect = item.aspect
        self.queue_draw()
        if not busy:
            self.tick_id = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _measure(self, interval):
        """Se a máquina não acompanhar a animação, avisa para ativar o modo leve."""
        if self.on_slow is None or interval > 250_000:
            return
        self.frame_samples.append(interval)
        if len(self.frame_samples) >= 90:
            samples = sorted(self.frame_samples)
            self.frame_samples = []
            if samples[len(samples) // 2] > SLOW_FRAME_US:
                callback, self.on_slow = self.on_slow, None
                callback()

    # ---- Geometria -------------------------------------------------------------
    def _item_size(self, item, box_height, width):
        """Largura e altura da caixa na tela (caixas deitadas ficam mais baixas)."""
        aspect = item.shown_aspect
        box_width = box_height * aspect
        limit = width * 0.34
        if box_width > limit:  # caixas deitadas (SNES, N64) ficam mais baixas
            box_width = limit
            box_height = limit / aspect
        return box_width, box_height

    def _place(self, offset, base_width, spine_step):
        """Posição (x, profundidade, ângulo) de uma caixa conforme a distância da selecionada."""
        if self.items and not self.items[0].boxed:
            # Objetos soltos (consoles): lado a lado, pouco girados.
            keys = [
                (0.0, 0.0, 0.0, 0.0, 0),
                (1.0, base_width * 1.05, -base_width * 0.55, 22.0, 0),
                (2.0, base_width * 1.85, -base_width * 1.0, 30.0, base_width * 0.7),
            ]
        else:
            keys = [
                # (distância, x, z, ângulo, passo extra por caixa)
                (0.0, 0.0, 0.0, 0.0, 0),
                (1.0, base_width * 0.86, -base_width * 0.42, 34.0, 0),
                (2.0, base_width * 1.28, -base_width * 0.52, 82.0, spine_step),
            ]
        x, z, angle = _lerp_keys(keys, abs(offset))
        side = 1 if offset > 0 else -1
        return side * x, z, side * angle

    def _layout_base(self):
        """Medidas usadas no desenho: tamanho da tela, da caixa central e espaço entre lombadas."""
        width, height = self.get_width(), self.get_height()
        box_height = height * 0.6
        center = self.items[self.index]
        base_width, base_height = self._item_size(center, box_height, width)
        return width, height, box_height, base_width, max(base_height * 0.095, 16)

    # ---- Desenho ---------------------------------------------------------------
    def do_snapshot(self, snapshot):
        """Desenha a prateleira: reflexos, caixas (das mais distantes para a central) e o título."""
        if not self.items:
            return
        width, height, box_height, base_width, spine_step = self._layout_base()
        if width <= 0 or height <= 0:
            return
        self.vx, self.vy, self.width = width / 2, height * HORIZON, width
        floor = height * (FLOOR - HORIZON)  # altura do chão em relação ao ponto de fuga
        rise = (1 - self.reveal) ** 2 * 500

        first = max(0, math.floor(self.position) - self.visible)
        last = min(len(self.items) - 1, math.ceil(self.position) + self.visible)
        entries = []
        for i in sorted(range(first, last + 1), key=lambda i: -abs(i - self.position)):
            item = self.items[i]
            offset = i - self.position
            x, z, angle = self._place(offset, base_width, spine_step)
            item_width, item_height = self._item_size(item, box_height, width)
            entries.append((item, x, z - rise, angle, item_width, item_height, offset))

        if self.reflections:
            for entry in entries:
                self._draw_item(snapshot, entry, floor, True)
        for entry in entries:
            self._draw_item(snapshot, entry, floor, False)
        self._draw_caption(snapshot, width, height)

    def _draw_item(self, snapshot, entry, floor, reflected):
        """Desenha uma caixa: frente e lombada (só as faces viradas para a tela) e o contorno."""
        item, x, z, angle, box_width, box_height, offset = entry
        phi = math.radians(angle)
        cos, sin = math.cos(phi), math.sin(phi)
        depth = box_height * item.depth if item.boxed else 0.0

        def world(lx, lz):
            """Converte um ponto da caixa (girada) para a posição no espaço."""
            return x + lx * cos - lz * sin, z + lx * sin + lz * cos

        faces = [((world(-box_width / 2, depth / 2), world(box_width / 2, depth / 2)), (-sin, cos), "front")]
        if item.boxed and abs(angle) > 0.5:
            side = 1 if angle > 0 else -1
            edge = side * box_width / 2
            faces.append(((world(edge, depth / 2), world(edge, -depth / 2)), (side * cos, side * sin), "spine"))

        visible = []
        for (a, b), normal, kind in faces:
            cx, cz = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            # Só desenha a face virada para quem olha.
            if normal[0] * -cx + normal[1] * (PERSPECTIVE - cz) > 0:
                visible.append((cz, a, b, kind))
        visible.sort(key=lambda face: face[0])

        dim = 0.32 * min(1.0, abs(offset) / 2.0) if item.boxed else 0
        for _cz, a, b, kind in visible:
            if reflected:
                texture = item.front_reflection if kind == "front" else item.spine_reflection
                if texture is not None:
                    self._quad(snapshot, texture, a, b, floor, floor + box_height * boxart.REFLECTION, None, 0)
            else:
                texture = item.front if kind == "front" else item.spine
                face_dim = dim if kind == "front" else 0
                self._quad(snapshot, texture, a, b, floor - box_height, floor, item.placeholder, face_dim)
                # Contorno brilhante do item selecionado, sumindo conforme ele sai do centro.
                glow = 1 - abs(offset) * 1.6
                if kind == "front" and item.glow is not None and glow > 0.01:
                    snapshot.push_opacity(min(1.0, glow))
                    self._quad(snapshot, item.glow, a, b, floor - box_height, floor, None, 0)
                    snapshot.pop()

    def _quad(self, snapshot, texture, a, b, y_top, y_bottom, placeholder, dim):
        """Desenha uma face vertical em perspectiva usando só transformações 2D.

        As bordas de cima e de baixo da face na tela são retas, então a linha do
        meio também é: uma inclinação (skew) só, para a face inteira, deixa essa
        linha reta e horizontal. Sobra a altura, que muda de um lado ao outro:
        a face é dividida em faixas verticais estreitas, cada uma com a altura do
        seu meio, mostrando o pedaço certo da mesma imagem (recorte retangular,
        barato). O número de faixas sai da variação de altura, para o erro ficar
        abaixo de ~0,3 px; as divisas caem em pixels inteiros, então não há
        frestas nem sobreposição. Face de frente = uma faixa só.
        """
        if texture is None and placeholder is None:
            return
        vx, vy, p = self.vx, self.vy, PERSPECTIVE
        (ax, az), (bx, bz) = a, b
        screen_a, screen_b = vx + ax * p / (p - az), vx + bx * p / (p - bz)
        if screen_b < screen_a:  # a textura sempre vai da esquerda para a direita
            ax, az, bx, bz = bx, bz, ax, az
            screen_a, screen_b = screen_b, screen_a
        if screen_b - screen_a < 0.5 or screen_b < -4 or screen_a > self.width + 4:  # fora da tela
            return
        wa, wb = p - az, p - bz
        top_a, bottom_a = vy + p * y_top / wa, vy + p * y_bottom / wa
        top_b, bottom_b = vy + p * y_top / wb, vy + p * y_bottom / wb
        height_a, height_b = bottom_a - top_a, bottom_b - top_b
        width = screen_b - screen_a
        # Origem num pixel inteiro: as divisas das faixas também ficam em pixels inteiros.
        origin = math.floor(screen_a)
        start, end = screen_a - origin, screen_b - origin
        middle_a = (top_a + bottom_a) / 2
        slope = ((top_b + bottom_b) / 2 - middle_a) / width
        middle_origin = middle_a - slope * start  # linha do meio na origem
        count = max(1, min(MAX_STRIPS, math.ceil(abs(height_b - height_a) / STRIP_ERROR), int(width)))
        shade = _rgba(0, 0, 0, dim) if dim > 0.02 else None

        snapshot.save()
        snapshot.translate(Graphene.Point().init(origin, middle_origin))
        if abs(slope) > 1e-6:
            snapshot.transform(Gsk.Transform().skew(0, math.degrees(math.atan(slope))))
        if count == 1:
            height = (height_a + height_b) / 2
            rect = Graphene.Rect().init(start, -height / 2, width, height)
            if texture is not None:
                snapshot.append_scaled_texture(texture, FILTER, rect)
            else:
                snapshot.append_color(placeholder, rect)
            if shade is not None:
                snapshot.append_color(shade, rect)
        else:
            # Coordenada s (0 a 1) da imagem em cada x da tela (perspectiva exata).
            dx, dz = bx - ax, bz - az

            def image_s(u):
                rx = u + origin - vx
                return (rx * wa - p * ax) / (p * dx + rx * dz)

            reach = max(height_a, height_b)
            u1, s1 = start, 0.0
            for i in range(1, count + 1):
                u0, s0 = u1, s1
                if i == count:
                    u1, s1 = end, 1.0
                else:
                    u1 = float(round(start + width * i / count))
                    if u1 <= u0:
                        continue
                    s1 = image_s(u1)
                strip = u1 - u0
                height = height_a + (height_b - height_a) * ((u0 + u1) / 2 - start) / width
                band = Graphene.Rect().init(u0, -height / 2, strip, height)
                if texture is not None:
                    full = strip / max(s1 - s0, 1e-9)  # largura da imagem inteira nesta faixa
                    snapshot.push_clip(Graphene.Rect().init(u0, -reach, strip, reach * 2))
                    snapshot.append_scaled_texture(
                        texture, FILTER, Graphene.Rect().init(u0 - s0 * full, -height / 2, full, height)
                    )
                    snapshot.pop()
                else:
                    snapshot.append_color(placeholder, band)
                if shade is not None:
                    snapshot.append_color(shade, band)
        snapshot.restore()

    def _draw_caption(self, snapshot, width, height):
        """Escreve o nome e as informações da caixa selecionada embaixo dela."""
        nearest = min(len(self.items) - 1, max(0, round(self.position)))
        item = self.items[nearest]
        alpha = max(0.0, 1 - abs(self.position - nearest) * 2.6) * self.reveal
        if alpha <= 0.01:
            return
        title_size = round(max(18, min(40, height * 0.052)))
        text_width = round(width * 0.7)
        title = self._layout(item.title, f"Sans Bold {title_size}", text_width)
        meta = self._layout(item.meta.upper(), f"Sans Bold {max(9, round(title_size * 0.32))}", text_width, 2.2)
        _, title_box = title.get_pixel_extents()
        top = height * (FLOOR + 0.035)

        snapshot.save()
        snapshot.translate(Graphene.Point().init((width - text_width) / 2, top + 2))
        # Sombra simples (texto deslocado), bem mais barata que sombra borrada.
        snapshot.append_layout(title, _rgba(0, 0, 0, 0.45 * alpha))
        snapshot.translate(Graphene.Point().init(0, -2))
        snapshot.append_layout(title, _rgba(1, 1, 1, alpha))
        snapshot.translate(Graphene.Point().init(0, title_box.height + 6))
        snapshot.append_layout(meta, _rgba(1, 1, 1, 0.66 * alpha))
        snapshot.restore()

    def _layout(self, text, font, width, spacing=0):
        """Texto pronto para desenhar (guardado para não refazer a cada quadro)."""
        key = (text, font, width, spacing)
        layout = self.layouts.get(key)
        if layout is None:
            if len(self.layouts) > 64:
                self.layouts.clear()
            layout = self.create_pango_layout(text)
            layout.set_font_description(Pango.FontDescription.from_string(font))
            layout.set_width(int(width * Pango.SCALE))
            layout.set_alignment(Pango.Alignment.CENTER)
            layout.set_ellipsize(Pango.EllipsizeMode.END)
            if spacing:
                attributes = Pango.AttrList()
                attributes.insert(Pango.attr_letter_spacing_new(int(spacing * Pango.SCALE)))
                layout.set_attributes(attributes)
            self.layouts[key] = layout
        return layout

    # ---- Entrada -----------------------------------------------------------------
    def _screen_x(self, offset, base_width, spine_step):
        """Posição horizontal na tela do centro de uma caixa (para saber onde o mouse clicou)."""
        x, z, _angle = self._place(offset, base_width, spine_step)
        return self.get_width() / 2 + x * PERSPECTIVE / (PERSPECTIVE - z)

    def _item_at(self, x):
        """Índice da caixa mais próxima de uma posição horizontal da tela."""
        _w, _h, _bh, base_width, spine_step = self._layout_base()
        return min(
            range(len(self.items)),
            key=lambda i: abs(self._screen_x(i - self.position, base_width, spine_step) - x),
        )

    def _on_click(self, _gesture, presses, x, _y):
        """Um clique seleciona a caixa; dois cliques em cima dela abrem."""
        self.grab_focus()
        if not self.items:
            return
        if presses == 1:
            self.dragged = False
            best = self._item_at(x)
            if best != self.index:
                self.select(best)
        elif presses == 2 and not self.dragged:
            self.on_activate(self.items[self.index])

    # Arraste: a prateleira segue o mouse; ao soltar, para na caixa mais próxima
    # (com um pequeno "arremesso" se o mouse foi solto em movimento).
    def _on_drag_begin(self, _gesture, _x, _y):
        """Começo do arraste: guarda de onde a prateleira partiu."""
        self.grab_focus()
        if not self.items:
            return
        _w, _h, _bh, base_width, spine_step = self._layout_base()
        center = self._screen_x(0, base_width, spine_step)
        neighbour = self._screen_x(1, base_width, spine_step)
        self.drag_step = max(40.0, abs(neighbour - center))
        self.drag_samples = []

    def _on_drag_update(self, _gesture, dx, _dy):
        """Durante o arraste: move a prateleira junto com o mouse."""
        if not self.items:
            return
        if not self.dragging:
            if abs(dx) < 8:
                return  # mexidinha pequena ainda é clique
            self.dragging = self.dragged = True
            self.drag_start = self.position + dx / self.drag_step  # sem pulo ao começar
            self.set_cursor_from_name("grabbing")
        last = len(self.items) - 1
        position = self.drag_start - dx / self.drag_step
        # Nas pontas a prateleira "estica" um pouco, mas não passa do limite.
        if position < 0:
            position = -0.35 * (1 - math.exp(position / 0.35))
        elif position > last:
            position = last + 0.35 * (1 - math.exp(-(position - last) / 0.35))
        self.position = position
        now = GLib.get_monotonic_time()
        self.drag_samples = [s for s in self.drag_samples if now - s[0] < 100_000] + [(now, position)]
        nearest = max(0, min(last, round(position)))
        if nearest != self.index:
            self.index = nearest
            if self.on_step:
                self.on_step()
            self._schedule()
            self.on_selected(self.index)
        self.queue_draw()

    def _on_drag_end(self, _gesture, _dx, _dy):
        """Fim do arraste: escolhe a caixa onde parar e anima até ela."""
        if not self.dragging:
            return
        self.dragging = False
        self.set_cursor(None)
        # Velocidade só dos últimos 100 ms (se o mouse parou antes de soltar, não há arremesso),
        # limitada para um puxão rápido não atravessar a prateleira inteira.
        now = GLib.get_monotonic_time()
        samples = [s for s in self.drag_samples if now - s[0] < 100_000]
        velocity = 0.0  # caixas por segundo
        if len(samples) >= 2:
            (t0, p0), (t1, p1) = samples[0], samples[-1]
            if t1 - t0 >= 15_000:
                velocity = max(-30.0, min(30.0, (p1 - p0) / ((t1 - t0) / 1_000_000)))
        target = round(self.position + velocity * 0.18)
        self.move(max(0, min(target, len(self.items) - 1)) - self.index)
        self._animate()

    def _on_scroll(self, _controller, dx, dy):
        """Roda do mouse: move uma caixa por vez."""
        delta = dx if abs(dx) > abs(dy) else dy
        if abs(delta) >= 0.05 and not self.wheel_locked:
            self.wheel_locked = True
            self.move(1 if delta > 0 else -1)
            GLib.timeout_add(130, self._unlock_wheel)
        return True

    def _unlock_wheel(self):
        """Libera a roda do mouse para o próximo movimento."""
        self.wheel_locked = False
        return GLib.SOURCE_REMOVE
