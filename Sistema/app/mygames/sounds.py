"""Som de clique ao passar de uma caixa para outra na prateleira.

O som não é um arquivo baixado: é gerado aqui mesmo (um "tic" curto, parecido
com o clique de um mouse) e salvo uma vez na pasta de cache.

Como toca:
- Linux: GStreamer (já vem no Ubuntu/GNOME). O tocador fica preparado e só
  volta ao começo a cada clique, para o som sair na hora, sem atraso.
- Windows: winsound, que já vem no Python do runtime.
Se nada disso existir, o hub simplesmente fica sem som (nunca dá erro).
"""
import math
import random
import struct
import sys
import threading
import time
import wave

from gi.repository import GLib

from .paths import CACHE_DIR, IS_WINDOWS

RATE = 44100
VERSION = 2  # mude se alterar o som, para gerar o arquivo de novo
MIN_INTERVAL = 0.035  # segundos: navegação muito rápida não vira um "zumbido"


def _click_samples():
    """Gera o som do clique: um estalo agudo e curto com um leve "corpo" de plástico."""
    rng = random.Random(7)  # sempre o mesmo som
    samples = []
    low = 0.0
    for n in range(int(RATE * 0.028)):
        t = n / RATE
        # Estalo: ruído com o grave tirado (fica "seco"), sumindo em ~1 ms.
        noise = rng.uniform(-1, 1)
        low += (noise - low) * 0.35
        snap = (noise - low) * math.exp(-t / 0.0011)
        # Corpo: duas ressonâncias da carcaça, sumindo em poucos milissegundos.
        body = (0.55 * math.sin(2 * math.pi * 3100 * t) * math.exp(-t / 0.0028)
                + 0.30 * math.sin(2 * math.pi * 1450 * t) * math.exp(-t / 0.0045))
        attack = min(1.0, t / 0.0002)  # sem "estouro" no começo
        samples.append(attack * (0.75 * snap + body) * 0.68)
    return samples


def click_file():
    """Caminho do click.wav no cache (gera se ainda não existir)."""
    path = CACHE_DIR / f"click-v{VERSION}.wav"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        data = b"".join(struct.pack("<h", int(max(-1, min(1, s)) * 32767)) for s in _click_samples())
        temporary = path.with_suffix(".part")
        with wave.open(str(temporary), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(RATE)
            output.writeframes(data)
        temporary.replace(path)
    return path


class _GstPlayer:
    """Tocador do Linux: alguns tocadores prontos que se revezam (cliques seguidos se sobrepõem)."""

    def __init__(self, path):
        """Prepara os tocadores já carregados com o som."""
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        self.Gst = Gst
        self.players = []
        for _ in range(3):
            player = Gst.ElementFactory.make("playbin", None)
            if player is None:
                raise RuntimeError("GStreamer sem playbin")
            player.set_property("uri", path.as_uri())
            player.set_property("video-sink", Gst.ElementFactory.make("fakesink", None))
            player.set_state(Gst.State.PAUSED)  # já deixa o som carregado
            self.players.append(player)
        self.next = 0
        self.plays = {}  # quantas vezes cada tocador foi usado (para pausar depois)

    def play(self):
        """Toca o clique do começo."""
        Gst = self.Gst
        player = self.players[self.next]
        self.next = (self.next + 1) % len(self.players)
        player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE, 0)
        player.set_state(Gst.State.PLAYING)
        # Depois do som, pausa de novo (não deixa a saída de áudio ocupada à toa).
        self.plays[player] = self.plays.get(player, 0) + 1
        GLib.timeout_add(250, self._rest, player, self.plays[player])

    def _rest(self, player, play_number):
        """Pausa o tocador se ele não foi usado de novo nesse meio-tempo."""
        if self.plays.get(player) == play_number:
            player.set_state(self.Gst.State.PAUSED)
        return False

    def close(self):
        """Libera o áudio ao fechar o hub."""
        for player in self.players:
            player.set_state(self.Gst.State.NULL)


class _WindowsPlayer:
    """Tocador do Windows (winsound, assíncrono)."""

    def __init__(self, path):
        """Guarda o arquivo do som."""
        import winsound

        self.winsound = winsound
        self.path = str(path)

    def play(self):
        """Toca o clique sem travar a interface."""
        flags = self.winsound.SND_FILENAME | self.winsound.SND_ASYNC | self.winsound.SND_NODEFAULT
        self.winsound.PlaySound(self.path, flags)

    def close(self):
        """Nada a liberar no Windows."""


class ClickSound:
    """O som de navegação do hub. Use click() a cada caixa que passa."""

    def __init__(self, enabled=True):
        """Prepara o tocador numa thread, para não atrasar a abertura do hub."""
        self.enabled = enabled
        self.player = None  # fica pronto em instantes; até lá, cliques são ignorados
        self.last = 0.0
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        """Gera o som e abre o tocador (se falhar, o hub fica sem som)."""
        try:
            path = click_file()
            self.player = _WindowsPlayer(path) if IS_WINDOWS else _GstPlayer(path)
        except Exception as error:  # noqa: BLE001 - som é opcional
            print(f"Aviso (som): {error}", file=sys.stderr)

    def click(self):
        """Toca o clique (ignora se estiver desligado ou se veio rápido demais)."""
        if not self.enabled or self.player is None:
            return
        now = time.monotonic()
        if now - self.last < MIN_INTERVAL:
            return
        self.last = now
        try:
            self.player.play()
        except Exception:  # noqa: BLE001
            pass

    def close(self):
        """Libera o áudio."""
        if self.player is not None:
            self.player.close()
