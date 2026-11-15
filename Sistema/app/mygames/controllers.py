"""Detecção de controles e configuração automática dos emuladores.

Lê o controle conectado direto do Linux (/sys/class/input), sem bibliotecas
extras, e escreve o mapeamento certo no formato de cada emulador antes de
abrir o jogo. Funciona com controles USB e Bluetooth (Xbox, PlayStation,
8BitDo, genéricos...).

Os formatos seguem o código-fonte de cada emulador:
- PCSX2: vínculos SDL por índice ("SDL-0/FaceSouth") — servem para qualquer controle.
- Snes9x e mGBA: SDL2 em modo joystick "cru" — o número de cada botão depende
  do modelo, por isso é calculado a partir dos códigos evdev do controle.
- Dolphin: backend evdev, que usa o nome do dispositivo e "Button N"/"Axis N±".
- RMG: modo "Automatic" com a API de gamepad do SDL3 (independe do modelo).
- MAME: tem mapeamento padrão para controles; só ligamos o joystick.
"""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

INPUT_CLASS = Path("/sys/class/input")

# Códigos evdev (linux/input-event-codes.h)
BTN_MISC, BTN_JOYSTICK = 0x100, 0x120
BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST = 0x130, 0x131, 0x133, 0x134
BTN_TL, BTN_TR, BTN_TL2, BTN_TR2 = 0x136, 0x137, 0x138, 0x139
BTN_SELECT, BTN_START, BTN_MODE, BTN_THUMBL, BTN_THUMBR = 0x13A, 0x13B, 0x13C, 0x13D, 0x13E
BTN_DPAD_UP, BTN_DPAD_DOWN, BTN_DPAD_LEFT, BTN_DPAD_RIGHT = 0x220, 0x221, 0x222, 0x223
ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ = 0x00, 0x01, 0x02, 0x03, 0x04, 0x05
ABS_GAS, ABS_BRAKE, ABS_HAT0X, ABS_HAT0Y = 0x09, 0x0A, 0x10, 0x11
HAT_CODES = range(0x10, 0x18)
ABS_MISC = 0x28


@dataclass
class Gamepad:
    """Um controle conectado: nome, botões/eixos que ele tem e onde fica cada botão físico."""
    name: str
    js: str
    keys: list
    axes: list
    vendor: str = ""
    driver: str = ""
    layout: dict = field(default_factory=dict)

    # ---- Ordem usada pelo SDL2 no Linux (Snes9x, mGBA) ----------------------
    def sdl_button(self, code):
        """Número do botão como o SDL2 enxerga no Linux (usado pelo Snes9x e mGBA)."""
        order = [c for c in self.keys if c >= BTN_JOYSTICK] + [c for c in self.keys if c < BTN_JOYSTICK]
        return order.index(code)

    def sdl_axis(self, code):
        """Número do eixo analógico como o SDL2 enxerga no Linux."""
        return [c for c in self.axes if c not in HAT_CODES].index(code)

    @property
    def sdl_axis_count(self):
        """Quantos eixos analógicos reais o controle tem (sem contar o direcional)."""
        return len([c for c in self.axes if c not in HAT_CODES])

    # ---- Ordem usada pelo backend evdev do Dolphin --------------------------
    def evdev_button(self, code):
        """Número do botão como o backend evdev do Dolphin enxerga."""
        return self.keys.index(code)

    def evdev_axis(self, code):
        """Número do eixo como o backend evdev do Dolphin enxerga."""
        return [c for c in self.axes if c < ABS_MISC].index(code)


def _bitmap(path):
    """Converte o bitmap de capacidades do sysfs numa lista de códigos."""
    try:
        words = Path(path).read_text().split()
    except OSError:
        return []
    codes = []
    for word_index, word in enumerate(reversed(words)):
        value = int(word, 16)
        bit = 0
        while value:
            if value & 1:
                codes.append(word_index * 64 + bit)
            value >>= 1
            bit += 1
    return sorted(codes)


