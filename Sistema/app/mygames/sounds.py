"""Som de clique ao passar de uma caixa para outra na prateleira.

O som não é um arquivo baixado: é gerado aqui mesmo (um "tic" curto, parecido
com o clique de um mouse) e salvo uma vez na pasta de cache.

Como toca:
- Linux: GStreamer (já vem no Ubuntu/GNOME). Um fluxo de áudio fica aberto
  tocando silêncio, e cada clique entra nele, para o som sair sempre e na hora.
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
    """Tocador do Linux: um fluxo de áudio que nunca para.

    O PipeWire/PulseAudio não toca um som tão curto quando o fluxo fica sem
    dados: depois de um silêncio ele espera juntar bastante áudio antes de
    voltar a tocar, e o clique nunca chega a isso (por isso só o primeiro
    saía). Então o fluxo é alimentado o tempo todo com pedacinhos de 10 ms
    de silêncio, e quando há um clique os pedacinhos levam o clique.
    """

    CHUNK_SECONDS = 0.01

    def __init__(self, path):
        """Abre o fluxo de áudio e guarda o som já pronto na memória."""
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        self.Gst = Gst
        with wave.open(str(path), "rb") as source:
            rate = source.getframerate()
            channels = source.getnchannels()
            self.data = source.readframes(source.getnframes())

        frames = int(rate * self.CHUNK_SECONDS)
        self.chunk_bytes = frames * channels * 2
        self.chunk_duration = Gst.util_uint64_scale(frames, Gst.SECOND, rate)
        self.silence = bytes(self.chunk_bytes)
        self.timestamp = 0
        self.lock = threading.Lock()
        self.offset = None  # onde está o clique sendo tocado (None = silêncio)

        self.pipeline = Gst.Pipeline.new("click-sound")
        self.source = Gst.ElementFactory.make("appsrc", None)
        convert = Gst.ElementFactory.make("audioconvert", None)
        resample = Gst.ElementFactory.make("audioresample", None)
        sink = None
        for name in ("pulsesink", "pipewiresink", "autoaudiosink"):
            sink = Gst.ElementFactory.make(name, None)
            if sink is not None:
                break
        if None in (self.source, convert, resample, sink):
            raise RuntimeError("GStreamer sem os elementos de áudio")

        caps = Gst.Caps.from_string(
            f"audio/x-raw,format=S16LE,layout=interleaved,rate={rate},channels={channels}")
        self.source.set_property("caps", caps)
        self.source.set_property("format", Gst.Format.TIME)
        self.source.set_property("max-bytes", self.chunk_bytes)  # pouco adiantado = pouco atraso
        self.source.connect("need-data", self._on_need_data)
        # Buffer pequeno na saída: o clique sai rápido depois da tecla.
        if sink.find_property("buffer-time") is not None:
            sink.set_property("buffer-time", 60_000)
            sink.set_property("latency-time", 10_000)

        for element in (self.source, convert, resample, sink):
            self.pipeline.add(element)
        self.source.link(convert)
        convert.link(resample)
        resample.link(sink)
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("não foi possível abrir a saída de áudio")

    def _on_need_data(self, source, _length):
        """O fluxo pediu mais áudio: manda o próximo pedaço (clique ou silêncio)."""
        with self.lock:
            if self.offset is None:
                data = self.silence
            else:
                data = self.data[self.offset:self.offset + self.chunk_bytes]
                self.offset += self.chunk_bytes
                if self.offset >= len(self.data):
                    self.offset = None
                data = data.ljust(self.chunk_bytes, b"\0")
        buffer = self.Gst.Buffer.new_wrapped(data)
        buffer.pts = self.timestamp
        buffer.duration = self.chunk_duration
        self.timestamp += self.chunk_duration
        source.emit("push-buffer", buffer)

    def play(self):
        """Começa o clique no próximo pedaço de áudio."""
        with self.lock:
            self.offset = 0

    def close(self):
        """Libera o áudio ao fechar o hub."""
        self.pipeline.set_state(self.Gst.State.NULL)


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
