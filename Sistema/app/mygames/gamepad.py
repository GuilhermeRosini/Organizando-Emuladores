"""Navegação no hub com controle (joystick) usando a API /dev/input/js* do Linux.

Não depende de bibliotecas extras. Direcional ou analógico esquerdo navegam,
botão A/X confirma e B/Círculo volta.
"""
import array
import fcntl
import glob
import os
import struct

from gi.repository import GLib

EVENT = struct.Struct("IhBB")
BUTTON, AXIS, INIT = 0x01, 0x02, 0x80
CONFIRM_BUTTONS = {0}
BACK_BUTTONS = {1}
SETTINGS_BUTTONS = {7, 9}  # Start
THRESHOLD = 16000
REPEAT_DELAY, REPEAT_RATE = 380, 140
EXIT_HOLD_MS = 900
BTN_SELECT, BTN_START = 0x13A, 0x13B
BTN_MISC, KEY_MAX = 0x100, 0x2FF
# _IOR('j', 0x34, __u16[KEY_MAX - BTN_MISC + 1]): qual código evdev cada botão do js representa.
JSIOCGBTNMAP = (2 << 30) | ((KEY_MAX - BTN_MISC + 1) * 2 << 16) | (ord("j") << 8) | 0x34
JSIOCGBUTTONS = (2 << 30) | (1 << 16) | (ord("j") << 8) | 0x12


def _button_codes(fd):
    try:
        count = array.array("B", [0])
        fcntl.ioctl(fd, JSIOCGBUTTONS, count, True)
        codes = array.array("H", [0] * (KEY_MAX - BTN_MISC + 1))
        fcntl.ioctl(fd, JSIOCGBTNMAP, codes, True)
        return list(codes[: count[0]])
    except OSError:
        return []


class Gamepads:
    def __init__(self, on_move, on_confirm, on_back, on_menu, is_enabled, on_change=None, on_exit_combo=None):
        self.on_move = on_move
        self.on_confirm = on_confirm
        self.on_back = on_back
        self.on_menu = on_menu
        self.is_enabled = is_enabled
        self.on_change = on_change
        self.on_exit_combo = on_exit_combo
        self.button_codes = {}
        self.pressed = set()
        self.exit_source = 0
        self.devices = {}
        self.axes = {}
        self.repeat_source = 0
        self.held = (0, 0)
        self._scan()
        GLib.timeout_add_seconds(2, self._scan)

    @property
    def connected(self):
        return bool(self.devices)

    def _scan(self):
        changed = False
        for path in glob.glob("/dev/input/js*"):
            if path in self.devices:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            source = GLib.io_add_watch(fd, GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self._on_readable, path)
            self.devices[path] = (fd, source)
            self.button_codes[path] = _button_codes(fd)
            changed = True
        if changed and self.on_change:
            self.on_change(self.connected)
        return GLib.SOURCE_CONTINUE

    def _drop(self, path):
        fd, _source = self.devices.pop(path, (None, None))
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if self.on_change:
            self.on_change(self.connected)

    def _on_readable(self, fd, condition, path):
        if condition & (GLib.IO_HUP | GLib.IO_ERR):
            self._drop(path)
            return GLib.SOURCE_REMOVE
        try:
            while data := os.read(fd, EVENT.size * 16):
                for offset in range(0, len(data) - EVENT.size + 1, EVENT.size):
                    self._handle(path, *EVENT.unpack_from(data, offset))
        except BlockingIOError:
            pass
        except OSError:
            self._drop(path)
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _handle(self, path, _time, value, kind, number):
        if kind & INIT:
            return
        enabled = self.is_enabled()
        if kind & BUTTON:
            codes = self.button_codes.get(path) or []
            code = codes[number] if number < len(codes) else None
            (self.pressed.add if value else self.pressed.discard)((path, code))
            self._check_exit_combo(path)
        if kind & BUTTON and value == 1 and enabled:
            if number in CONFIRM_BUTTONS:
                self.on_confirm()
            elif number in BACK_BUTTONS:
                self.on_back()
            elif number in SETTINGS_BUTTONS:
                self.on_menu()
        elif kind & AXIS:
            self.axes[(path, number)] = value
            self._update_direction(enabled)

    def _check_exit_combo(self, path):
        """SELECT + START segurados juntos fecham o emulador e voltam ao hub."""
        held = (path, BTN_SELECT) in self.pressed and (path, BTN_START) in self.pressed
        if held and not self.exit_source and self.on_exit_combo:
            self.exit_source = GLib.timeout_add(EXIT_HOLD_MS, self._fire_exit_combo, path)
        elif not held and self.exit_source:
            GLib.source_remove(self.exit_source)
            self.exit_source = 0

    def _fire_exit_combo(self, path):
        self.exit_source = 0
        if (path, BTN_SELECT) in self.pressed and (path, BTN_START) in self.pressed:
            self.on_exit_combo()
        return GLib.SOURCE_REMOVE

    def _direction(self):
        dx = dy = 0
        for (_path, number), value in self.axes.items():
            # Eixos 0/1: analógico esquerdo. Eixos 6/7: direcional (hat).
            if number in (0, 6) and abs(value) > THRESHOLD:
                dx = 1 if value > 0 else -1
            elif number in (1, 7) and abs(value) > THRESHOLD:
                dy = 1 if value > 0 else -1
        return dx, dy

    def _update_direction(self, enabled):
        direction = self._direction()
        if direction == self.held:
            return
        self.held = direction
        if self.repeat_source:
            GLib.source_remove(self.repeat_source)
            self.repeat_source = 0
        if direction != (0, 0) and enabled:
            self.on_move(*direction)
            self.repeat_source = GLib.timeout_add(REPEAT_DELAY, self._repeat_start)

    def _repeat_start(self):
        self.repeat_source = GLib.timeout_add(REPEAT_RATE, self._repeat)
        return GLib.SOURCE_REMOVE

    def _repeat(self):
        if self.held == (0, 0) or not self.is_enabled():
            self.repeat_source = 0
            return GLib.SOURCE_REMOVE
        self.on_move(*self.held)
        return GLib.SOURCE_CONTINUE
