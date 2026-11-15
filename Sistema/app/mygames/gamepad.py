"""Navegação no hub com controle (joystick).

Linux: lê /dev/input/js* direto do sistema (classe Gamepads).
Windows: lê os controles pela API XInput (classe XInputGamepads), a mesma
usada pelos jogos para controles de Xbox e compatíveis.

Não depende de bibliotecas extras. Direcional ou analógico esquerdo navegam,
botão A/X confirma, B/Círculo volta e LB/RB trocam de console.
"""
import array
import ctypes
import glob
import os
import struct
import sys

try:
    import fcntl  # só existe no Linux
except ImportError:
    fcntl = None

from gi.repository import GLib

EVENT = struct.Struct("IhBB")
BUTTON, AXIS, INIT = 0x01, 0x02, 0x80
CONFIRM_BUTTONS = {0}
BACK_BUTTONS = {1}
SETTINGS_BUTTONS = {7, 9}  # Start
PREVIOUS_TAB_BUTTONS, NEXT_TAB_BUTTONS = {4}, {5}  # LB / RB
THRESHOLD = 16000
REPEAT_DELAY, REPEAT_RATE = 380, 140
EXIT_HOLD_MS = 900
BTN_SELECT, BTN_START = 0x13A, 0x13B
BTN_MISC, KEY_MAX = 0x100, 0x2FF
# _IOR('j', 0x34, __u16[KEY_MAX - BTN_MISC + 1]): qual código evdev cada botão do js representa.
JSIOCGBTNMAP = (2 << 30) | ((KEY_MAX - BTN_MISC + 1) * 2 << 16) | (ord("j") << 8) | 0x34
JSIOCGBUTTONS = (2 << 30) | (1 << 16) | (ord("j") << 8) | 0x12


def _button_codes(fd):
    """Pergunta ao Linux qual código (A, B, Start...) cada botão do controle representa."""
    try:
        count = array.array("B", [0])
        fcntl.ioctl(fd, JSIOCGBUTTONS, count, True)
        codes = array.array("H", [0] * (KEY_MAX - BTN_MISC + 1))
        fcntl.ioctl(fd, JSIOCGBTNMAP, codes, True)
        return list(codes[: count[0]])
    except OSError:
        return []


class Gamepads:
    """Lê os controles para navegar no hub e para o atalho SELECT + START que fecha o jogo."""

    def __init__(
        self, on_move, on_confirm, on_back, on_menu, is_enabled, on_change=None, on_exit_combo=None, on_tab=None
    ):
        """Guarda as ações do hub e começa a procurar controles (de novo a cada 2 segundos)."""
        self.on_tab = on_tab
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
        """Há algum controle conectado?"""
        return bool(self.devices)

    def _scan(self):
        """Procura controles novos em /dev/input/js* e passa a escutá-los."""
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
        """Esquece um controle que foi desconectado."""
        fd, _source = self.devices.pop(path, (None, None))
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if self.on_change:
            self.on_change(self.connected)

    def _on_readable(self, fd, condition, path):
        """Lê os eventos que chegaram de um controle."""
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
        """Trata um evento: botão (confirmar, voltar, menu, LB/RB) ou movimento do direcional/analógico."""
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
            elif self.on_tab and number in PREVIOUS_TAB_BUTTONS | NEXT_TAB_BUTTONS:
                self.on_tab(-1 if number in PREVIOUS_TAB_BUTTONS else 1)
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
        """SELECT + START continuam apertados depois do tempo: fecha o jogo."""
        self.exit_source = 0
        if (path, BTN_SELECT) in self.pressed and (path, BTN_START) in self.pressed:
            self.on_exit_combo()
        return GLib.SOURCE_REMOVE

    def _direction(self):
        """Direção atual do direcional ou do analógico esquerdo (-1, 0 ou 1 em x e y)."""
        dx = dy = 0
        for (_path, number), value in self.axes.items():
            # Eixos 0/1: analógico esquerdo. Eixos 6/7: direcional (hat).
            if number in (0, 6) and abs(value) > THRESHOLD:
                dx = 1 if value > 0 else -1
            elif number in (1, 7) and abs(value) > THRESHOLD:
                dy = 1 if value > 0 else -1
        return dx, dy

    def _update_direction(self, enabled):
        """Move a seleção quando a direção muda e prepara a repetição ao segurar."""
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
        """Depois da pausa inicial, começa a repetir o movimento enquanto segura."""
        self.repeat_source = GLib.timeout_add(REPEAT_RATE, self._repeat)
        return GLib.SOURCE_REMOVE

    def _repeat(self):
        """Repete o movimento enquanto a direção continua apertada."""
        if self.held == (0, 0) or not self.is_enabled():
            self.repeat_source = 0
            return GLib.SOURCE_REMOVE
        self.on_move(*self.held)
        return GLib.SOURCE_CONTINUE


# ---------------------------------------------------------------------------
# Windows: XInput (controles de Xbox e compatíveis)
# ---------------------------------------------------------------------------
XI_UP, XI_DOWN, XI_LEFT, XI_RIGHT = 0x0001, 0x0002, 0x0004, 0x0008
XI_START, XI_BACK, XI_LB, XI_RB = 0x0010, 0x0020, 0x0100, 0x0200
XI_A, XI_B, XI_X, XI_Y = 0x1000, 0x2000, 0x4000, 0x8000
XI_NOT_CONNECTED = 1167
POLL_MS = 16