def _read(path):
    """Lê um arquivo de texto do sistema (sysfs); devolve "" se não existir."""
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def _layout(keys, axes, xbox):
    """Descobre onde fica cada botão físico do controle."""
    key, has_axis = set(keys).__contains__, set(axes).__contains__
    layout = {}
    if key(BTN_SOUTH):
        # Controles Xbox (driver xpad/hid-microsoft) trocam BTN_NORTH e BTN_WEST.
        north, west = (BTN_WEST, BTN_NORTH) if xbox else (BTN_NORTH, BTN_WEST)
        layout.update(south=("b", BTN_SOUTH), east=("b", BTN_EAST), north=("b", north), west=("b", west))
        for name, code in (("l1", BTN_TL), ("r1", BTN_TR), ("select", BTN_SELECT), ("start", BTN_START),
                           ("guide", BTN_MODE), ("l3", BTN_THUMBL), ("r3", BTN_THUMBR)):
            if key(code):
                layout[name] = ("b", code)
    else:
        # Controle genérico: segue a ordem mais comum dos botões.
        generic = [c for c in keys if c >= BTN_MISC]
        for name, index in (("north", 0), ("east", 1), ("south", 2), ("west", 3), ("l1", 4), ("r1", 5),
                            ("l2", 6), ("r2", 7), ("select", 8), ("start", 9), ("l3", 10), ("r3", 11)):
            if index < len(generic):
                layout[name] = ("b", generic[index])

    # Gatilhos: botão digital ou eixo analógico.
    right_stick = (ABS_RX, ABS_RY) if has_axis(ABS_RX) else (ABS_Z, ABS_RZ)
    triggers = (ABS_Z, ABS_RZ) if has_axis(ABS_RX) else (ABS_BRAKE, ABS_GAS)
    for name, button, axis in (("l2", BTN_TL2, triggers[0]), ("r2", BTN_TR2, triggers[1])):
        if has_axis(axis):
            layout[name] = ("a", axis, "+")
        elif key(button):
            layout[name] = ("b", button)

    if has_axis(ABS_X) and has_axis(ABS_Y):
        layout.update(lx=ABS_X, ly=ABS_Y)
    if all(has_axis(c) for c in right_stick):
        layout.update(rx=right_stick[0], ry=right_stick[1])

    if has_axis(ABS_HAT0X) and has_axis(ABS_HAT0Y):
        layout.update(up=("h", ABS_HAT0Y, "-"), down=("h", ABS_HAT0Y, "+"),
                      left=("h", ABS_HAT0X, "-"), right=("h", ABS_HAT0X, "+"))
    elif key(BTN_DPAD_UP):
        layout.update(up=("b", BTN_DPAD_UP), down=("b", BTN_DPAD_DOWN),
                      left=("b", BTN_DPAD_LEFT), right=("b", BTN_DPAD_RIGHT))
    elif "lx" in layout:
        layout.update(up=("a", ABS_Y, "-"), down=("a", ABS_Y, "+"),
                      left=("a", ABS_X, "-"), right=("a", ABS_X, "+"))
    return layout


def gamepads():
    """Lista os controles conectados, na ordem /dev/input/js0, js1..."""
    found = []
    for js in sorted(INPUT_CLASS.glob("js*"), key=lambda p: int(re.sub(r"\D", "", p.name) or 0)):
        device = (js / "device").resolve()
        name = _read(device / "name")
        keys = _bitmap(device / "capabilities" / "key")
        axes = _bitmap(device / "capabilities" / "abs")
        vendor = _read(device / "id" / "vendor")
        try:
            driver = Path(os.readlink(device / "device" / "driver")).name
        except OSError:
            driver = ""
        xbox = driver in ("xpad", "microsoft") or vendor == "045e" or "xbox" in name.casefold()
        pad = Gamepad(name, f"/dev/input/{js.name}", keys, axes, vendor, driver)
        pad.layout = _layout(keys, axes, xbox)
        if pad.layout:
            found.append(pad)
    return found


