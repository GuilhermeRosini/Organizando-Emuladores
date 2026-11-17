"""Prateleira 3D de caixas: a selecionada de frente, as outras mostrando a lombada.

Desempenho:
- A perspectiva é calculada aqui, em Python. Cada face inclinada é desenhada
  em faixas verticais estreitas, retas (sem transformação nenhuma), com as
  divisas em pixels inteiros: sem frestas, e o erro de posição fica abaixo de
  ~0,3 px. Matrizes de projeção 3D e inclinações (skew) saíam erradas no GTK
  4.24 do Windows (rosa, embaralhado ou com frestas); retângulos recortados
  funcionam igual em todos os renderizadores. Faces de frente = uma faixa só.
- Partes de caixas totalmente escondidas atrás da vizinha não são desenhadas.
- Onde a matriz de projeção 3D funciona (testado uma vez ao abrir, desenhando
  um xadrez colorido e conferindo os pixels), cada face é uma imagem só com a
  matriz: o jeito mais leve. As faixas só entram onde a matriz sai errada
  (GTK 4.24 do Windows em alguns vídeos, ou o renderizador cairo).
- Em movimento as faixas são mais largas (erro invisível com a caixa andando);
  quando a prateleira para, o último quadro sai com a precisão total e fica
  guardado, para não ser recalculado à toa.
- Capas, lombadas e reflexos são preparados em threads, das caixas mais
  próximas para as mais distantes, e só ficam na memória as caixas por perto.
- O relógio de animação só roda enquanto algo se move.
- Roda sem fim (tela Início): a seleção e a posição ficam sempre entre 0 e o
  número de itens. Ao passar do último para o primeiro, a posição é deslocada
  de uma volta inteira (a imagem na tela é a mesma), então nada cresce e não há
  caixas a mais na memória: só se desenham as poucas que cabem na tela.
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
STRIP_ERROR = 0.3  # erro máximo (px) de posição dentro de uma faixa
REFLECTION_ERROR = 0.6  # reflexos são apagados: podem errar um pouco mais (menos faixas)
SIDE_REFLECTION_ERROR = 1.5  # reflexos das pilhas de lado: bem apagados e cobertos pelos da frente
UNIT = 1000.0  # quadrado em que a textura é desenhada antes da matriz de projeção
MOTION_ERROR = 5.0  # em movimento o erro pode ser 5x maior (ninguém vê): ~5x menos faixas
MAX_STRIPS = 240
SLOW_FRAME_US = 30_000  # quadros acima disso (≈33 fps) contam como lentos
FILTER = Gsk.ScalingFilter.LINEAR
BADGE_SECONDS = 0.65  # o console encolhe e vai para o canto ao entrar nele
BADGE_HEIGHT = 0.16  # tamanho final do console no canto (fração da altura da prateleira)
BADGE_WIDTH = 0.13  # ... e no máximo esta fração da largura
BADGE_MARGIN = 26  # px até as bordas

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
    opaque: bool = field(default=False, repr=False)  # sem transparência: esconde o que está atrás
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
        result["opaque"] = boxart.is_opaque(surface) and boxart.is_opaque(spine)
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


def _covered(face, cover):
    """Trecho de x (na tela) em que `cover` esconde `face` por inteiro, na vertical; ou None.

    As bordas de cima e de baixo das duas faces são retas, então basta comparar
    nas pontas do trecho em comum (com 0,25 px de tolerância: caixas iguais lado
    a lado têm a mesma altura). Deixa 1 px de folga de cada lado (bordas
    suavizadas da face da frente).
    """
    left = max(face[2], cover[2]) + 1
    right = min(face[3], cover[3]) - 1
    if right <= left:
        return None

    def edges(f, x):
        t = (x - f[2]) / max(f[3] - f[2], 1e-9)
        return f[4] + (f[6] - f[4]) * t, f[5] + (f[7] - f[5]) * t

    for x in (left, right):
        face_top, face_bottom = edges(face, x)
        cover_top, cover_bottom = edges(cover, x)
        if cover_top > face_top + 0.25 or cover_bottom < face_bottom - 0.25:
            return None
    return left, right


def _matrix_works(shelf, renderer):
    """Teste do _quad_matrix no renderizador de verdade: xadrez de 4 cores em perspectiva."""
    import cairo

    size, cell = 256, 32
    colors = [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0)]
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    for cy in range(size // cell):
        for cx in range(size // cell):
            cr.set_source_rgb(*colors[(cx + 2 * cy) % 4])
            cr.rectangle(cx * cell, cy * cell, cell, cell)
            cr.fill()
    texture = boxart.texture(surface)
    width, height = 320, 240
    saved = shelf.vx, shelf.vy, shelf.width
    shelf.vx, shelf.vy, shelf.width = width / 2, height / 2, width
    a, b, y_top, y_bottom = (-70.0, 250.0), (70.0, -250.0), -90.0, 90.0
    try:
        snapshot = Gtk.Snapshot()
        snapshot.append_color(_rgba(0, 0, 0), _rect(0, 0, width, height))
        shelf._quad_matrix(snapshot, texture, a, b, y_top, y_bottom, None, 0)
        rendered = renderer.render_texture(snapshot.to_node(), _rect(0, 0, width, height))
    finally:
        shelf.vx, shelf.vy, shelf.width = saved
    downloader = Gdk.TextureDownloader.new(rendered)
    downloader.set_format(Gdk.MemoryFormat.R8G8B8A8)
    data, stride = downloader.download_bytes()
    pixels = data.get_data()
    p = PERSPECTIVE
    good = total = 0
    for cy in range(size // cell):
        for cx in range(size // cell):
            s, t = (cx + 0.5) * cell / size, (cy + 0.5) * cell / size
            w = p - (a[1] + (b[1] - a[1]) * s)
            x = width / 2 + p * (a[0] + (b[0] - a[0]) * s) / w
            y = height / 2 + p * (y_top + (y_bottom - y_top) * t) / w
            i = int(y) * stride + int(x) * 4
            expected = colors[(cx + 2 * cy) % 4]
            total += 1
            if all(abs(pixels[i + k] - 255 * expected[k]) < 60 for k in range(3)):
                good += 1
    return good >= total * 0.95


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
        self.loop = False  # roda sem fim: depois do último item vem o primeiro
        self.visible = VISIBLE  # caixas desenhadas de cada lado (menos no modo leve)
        self.tick_id = 0
        self.last_time = 0
        self.frame_samples = []
        self.wheel_locked = False
        self.layouts = {}
        self.vx = self.vy = 0.0
        self.width = 0
        self.scale = 1.0  # pixels físicos por pixel lógico (divisas das faixas caem em pixels inteiros)
        self.moving = False  # animando ou arrastando: desenho mais leve
        self.use_matrix = False  # a matriz 3D funciona neste computador? (testado ao aparecer)
        self.matrix_tested = False
        self.matrix = Graphene.Matrix()
        self.unit = Graphene.Rect().init(0, 0, UNIT, UNIT)
        self.cache_key = None  # prateleira parada: o desenho fica guardado
        self.cache_node = None
        # Console em que se entrou, pequeno no canto de baixo à esquerda (clicar volta ao Início).
        self.badge = None  # (frente, contorno, retângulo de onde saiu)
        self.badge_progress = 1.0
        self.badge_started = 0
        self.badge_rect = None  # onde foi desenhado por último (para o clique)
        self.badge_hover = False
        self.on_badge = None
        self.ignore_double = False  # o 2º clique de um duplo clique no console do canto não abre nada
        self.loader = TextureLoader(self._on_loaded)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_focusable(True)
        self.connect("map", lambda _widget: GLib.idle_add(self._test_matrix))

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
        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", lambda _c, x, y: self._update_hover(x, y))
        motion.connect("leave", lambda _c: self._update_hover(-1, -1))
        self.add_controller(motion)

    # ---- Dados -----------------------------------------------------------------
    def set_items(self, items, index=0, animate=True, loop=False):
        """Troca as caixas da prateleira (ao mudar de aba ou buscar). loop=True: roda sem fim."""
        keep = {id(item) for item in items}
        for item in self.items:
            if id(item) not in keep:
                self._release(item)
        self.items = items
        self.loop = loop and len(items) > 1
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
        """Move a seleção (-1 esquerda, +1 direita). Na roda sem fim, do último vai para o primeiro."""
        if not self.items:
            return
        target = self.index + step
        if self.loop:
            target = self._rewind(target)
        else:
            target = max(0, min(target, len(self.items) - 1))
        if target != self.index:
            self.index = target
            if self.on_step:
                self.on_step()
            self._schedule()
            self._animate()
            self.on_selected(self.index)

    def select(self, index):
        """Seleciona a caixa de uma posição (na roda sem fim, pelo caminho mais curto)."""
        step = index - self.index
        if self.loop:
            count = len(self.items)
            step = (step + count // 2) % count - count // 2
        self.move(step)

    def _rewind(self, target):
        """Roda sem fim: traz o alvo de volta para 0..n-1 e desloca a posição de voltas inteiras.

        Deslocar a posição de n caixas não muda nada na tela (o desenho é o
        mesmo a cada volta), então a animação continua lisa e os números nunca
        crescem: é a "reiniciada" da roda a cada volta.
        """
        count = len(self.items)
        wrapped = target % count
        shift = wrapped - target
        if shift:
            self.position += shift
            self.drag_start += shift
            self.drag_samples = [(time, position + shift) for time, position in self.drag_samples]
        return wrapped

    def _distance(self, a, b):
        """Quantas caixas separam duas posições (na roda sem fim, pelo lado mais curto)."""
        distance = abs(a - b)
        if self.loop:
            distance %= len(self.items)
            distance = min(distance, len(self.items) - distance)
        return distance

    # ---- Texturas ----------------------------------------------------------------
    def _schedule(self):
        """Pede as imagens das caixas por perto e libera as distantes."""
        count = len(self.items)
        for distance in range(PRELOAD + 1):
            for i in {self.index - distance, self.index + distance}:
                if self.loop:
                    i %= count
                if 0 <= i < count and self.items[i].state == EMPTY:
                    self.loader.request(self.items[i], distance)
        for key, item in list(self.loaded.items()):
            position = self.positions.get(key)
            if position is None or self._distance(position, self.index) > KEEP:
                self._release(item)

    def _release(self, item):
        """Libera as imagens de uma caixa da memória (refeitas quando ela voltar)."""
        item.token += 1
        item.state = EMPTY
        item.front = item.front_reflection = None
        item.spine = item.spine_reflection = item.glow = None
        item.opaque = False
        self.loaded.pop(id(item), None)

    def _on_loaded(self, item, token, result):
        """Imagens de uma caixa prontas: passa a desenhá-las."""
        if item.token != token:
            return GLib.SOURCE_REMOVE
        item.state = READY
        for key in ("front", "front_reflection", "spine", "spine_reflection", "glow"):
            setattr(item, key, result.get(key))
        item.opaque = result.get("opaque", False)
        item.aspect = result.get("aspect", item.aspect)
        position = self.positions.get(id(item))
        if position is None:
            self._release(item)
            return GLib.SOURCE_REMOVE
        self.loaded[id(item)] = item
        if self._distance(position, self.position) > VISIBLE + 1:
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
        if self.badge is not None and self.badge_progress < 1:
            if not self.badge_started:
                self.badge_started = now
            self.badge_progress = min(1.0, (now - self.badge_started) / (BADGE_SECONDS * 1_000_000))
            busy = True
        first = max(0, math.floor(self.position) - VISIBLE)
        nearby = self.items if self.loop else self.items[first : math.ceil(self.position) + VISIBLE + 1]
        for item in nearby:
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
        if self.items[0].boxed:
            base_width, base_height = self._item_size(self.items[self.index], box_height, width)
        else:
            # Consoles: o espaço entre eles é sempre o mesmo, seja qual for o selecionado
            # (o PS2, em pé e estreito, não pode puxar os vizinhos para perto).
            base_width, base_height = max(
                (self._item_size(item, box_height, width) for item in self.items), key=lambda size: size[0])
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

        entries = []
        count = len(self.items)
        for i in sorted(self._drawn_slots(), key=lambda i: -abs(i - self.position)):
            item = self.items[i % count]  # na roda sem fim, a mesma caixa se repete a cada volta
            offset = i - self.position
            x, z, angle = self._place(offset, base_width, spine_step)
            item_width, item_height = self._item_size(item, box_height, width)
            entries.append((item, x, z - rise, angle, item_width, item_height, offset))

        native = self.get_native()
        surface = native.get_surface() if native is not None else None
        self.scale = surface.get_scale() if surface is not None and hasattr(surface, "get_scale") else self.get_scale_factor()
        self.moving = bool(self.tick_id) or self.dragging
        key = (width, height, self.position, self.reveal, self.reflections, self.scale, self.use_matrix,
               tuple((id(entry[0]), entry[0].state, entry[0].token, entry[0].shown_aspect) for entry in entries))
        if not self.moving and key == self.cache_key and self.cache_node is not None:
            snapshot.append_node(self.cache_node)
        else:
            boxes = Gtk.Snapshot()
            if self.reflections:
                for entry in entries:
                    self._draw_item(boxes, entry, floor, True)
            hidden = {} if self.use_matrix or self.loop else self._hidden_parts(entries, floor)
            for entry in entries:
                self._draw_item(boxes, entry, floor, False, hidden.get(id(entry[0])))
            node = boxes.to_node()
            if node is not None:
                snapshot.append_node(node)
            self.cache_key, self.cache_node = (None, None) if self.moving else (key, node)
        self._draw_caption(snapshot, width, height)
        if self.badge is not None:
            self._draw_badge(snapshot, width, height)

    # ---- Console no canto ------------------------------------------------------
    def dock_badge(self, item):
        """Entrou num console: a imagem dele encolhe e desliza até o canto de baixo, à esquerda."""
        start = self._front_rect(item) if self.items and item.front is not None else None
        if start is None:
            self.badge = None
            return
        self.badge = (item.front, item.glow, start)
        self.badge_progress = 0.0
        self.badge_started = 0
        self._animate()

    def clear_badge(self):
        """Tira o console do canto (voltou para o Início)."""
        self.badge = None
        self.badge_rect = None
        self._update_hover(-1, -1)
        self.queue_draw()

    def _front_rect(self, item):
        """Retângulo da frente da caixa selecionada na tela, como está desenhada agora."""
        width, height, box_height, base_width, spine_step = self._layout_base()
        if width <= 0 or height <= 0:
            return None
        self.vx, self.vy, self.width = width / 2, height * HORIZON, width
        floor = height * (FLOOR - HORIZON)
        offset = self.index - self.position
        x, z, angle = self._place(offset, base_width, spine_step)
        item_width, item_height = self._item_size(item, box_height, width)
        entry = (item, x, z - (1 - self.reveal) ** 2 * 500, angle, item_width, item_height, offset)
        for a, b, kind in self._faces(entry):
            face = self._screen_face(a, b, floor - item_height, floor) if kind == "front" else None
            if face is not None:
                _a, _b, left, right, top_a, bottom_a, top_b, bottom_b = face
                top, bottom = min(top_a, top_b), max(bottom_a, bottom_b)
                return left, top, right - left, bottom - top
        return None

    def _draw_badge(self, snapshot, width, height):
        """Desenha o console a caminho do canto (encolhendo, descendo e indo para a esquerda) ou já lá."""
        front, glow, (sx, sy, sw, sh) = self.badge
        aspect = front.get_width() / max(1, front.get_height())
        eh = height * BADGE_HEIGHT
        ew = eh * aspect
        if ew > width * BADGE_WIDTH:
            ew = width * BADGE_WIDTH
            eh = ew / aspect
        ex, ey = BADGE_MARGIN, height - eh - BADGE_MARGIN / 2
        t = self.badge_progress
        shrink = 1 - (1 - t) ** 3  # encolhe e desce rápido no começo...
        glide = t * t * (3 - 2 * t)  # ...e vai para o lado um pouco depois: caminho em curva
        w, h = sw + (ew - sw) * shrink, sh + (eh - sh) * shrink
        cx = sx + sw / 2 + (ex + ew / 2 - sx - sw / 2) * glide
        cy = sy + sh / 2 + (ey + eh / 2 - sy - sh / 2) * shrink
        rect = _rect(cx - w / 2, cy - h / 2, w, h)
        self.badge_rect = (cx - w / 2, cy - h / 2, w, h)
        snapshot.append_scaled_texture(front, FILTER, rect)
        if glow is not None:
            snapshot.push_opacity(min(1.0, 1.0 - 0.45 * shrink + (0.3 if self.badge_hover else 0)))
            snapshot.append_scaled_texture(glow, FILTER, rect)
            snapshot.pop()

    def _in_badge(self, x, y):
        """O ponto (x, y) está em cima do console do canto?"""
        if self.badge is None or self.badge_rect is None:
            return False
        left, top, w, h = self.badge_rect
        return left <= x <= left + w and top <= y <= top + h

    def _update_hover(self, x, y):
        """Mãozinha e brilho mais forte quando o mouse passa sobre o console do canto."""
        hover = self.on_badge is not None and self._in_badge(x, y)
        if hover != self.badge_hover and not self.dragging:
            self.badge_hover = hover
            self.set_cursor_from_name("pointer" if hover else None)
            self.queue_draw()

    def _drawn_slots(self):
        """Lugares da prateleira desenhados agora (na roda sem fim podem passar de 0..n-1)."""
        if self.loop:
            reach = self._loop_reach()
            return range(math.floor(self.position) - reach, math.ceil(self.position) + reach + 1)
        first = max(0, math.floor(self.position) - self.visible)
        last = min(len(self.items) - 1, math.ceil(self.position) + self.visible)
        return range(first, last + 1)

    def _loop_reach(self):
        """Roda sem fim: a partir de quantos lugares de distância a caixa já está toda fora da tela.

        Só essas poucas são desenhadas (5 a 9 na prática), não importa quantas voltas a roda deu.
        """
        width, _height, _box_height, base_width, spine_step = self._layout_base()
        half = width * 0.34 / 2  # nenhuma caixa fica mais larga que 34% da tela (_item_size)
        for reach in range(1, self.visible + 1):
            x, z, _angle = self._place(reach, base_width, spine_step)
            # Borda mais próxima do centro, no pior caso (girada e mais ao fundo): já passou da tela?
            if (x - half) * PERSPECTIVE / (PERSPECTIVE - z + half) > width / 2:
                return reach
        return self.visible

    def _faces(self, entry):
        """Faces da caixa viradas para quem olha: [(a, b, tipo)], da mais distante para a mais próxima."""
        item, x, z, angle, box_width, box_height, _offset = entry
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
        return [(a, b, kind) for _cz, a, b, kind in visible]

    def _screen_face(self, a, b, y_top, y_bottom):
        """Face projetada na tela, da esquerda para a direita, ou None se não aparece.

        Devolve (a, b, x_a, x_b, topo_a, base_a, topo_b, base_b): as bordas de
        cima e de baixo são retas entre a e b.
        """
        vx, vy, p = self.vx, self.vy, PERSPECTIVE
        (ax, az), (bx, bz) = a, b
        screen_a, screen_b = vx + ax * p / (p - az), vx + bx * p / (p - bz)
        if screen_b < screen_a:  # a textura sempre vai da esquerda para a direita
            a, b = b, a
            (ax, az), (bx, bz) = a, b
            screen_a, screen_b = screen_b, screen_a
        if screen_b - screen_a < 0.5 or screen_b < -4 or screen_a > self.width + 4:  # fora da tela
            return None
        wa, wb = p - az, p - bz
        return (a, b, screen_a, screen_b,
                vy + p * y_top / wa, vy + p * y_bottom / wa, vy + p * y_top / wb, vy + p * y_bottom / wb)

    def _hidden_parts(self, entries, floor):
        """Trechos (x na tela) de cada face totalmente cobertos pela caixa vizinha, mais à frente.

        Só caixas opacas e já carregadas escondem algo, e só quando cobrem a face
        inteira na vertical. Esses trechos não são desenhados: nas pilhas de
        lombadas, quase toda a frente das caixas fica escondida.
        """
        drawn_after = {id(entry[0]): order for order, entry in enumerate(entries)}
        by_index = {self.positions.get(id(entry[0])): entry for entry in entries}
        hidden = {}
        for order, entry in enumerate(entries):
            item = entry[0]
            index = self.positions.get(id(item))
            if index is None or not item.boxed:
                continue
            covers = []
            for near_index in (index - 1, index + 1):
                near = by_index.get(near_index)
                if near is None or drawn_after[id(near[0])] <= order or not near[0].opaque:
                    continue
                for a, b, _kind in self._faces(near):
                    face = self._screen_face(a, b, floor - near[5], floor)
                    if face is not None:
                        covers.append(face)
            if not covers:
                continue
            parts = {}
            for a, b, kind in self._faces(entry):
                face = self._screen_face(a, b, floor - entry[5], floor)
                if face is None:
                    continue
                spans = [span for cover in covers if (span := _covered(face, cover)) is not None]
                if spans:
                    parts[kind] = spans
            if parts:
                hidden[id(item)] = parts
        return hidden

    def _draw_item(self, snapshot, entry, floor, reflected, hidden=None):
        """Desenha uma caixa: frente e lombada (só as faces viradas para a tela) e o contorno."""
        item, _x, _z, _angle, _box_width, box_height, offset = entry
        dim = 0.32 * min(1.0, abs(offset) / 2.0) if item.boxed else 0
        for a, b, kind in self._faces(entry):
            if reflected:
                texture = item.front_reflection if kind == "front" else item.spine_reflection
                if texture is not None:
                    error = REFLECTION_ERROR if abs(offset) < 1.5 else SIDE_REFLECTION_ERROR
                    self._quad(snapshot, texture, a, b, floor, floor + box_height * boxart.REFLECTION, None, 0,
                               error=error)
            else:
                texture = item.front if kind == "front" else item.spine
                face_dim = dim if kind == "front" else 0
                skip = hidden.get(kind, ()) if hidden else ()
                self._quad(snapshot, texture, a, b, floor - box_height, floor, item.placeholder, face_dim, skip)
                # Contorno brilhante do item selecionado, sumindo conforme ele sai do centro.
                glow = 1 - abs(offset) * 1.6
                if kind == "front" and item.glow is not None and glow > 0.01:
                    snapshot.push_opacity(min(1.0, glow))
                    self._quad(snapshot, item.glow, a, b, floor - box_height, floor, None, 0)
                    snapshot.pop()

    def _quad(self, snapshot, texture, a, b, y_top, y_bottom, placeholder, dim, hidden=(), error=STRIP_ERROR):
        """Desenha uma face vertical em perspectiva com retângulos comuns.

        Face de frente para a tela: um retângulo só (exato). Face girada: faixas
        verticais estreitas, cada uma um retângulo reto recortado da mesma
        imagem, na altura e posição exatas da perspectiva no meio da faixa. A
        largura das faixas sai da inclinação das bordas, para nenhum ponto ficar
        a mais de `error` px do lugar certo. As divisas caem em pixels inteiros
        da tela: sem frestas nem sobreposição. A imagem é a mesma em todas as
        faixas (só recortada), então a filtragem é contínua de uma para outra.
        `hidden` = trechos de x cobertos pela caixa da frente (não desenhados).
        """
        if texture is None and placeholder is None:
            return
        if self.use_matrix:
            self._quad_matrix(snapshot, texture, a, b, y_top, y_bottom, placeholder, dim)
            return
        face = self._screen_face(a, b, y_top, y_bottom)
        if face is None:
            return
        if self.moving:
            error *= MOTION_ERROR
        (ax, az), (bx, bz), screen_a, screen_b, top_a, bottom_a, top_b, bottom_b = face
        width = screen_b - screen_a
        height_a, height_b = bottom_a - top_a, bottom_b - top_b
        middle_a = (top_a + bottom_a) / 2
        slope = ((top_b + bottom_b) / 2 - middle_a) / width  # da linha do meio
        grow = (height_b - height_a) / width  # da altura
        # Erro no canto de uma faixa de largura w: (|slope| + |grow|/2) · w/2.
        count = math.ceil(width * (abs(slope) + abs(grow) / 2) / (2 * error))
        count = max(1, min(count, MAX_STRIPS, int(width)))
        shade = _rgba(0, 0, 0, dim) if dim > 0.02 else None

        if count == 1:
            if hidden and any(left <= screen_a and screen_b <= right for left, right in hidden):
                return
            height = (height_a + height_b) / 2
            rect = Graphene.Rect().init(screen_a, middle_a + slope * width / 2 - height / 2, width, height)
            if texture is not None:
                snapshot.append_scaled_texture(texture, FILTER, rect)
            else:
                snapshot.append_color(placeholder, rect)
            if shade is not None:
                snapshot.append_color(shade, rect)
            return

        vx, p, scale = self.vx, PERSPECTIVE, self.scale
        wa, dx, dz = p - az, bx - ax, bz - az
        rect = Graphene.Rect
        push_clip, pop = snapshot.push_clip, snapshot.pop
        x1, s1 = screen_a, 0.0
        for i in range(1, count + 1):
            x0, s0 = x1, s1
            if i == count:
                x1, s1 = screen_b, 1.0
            else:
                x1 = round((screen_a + width * i / count) * scale) / scale  # divisa num pixel (físico) inteiro
                if x1 <= x0:
                    continue
                rx = x1 - vx
                s1 = (rx * wa - p * ax) / (p * dx + rx * dz)  # ponto da imagem nesse x (perspectiva exata)
            if hidden and any(left <= x0 and x1 <= right for left, right in hidden):
                continue
            center = (x0 + x1) / 2 - screen_a
            height = height_a + grow * center
            top = middle_a + slope * center - height / 2
            strip = x1 - x0
            if texture is not None:
                full = strip / max(s1 - s0, 1e-9)  # largura da imagem inteira nesta faixa
                push_clip(rect().init(x0, top - 2, strip, height + 4))
                snapshot.append_scaled_texture(texture, FILTER, rect().init(x0 - s0 * full, top, full, height))
                pop()
                if shade is not None:
                    snapshot.append_color(shade, rect().init(x0, top, strip, height))
            else:
                band = rect().init(x0, top, strip, height)
                snapshot.append_color(placeholder, band)
                if shade is not None:
                    snapshot.append_color(shade, band)

    def _quad_matrix(self, snapshot, texture, a, b, y_top, y_bottom, placeholder, dim):
        """Desenha a face como uma imagem só, com uma matriz de projeção (homografia).

        Exato e o mais leve, mas só é usado onde _test_matrix confirmou que o
        renderizador do GTK desenha matrizes 3D certo.
        """
        face = self._screen_face(a, b, y_top, y_bottom)
        if face is None:
            return
        (ax, az), (bx, bz) = face[0], face[1]
        vx, vy, p = self.vx, self.vy, PERSPECTIVE
        dx, dz, dy = (bx - ax) / UNIT, (bz - az) / UNIT, (y_bottom - y_top) / UNIT
        w0 = p - az
        # Ponto (s, t) da textura -> tela: x = vx + p·(ax + s·dx) / (w0 − s·dz), y = vy + p·(y_top + t·dy) / (w0 − s·dz)
        self.matrix.init_from_float([
            p * dx - vx * dz, -vy * dz, 0, -dz,
            0, p * dy, 0, 0,
            0, 0, 1, 0,
            vx * w0 + p * ax, vy * w0 + p * y_top, 0, w0,
        ])
        snapshot.save()
        snapshot.transform_matrix(self.matrix)
        if texture is not None:
            snapshot.append_scaled_texture(texture, FILTER, self.unit)
        else:
            snapshot.append_color(placeholder, self.unit)
        if dim > 0.02:
            snapshot.append_color(_rgba(0, 0, 0, dim), self.unit)
        snapshot.restore()

    def _test_matrix(self):
        """Desenha um xadrez colorido em perspectiva com a matriz e confere os pixels.

        Se o renderizador deste computador desenhar certo, a prateleira usa a
        matriz (uma imagem por face, bem leve). Senão, continua com as faixas.
        """
        if self.matrix_tested:
            return GLib.SOURCE_REMOVE
        native = self.get_native()
        renderer = native.get_renderer() if native is not None else None
        if renderer is None or not renderer.is_realized():
            return GLib.SOURCE_REMOVE  # tenta de novo no próximo "map"
        self.matrix_tested = True
        try:
            self.use_matrix = _matrix_works(self, renderer)
        except Exception:  # noqa: BLE001 - na dúvida, faixas (funcionam em todo lugar)
            self.use_matrix = False
        self.cache_key = None
        self.queue_draw()
        return GLib.SOURCE_REMOVE

    def _draw_caption(self, snapshot, width, height):
        """Escreve o nome e as informações da caixa selecionada embaixo dela."""
        nearest = round(self.position)
        if not self.loop:
            nearest = min(len(self.items) - 1, max(0, nearest))
        item = self.items[nearest % len(self.items)]
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
        """Lugar da caixa mais próxima de uma posição horizontal da tela (fora de 0..n-1 na roda sem fim)."""
        _w, _h, _bh, base_width, spine_step = self._layout_base()
        slots = self._drawn_slots() if self.loop else range(len(self.items))
        return min(slots, key=lambda i: abs(self._screen_x(i - self.position, base_width, spine_step) - x))

    def _on_click(self, _gesture, presses, x, y):
        """Um clique seleciona a caixa; dois cliques em cima dela abrem. O console do canto volta ao Início."""
        self.grab_focus()
        if not self.items:
            return
        if presses == 1 and self.on_badge is not None and self._in_badge(x, y):
            self.ignore_double = True
            self.on_badge()
            return
        if presses == 2 and self.ignore_double:
            return
        self.ignore_double = False
        if presses == 1:
            self.dragged = False
            best = self._item_at(x)
            if best != self.index:
                self.move(best - self.index)  # lugar exato clicado (na roda, pode ser de outra volta)
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
        # Nas pontas a prateleira "estica" um pouco, mas não passa do limite (a roda sem fim não tem pontas).
        if self.loop:
            pass
        elif position < 0:
            position = -0.35 * (1 - math.exp(position / 0.35))
        elif position > last:
            position = last + 0.35 * (1 - math.exp(-(position - last) / 0.35))
        self.position = position
        now = GLib.get_monotonic_time()
        self.drag_samples = [s for s in self.drag_samples if now - s[0] < 100_000] + [(now, position)]
        if self.loop:
            nearest = self._rewind(round(position))  # passou de uma volta: reinicia a contagem
        else:
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
        self.badge_hover = False
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
        if not self.loop:
            target = max(0, min(target, len(self.items) - 1))
        self.move(target - self.index)
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