class _XInputGamepad(ctypes.Structure):
    """Estado dos botões, gatilhos e analógicos de um controle (estrutura do XInput)."""

    _fields_ = [
        ("wButtons", ctypes.c_ushort), ("bLeftTrigger", ctypes.c_ubyte), ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short), ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short), ("sThumbRY", ctypes.c_short),
    ]


class _XInputState(ctypes.Structure):
    """Leitura completa de um controle: número do pacote + estado."""

    _fields_ = [("dwPacketNumber", ctypes.c_ulong), ("Gamepad", _XInputGamepad)]


def _load_xinput():
    """Carrega a DLL do XInput que existir neste Windows (1.4, 1.3 ou 9.1.0)."""
    for name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            return ctypes.WinDLL(name)
        except OSError:
            continue
    return None


class XInputGamepads:
    """Mesmo comportamento da classe Gamepads, lendo os controles pelo XInput do Windows.

    O XInput não avisa quando algo muda: o estado é lido ~60 vezes por segundo
    nos controles conectados, e os 4 encaixes são verificados a cada 2 segundos.
    """

    def __init__(
        self, on_move, on_confirm, on_back, on_menu, is_enabled, on_change=None, on_exit_combo=None, on_tab=None
    ):
        """Guarda as ações do hub e começa a ler os controles."""
        self.on_move, self.on_confirm, self.on_back, self.on_menu = on_move, on_confirm, on_back, on_menu
        self.is_enabled, self.on_change, self.on_exit_combo, self.on_tab = is_enabled, on_change, on_exit_combo, on_tab
        self.dll = _load_xinput()
        self.slots = {}  # encaixe (0-3) -> botões apertados na última leitura
        self.held = (0, 0)
        self.repeat_source = 0
        self.exit_source = 0
        if self.dll is None:
            return
        self._scan()
        GLib.timeout_add_seconds(2, self._scan)
        GLib.timeout_add(POLL_MS, self._poll)

    @property
    def connected(self):
        """Há algum controle conectado?"""
        return bool(self.slots)

    def _read(self, slot):
        """Lê um encaixe; devolve o estado ou None se não houver controle nele."""
        state = _XInputState()
        if self.dll.XInputGetState(slot, ctypes.byref(state)) != 0:
            return None
        return state.Gamepad

    def _scan(self):
        """Procura controles conectados/desconectados nos 4 encaixes."""
        changed = False
        for slot in range(4):
            present = self._read(slot) is not None
            if present and slot not in self.slots:
                self.slots[slot] = 0
                changed = True
            elif not present and slot in self.slots:
                del self.slots[slot]
                changed = True
        if changed and self.on_change:
            self.on_change(self.connected)
        return GLib.SOURCE_CONTINUE

    def _poll(self):
        """Lê os controles conectados e transforma o que mudou em ações do hub."""
        enabled = self.is_enabled()
        direction = (0, 0)
        exit_combo = False
        for slot in list(self.slots):
            pad = self._read(slot)
            if pad is None:
                continue
            buttons, before = pad.wButtons, self.slots[slot]
            pressed = buttons & ~before
            self.slots[slot] = buttons
            exit_combo = exit_combo or (buttons & XI_BACK and buttons & XI_START)
            if enabled and pressed:
                if pressed & XI_A:
                    self.on_confirm()
                elif pressed & XI_B:
                    self.on_back()
                elif pressed & XI_START and not buttons & XI_BACK:
                    self.on_menu()
                elif self.on_tab and pressed & (XI_LB | XI_RB):
                    self.on_tab(-1 if pressed & XI_LB else 1)
            dx = (1 if buttons & XI_RIGHT else -1 if buttons & XI_LEFT else 0) or (
                1 if pad.sThumbLX > THRESHOLD else -1 if pad.sThumbLX < -THRESHOLD else 0
            )
            # No XInput o analógico para cima é positivo; no hub "cima" é -1.
            dy = (1 if buttons & XI_DOWN else -1 if buttons & XI_UP else 0) or (
                -1 if pad.sThumbLY > THRESHOLD else 1 if pad.sThumbLY < -THRESHOLD else 0
            )
            if (dx, dy) != (0, 0):
                direction = (dx, dy)
        self._update_exit_combo(bool(exit_combo))
        self._update_direction(direction, enabled)
        return GLib.SOURCE_CONTINUE

    def _update_exit_combo(self, held):
        """SELECT (Back) + START segurados juntos fecham o emulador e voltam ao hub."""
        if held and not self.exit_source and self.on_exit_combo:
            self.exit_source = GLib.timeout_add(EXIT_HOLD_MS, self._fire_exit_combo)
        elif not held and self.exit_source:
            GLib.source_remove(self.exit_source)
            self.exit_source = 0

    def _fire_exit_combo(self):
        """O combo continuou apertado pelo tempo necessário: fecha o jogo."""
        self.exit_source = 0
        self.on_exit_combo()
        return GLib.SOURCE_REMOVE

    def _update_direction(self, direction, enabled):
        """Move a seleção quando a direção muda e prepara a repetição ao segurar."""
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
        """Depois da pausa inicial, começa a repetir o movimento enquanto segura."""
        self.repeat_source = GLib.timeout_add(REPEAT_RATE, self._repeat)
        return GLib.SOURCE_REMOVE

    def _repeat(self):
        """Repete o movimento enquanto a direção continua apertada."""
        if self.held == (0, 0) or not self.is_enabled():
            self.repeat_source = 0
            return GLib.SOURCE_REMOVE
        self.on_move(*self.held)
        return GLib.SOURCE_CONTINUE


if sys.platform == "win32":
    Gamepads = XInputGamepads  # noqa: F811 - no Windows o hub usa o XInput