# ---------------------------------------------------------------------------
# Ajudantes de INI
# ---------------------------------------------------------------------------
def _set_ini_section(path, section, values, separator=" = ", replace=True):
    """Troca (ou cria) uma seção inteira de um arquivo INI preservando o resto."""
    path = Path(path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
    header = f"[{section}]"
    body = [f"{key}{separator}{value}" for key, value in values]
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == header)
    except StopIteration:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [header] + body + [""]
    else:
        if not replace:
            return False
        end = start + 1
        while end < len(lines) and not lines[end].strip().startswith("["):
            end += 1
        lines[start + 1:end] = body + [""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# PCSX2 — vínculos SDL independentes do modelo (feito uma vez)
# ---------------------------------------------------------------------------
PCSX2_PAD = [
    ("Up", "DPadUp", "Up"), ("Right", "DPadRight", "Right"), ("Down", "DPadDown", "Down"),
    ("Left", "DPadLeft", "Left"), ("Triangle", "FaceNorth", "I"), ("Circle", "FaceEast", "L"),
    ("Cross", "FaceSouth", "K"), ("Square", "FaceWest", "J"), ("Select", "Back", "Backspace"),
    ("Start", "Start", "Return"), ("L1", "LeftShoulder", "Q"), ("L2", "+LeftTrigger", "1"),
    ("R1", "RightShoulder", "E"), ("R2", "+RightTrigger", "3"), ("L3", "LeftStick", "2"),
    ("R3", "RightStick", "4"), ("LUp", "-LeftY", "W"), ("LRight", "+LeftX", "D"),
    ("LDown", "+LeftY", "S"), ("LLeft", "-LeftX", "A"), ("RUp", "-RightY", "T"),
    ("RRight", "+RightX", "H"), ("RDown", "+RightY", "G"), ("RLeft", "-RightX", "F"),
]


def configure_pcsx2(ini):
    """Liga o controle 1 do PS2 ao primeiro controle SDL, mantendo o teclado junto."""
    text = ini.read_text(encoding="utf-8", errors="replace") if ini.exists() else ""
    if "SDL-0/FaceSouth" in text:
        return
    values = [("Type", "DualShock2"), ("InvertL", "0"), ("InvertR", "0"), ("Deadzone", "0"),
              ("AxisScale", "1.33"), ("LargeMotorScale", "1"), ("SmallMotorScale", "1"),
              ("ButtonDeadzone", "0"), ("PressureModifier", "0.5")]
    for key, sdl, keyboard in PCSX2_PAD:
        # O PCSX2 aceita várias linhas com a mesma chave: teclado E controle funcionam.
        values += [(key, f"Keyboard/{keyboard}"), (key, f"SDL-0/{sdl}")]
    values += [("Analog", "SDL-0/Guide"), ("LargeMotor", "SDL-0/LargeMotor"), ("SmallMotor", "SDL-0/SmallMotor")]
    _set_ini_section(ini, "Pad1", values)
    sources = [("Keyboard", "true"), ("Mouse", "true"), ("SDL", "true"),
               ("SDLControllerEnhancedMode", "true"), ("SDLPS5PlayerLED", "true")]
    _set_ini_section(ini, "InputSources", sources)


# ---------------------------------------------------------------------------
# Snes9x — SDL2 joystick cru, recalculado a cada abertura
# ---------------------------------------------------------------------------
def _snes9x_binding(pad, entry):
    """Converte um botão/eixo do controle no texto que o Snes9x usa no snes9x.conf."""
    if entry is None:
        return "Unset"
    kind = entry[0]
    if kind == "b":
        return f"Joystick 1 Button {pad.sdl_button(entry[1])}"
    if kind == "a":
        return f"Joystick 1 Axis {pad.sdl_axis(entry[1])} {entry[2]} 50"
    # Direcional (hat): o Snes9x cria eixos virtuais depois dos eixos reais.
    # Cima = eixo n positivo, baixo = n negativo, esquerda = n+1 negativo, direita = n+1 positivo.
    base = pad.sdl_axis_count
    axis, sign = {"up": (base, "+"), "down": (base, "-"), "left": (base + 1, "-"), "right": (base + 1, "+")}[entry[3]]
    return f"Joystick 1 Axis {axis} {sign} 50"


def configure_snes9x(conf, pad):
    """Escreve o mapeamento do controle no Snes9x (botões na mesma posição do controle do SNES)."""
    layout = dict(pad.layout)
    for direction in ("up", "down", "left", "right"):
        if direction in layout and layout[direction][0] == "h":
            layout[direction] = layout[direction] + (direction,)
    # Posição igual ao controle do Super Nintendo: A à direita, B embaixo.
    mapping = {"A": "east", "B": "south", "X": "north", "Y": "west", "L": "l1", "R": "r1",
               "Select": "select", "Start": "start", "Up": "up", "Down": "down", "Left": "left", "Right": "right"}
    keys = ["A", "B", "Down", "L", "Left", "R", "Right", "Select", "Start", "Sticky A", "Sticky B", "Sticky L",
            "Sticky R", "Sticky X", "Sticky Y", "Turbo A", "Turbo B", "Turbo L", "Turbo R", "Turbo X", "Turbo Y",
            "Up", "X", "Y"]
    values = [(key, _snes9x_binding(pad, layout.get(mapping[key])) if key in mapping else "Unset") for key in keys]
    _set_ini_section(conf, "Joypad 0", values)


def configure_snes9x_keyboard(conf):
    """Sem controle: volta para o teclado (setas, Z/X/A/S, Q/W, Enter, Espaço)."""
    keyboard = {"A": "x", "B": "z", "X": "s", "Y": "a", "L": "q", "R": "w", "Select": "space",
                "Start": "Return", "Up": "Up", "Down": "Down", "Left": "Left", "Right": "Right"}
    if not conf.exists() or "Joystick 1" not in conf.read_text(encoding="utf-8", errors="replace"):
        return
    keys = ["A", "B", "Down", "L", "Left", "R", "Right", "Select", "Start", "Up", "X", "Y"]
    _set_ini_section(conf, "Joypad 0", [(key, f"Keyboard {keyboard[key]}") for key in keys])


# ---------------------------------------------------------------------------
# mGBA — SDL2 joystick cru, recalculado a cada abertura
# ---------------------------------------------------------------------------
def configure_mgba(config_ini, pad):
    """Escreve o mapeamento do controle no mGBA (botões na mesma posição do Game Boy Advance)."""
    layout = pad.layout
    values = {}

    def button(name):
        """Número SDL do botão físico pedido, ou -1 se o controle não tiver."""
        entry = layout.get(name)
        return pad.sdl_button(entry[1]) if entry and entry[0] == "b" else -1

    # Teclas do GBA: A à direita e B embaixo, como no portátil.
    for key, name in (("keyA", "east"), ("keyB", "south"), ("keyL", "l1"), ("keyR", "r1"),
                      ("keySelect", "select"), ("keyStart", "start")):
        values[key] = button(name)
    for key, name in (("keyUp", "up"), ("keyDown", "down"), ("keyLeft", "left"), ("keyRight", "right")):
        values[key] = button(name)
    # Direcional como hat (números = teclas internas do mGBA: Right=4, Left=5, Up=6, Down=7).
    values.update(hat0Up=6, hat0Down=7, hat0Left=5, hat0Right=4)
    if "lx" in layout:
        x, y = pad.sdl_axis(layout["lx"]), pad.sdl_axis(layout["ly"])
        values.update(axisLeftAxis=f"-{x}", axisRightAxis=f"+{x}", axisUpAxis=f"-{y}", axisDownAxis=f"+{y}",
                      axisLeftValue=-16384, axisRightValue=16384, axisUpValue=-16384, axisDownValue=16384)
    _set_ini_section(config_ini, "gba.input.SDLB", list(values.items()), separator="=")


# ---------------------------------------------------------------------------
# Dolphin — backend evdev, recalculado a cada abertura
# ---------------------------------------------------------------------------
def _dolphin_input(pad, entry):
    """Converte um botão/eixo do controle no texto que o Dolphin usa (ex.: `Button 3`)."""
    if entry is None:
        return ""
    if entry[0] == "b":
        return f"`Button {pad.evdev_button(entry[1])}`"
    return f"`Axis {pad.evdev_axis(entry[1])}{entry[2]}`"


def configure_dolphin(config_dir, pad):
    """Escreve o mapeamento do controle no Dolphin (controle de GameCube na porta 1)."""
    layout = pad.layout
    get = lambda name: _dolphin_input(pad, layout.get(name))
    stick = lambda code, sign: f"`Axis {pad.evdev_axis(code)}{sign}`" if code is not None else ""
    lx, ly, rx, ry = (layout.get(k) for k in ("lx", "ly", "rx", "ry"))
    values = [
        ("Device", f"evdev/0/{pad.name}"),
        ("Buttons/A", get("south")), ("Buttons/B", get("east")), ("Buttons/X", get("west")),
        ("Buttons/Y", get("north")), ("Buttons/Z", get("r1")), ("Buttons/Start", get("start")),
        ("Main Stick/Up", stick(ly, "-")), ("Main Stick/Down", stick(ly, "+")),
        ("Main Stick/Left", stick(lx, "-")), ("Main Stick/Right", stick(lx, "+")),
        ("Main Stick/Calibration", "100.00 141.42 100.00 141.42 100.00 141.42 100.00 141.42"),
        ("C-Stick/Up", stick(ry, "-")), ("C-Stick/Down", stick(ry, "+")),
        ("C-Stick/Left", stick(rx, "-")), ("C-Stick/Right", stick(rx, "+")),
        ("C-Stick/Calibration", "100.00 141.42 100.00 141.42 100.00 141.42 100.00 141.42"),
        ("Triggers/L", get("l2")), ("Triggers/R", get("r2")),
        ("Triggers/L-Analog", get("l2")), ("Triggers/R-Analog", get("r2")),
        ("D-Pad/Up", get("up")), ("D-Pad/Down", get("down")),
        ("D-Pad/Left", get("left")), ("D-Pad/Right", get("right")),
        ("Rumble/Motor", "`Constant`"),
    ]
    _set_ini_section(config_dir / "GCPadNew.ini", "GCPad1", [(k, v) for k, v in values if v])


def restore_dolphin_keyboard(config_dir):
    """Sem controle: deixa o Dolphin com o teclado padrão dele."""
    pad_ini = config_dir / "GCPadNew.ini"
    if not pad_ini.exists():
        return
    text = pad_ini.read_text(encoding="utf-8", errors="replace")
    # Remove só o que o hub escreveu (Linux: evdev; Windows: XInput), nunca um ajuste manual.
    if "Device = evdev/" in text or "Device = XInput/" in text:
        pad_ini.unlink()


def configure_dolphin_xinput(config_dir):
    """Windows: liga o controle 1 do GameCube ao controle XInput 1 (Xbox e compatíveis).

    Os nomes ("Button A", "Left X+"...) são os que o próprio Dolphin usa para XInput.
    Sem controle conectado o Dolphin simplesmente não recebe nada dele; o teclado
    continua disponível pelo menu do Dolphin.
    """
    values = [
        ("Device", "XInput/0/Gamepad"),
        ("Buttons/A", "`Button A`"), ("Buttons/B", "`Button B`"), ("Buttons/X", "`Button X`"),
        ("Buttons/Y", "`Button Y`"), ("Buttons/Z", "`Shoulder R`"), ("Buttons/Start", "`Start`"),
        ("Main Stick/Up", "`Left Y+`"), ("Main Stick/Down", "`Left Y-`"),
        ("Main Stick/Left", "`Left X-`"), ("Main Stick/Right", "`Left X+`"),
        ("Main Stick/Calibration", "100.00 141.42 100.00 141.42 100.00 141.42 100.00 141.42"),
        ("C-Stick/Up", "`Right Y+`"), ("C-Stick/Down", "`Right Y-`"),
        ("C-Stick/Left", "`Right X-`"), ("C-Stick/Right", "`Right X+`"),
        ("C-Stick/Calibration", "100.00 141.42 100.00 141.42 100.00 141.42 100.00 141.42"),
        ("Triggers/L", "`Trigger L`"), ("Triggers/R", "`Trigger R`"),
        ("Triggers/L-Analog", "`Trigger L`"), ("Triggers/R-Analog", "`Trigger R`"),
        ("D-Pad/Up", "`Pad N`"), ("D-Pad/Down", "`Pad S`"), ("D-Pad/Left", "`Pad W`"), ("D-Pad/Right", "`Pad E`"),
        ("Rumble/Motor", "`Motor L | Motor R`"),
    ]
    _set_ini_section(config_dir / "GCPadNew.ini", "GCPad1", values)


# ---------------------------------------------------------------------------
# RMG (Nintendo 64) — modo automático do SDL3, feito uma vez
# ---------------------------------------------------------------------------
# Botões/eixos de gamepad do SDL3: SOUTH=0, EAST=1, WEST=2, NORTH=3, BACK=4, GUIDE=5,
# START=6, LS=7, RS=8, LB=9, RB=10, DPAD_UP=11, DOWN=12, LEFT=13, RIGHT=14.
# Eixos: LEFTX=0, LEFTY=1, RIGHTX=2, RIGHTY=3, LT=4, RT=5. Tipo 0=botão, 1=eixo.
RMG_MAPPING = [
    ("A", [(0, 0, 0)]), ("B", [(0, 2, 0)]), ("Start", [(0, 6, 0)]),
    ("DpadUp", [(0, 11, 0)]), ("DpadDown", [(0, 12, 0)]), ("DpadLeft", [(0, 13, 0)]), ("DpadRight", [(0, 14, 0)]),
    ("CButtonUp", [(1, 3, 0)]), ("CButtonDown", [(1, 3, 1)]), ("CButtonLeft", [(1, 2, 0)]), ("CButtonRight", [(1, 2, 1)]),
    ("LeftTrigger", [(0, 9, 0)]), ("RightTrigger", [(0, 10, 0), (1, 5, 1)]), ("ZTrigger", [(1, 4, 1)]),
    ("AnalogStickUp", [(1, 1, 0)]), ("AnalogStickDown", [(1, 1, 1)]),
    ("AnalogStickLeft", [(1, 0, 0)]), ("AnalogStickRight", [(1, 0, 1)]),
]


def configure_rmg(cfg):
    """Liga o controle 1 do N64 no RMG em modo automático (só na primeira vez)."""
    section = "Rosalie's Mupen GUI - Input Plugin Profile 0"
    if cfg.exists() and f"[{section}]" in cfg.read_text(encoding="utf-8", errors="replace"):
        return
    values = [("PluggedIn", "True"), ("DeviceType", "2"), ("DeviceName", '"Automatic"'),
              ("DeviceNum", "-1"), ("Deadzone", "9"), ("Sensitivity", "100"), ("Pak", "0")]
    for name, entries in RMG_MAPPING:
        values += [
            (f"{name}_Name", '"' + ";".join(name for _ in entries) + '"'),
            (f"{name}_InputType", '"' + ";".join(str(e[0]) for e in entries) + '"'),
            (f"{name}_Data", '"' + ";".join(str(e[1]) for e in entries) + '"'),
            (f"{name}_ExtraData", '"' + ";".join(str(e[2]) for e in entries) + '"'),
        ]
    _set_ini_section(cfg, section, values)
