"""Carrossel infinito de consoles (arrastar, roda do mouse, setas e controle)."""
from gi.repository import Gdk, GLib, Gtk

COPIES = 7
CENTER = COPIES // 2


class ConsoleCarousel(Gtk.ScrolledWindow):
    def __init__(self, consoles, on_selected, on_open):
        super().__init__()
        self.consoles = consoles
        self.on_selected = on_selected
        self.on_open = on_open
        self.selected_index = 0
        self.selected_copy = CENTER
        self.cards = []
        self.outlines = []
        self.wheel_locked = False
        self.drag_start = 0
        self.drag_moved = False
        self.anim_source = 0
        self.anim_from = self.anim_to = 0
        self.anim_started = 0
        self.center_source = 0

        self.add_css_class("carousel")
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_min_content_height(380)
        self.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        self.set_kinetic_scrolling(False)
        self.track = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.track.set_valign(Gtk.Align.CENTER)
        self.set_child(self.track)

        # Repetir os cartões mantém consoles vizinhos à vista mesmo nas pontas.
        for copy_index in range(COPIES):
            for console_index, console in enumerate(consoles):
                card = self._make_card(console, copy_index == CENTER)
                card.connect("clicked", self._on_card_clicked, console_index, copy_index)
                double = Gtk.GestureClick.new()
                double.set_button(Gdk.BUTTON_PRIMARY)
                double.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
                double.connect("pressed", self._on_card_pressed, console_index, copy_index)
                card.add_controller(double)
                self.track.append(card)
                self.cards.append(card)

        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll.connect("scroll", self._on_wheel)
        self.add_controller(scroll)

        drag = Gtk.GestureDrag.new()
        drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        drag.set_button(Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)
        self.set_cursor_from_name("grab")
        self.connect("notify::width", lambda *_: GLib.idle_add(self._center))

    # ---- Cartões -------------------------------------------------------------
    def _make_card(self, console, focusable):
        button = Gtk.Button()
        button.add_css_class("console-card")
        button.set_focusable(focusable)
        button.set_overflow(Gtk.Overflow.VISIBLE)
        button.set_tooltip_text(console.name)
        art = Gtk.Overlay()
        art.set_overflow(Gtk.Overflow.VISIBLE)
        art.set_size_request(270, 320)
        if console.art.is_file():
            picture = Gtk.Picture.new_for_filename(str(console.art))
            picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            picture.set_can_shrink(True)
            art.set_child(picture)
            # O halo branco acompanha a silhueta do console selecionado.
            outline = Gtk.Picture.new_for_filename(str(console.outline if console.outline.is_file() else console.art))
            outline.add_css_class("console-outline")
            outline.set_content_fit(Gtk.ContentFit.CONTAIN)
            outline.set_can_shrink(True)
            outline.set_can_target(False)
            outline.set_opacity(0)
            art.add_overlay(outline)
            self.outlines.append(outline)
        else:
            art.set_child(Gtk.Label(label=console.name))
            self.outlines.append(None)
        button.set_child(art)
        return button

    def _on_card_clicked(self, _button, console_index, copy_index):
        self.select(console_index, target_copy=copy_index)

    def _on_card_pressed(self, _gesture, presses, _x, _y, console_index, copy_index):
        if presses == 2:
            self.select(console_index, target_copy=copy_index)
            self.on_open(console_index)

    # ---- Seleção -------------------------------------------------------------
    def move(self, step):
        self.select(self.selected_index + step)

    def select(self, index, target_copy=None):
        copy_offset, console_index = divmod(index, len(self.consoles))
        target_copy = self.selected_copy + copy_offset if target_copy is None else target_copy
        target_copy = self._recenter_copy(target_copy)
        self._stop_animation()
        if self.center_source:
            GLib.source_remove(self.center_source)
        self._mark_selected(console_index, target_copy)
        self.center_source = GLib.timeout_add(40, self._center_later)

    def _mark_selected(self, index, copy_index):
        changed = index % len(self.consoles) != self.selected_index
        self.selected_index = index % len(self.consoles)
        self.selected_copy = copy_index
        selected = copy_index * len(self.consoles) + self.selected_index
        for card_index, card in enumerate(self.cards):
            is_selected = card_index == selected
            (card.add_css_class if is_selected else card.remove_css_class)("selected")
            outline = self.outlines[card_index]
            if outline is not None:
                outline.set_opacity(1 if is_selected else 0)
        if changed:
            self.on_selected(self.selected_index)

    def _center_later(self):
        self.center_source = 0
        return self._center(True)

    def _center(self, animate=False):
        if not self.cards:
            return GLib.SOURCE_REMOVE
        card = self.cards[self.selected_copy * len(self.consoles) + self.selected_index]
        ok, bounds = card.compute_bounds(self.track)
        if not ok:
            return GLib.SOURCE_REMOVE
        adjustment = self.get_hadjustment()
        target = bounds.get_x() + bounds.get_width() / 2 - adjustment.get_page_size() / 2
        maximum = max(adjustment.get_lower(), adjustment.get_upper() - adjustment.get_page_size())
        target = min(max(target, adjustment.get_lower()), maximum)
        if animate:
            self._animate_to(target)
        else:
            self._stop_animation()
            adjustment.set_value(target)
        return GLib.SOURCE_REMOVE

    def _cycle_width(self):
        per_copy = len(self.consoles)
        other = (self.selected_index + 1) % per_copy
        ok_a, a = self.cards[CENTER * per_copy + other].compute_bounds(self.track)
        ok_b, b = self.cards[(CENTER + 1) * per_copy + other].compute_bounds(self.track)
        return b.get_x() - a.get_x() if ok_a and ok_b else 0

    def _recenter_copy(self, copy_index):
        if 1 <= copy_index < COPIES - 1:
            return copy_index
        width = self._cycle_width()
        if width <= 0:
            return copy_index
        adjustment = self.get_hadjustment()
        adjustment.set_value(adjustment.get_value() + (CENTER - copy_index) * width)
        return CENTER

    def _nearest_to_center(self):
        adjustment = self.get_hadjustment()
        center = adjustment.get_value() + adjustment.get_page_size() / 2
        best, best_distance = None, float("inf")
        for card_index, card in enumerate(self.cards):
            ok, bounds = card.compute_bounds(self.track)
            if ok:
                distance = abs(bounds.get_x() + bounds.get_width() / 2 - center)
                if distance < best_distance:
                    best, best_distance = card_index, distance
        if best is None:
            return self.selected_index, self.selected_copy
        copy_index, console_index = divmod(best, len(self.consoles))
        return console_index, copy_index

    # ---- Animação ------------------------------------------------------------
    def _stop_animation(self):
        if self.anim_source:
            GLib.source_remove(self.anim_source)
            self.anim_source = 0

    def _animate_to(self, target):
        self._stop_animation()
        self.anim_from = self.get_hadjustment().get_value()
        self.anim_to = target
        self.anim_started = GLib.get_monotonic_time()
        self.anim_source = GLib.timeout_add(16, self._animation_step)

    def _animation_step(self):
        progress = min((GLib.get_monotonic_time() - self.anim_started) / 380_000, 1)
        eased = 1 - (1 - progress) ** 3
        self.get_hadjustment().set_value(self.anim_from + (self.anim_to - self.anim_from) * eased)
        if progress >= 1:
            self.anim_source = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    # ---- Entrada -------------------------------------------------------------
    def _on_wheel(self, _controller, dx, dy):
        delta = dx if abs(dx) > abs(dy) else dy
        if abs(delta) >= 0.05 and not self.wheel_locked:
            self.wheel_locked = True
            self.move(1 if delta > 0 else -1)
            GLib.timeout_add(240, self._unlock_wheel)
        return True

    def _unlock_wheel(self):
        self.wheel_locked = False
        return GLib.SOURCE_REMOVE

    def _on_drag_begin(self, _gesture, _x, _y):
        self._stop_animation()
        if self.center_source:
            GLib.source_remove(self.center_source)
            self.center_source = 0
        self.drag_start = self.get_hadjustment().get_value()
        self.drag_moved = False
        self.set_cursor_from_name("grabbing")

    def _on_drag_update(self, gesture, offset_x, _offset_y):
        if abs(offset_x) < 4:
            return
        if not self.drag_moved and not gesture.set_state(Gtk.EventSequenceState.CLAIMED):
            return
        self.drag_moved = True
        adjustment = self.get_hadjustment()
        value = self.drag_start - offset_x
        width = self._cycle_width()
        if width > 0:
            lower = adjustment.get_lower()
            maximum = max(lower, adjustment.get_upper() - adjustment.get_page_size())
            while value >= maximum - width:
                value -= width
                self.drag_start -= width
            while value <= lower + width:
                value += width
                self.drag_start += width
            value = min(max(value, lower), maximum)
        adjustment.set_value(value)
        self._mark_selected(*self._nearest_to_center())

    def _on_drag_end(self, _gesture, _x, _y):
        if self.drag_moved:
            index, copy_index = self._nearest_to_center()
            self.select(index, target_copy=copy_index)
        else:
            GLib.idle_add(self._center, True)
        self.drag_moved = False
        self.set_cursor_from_name("grab")
