"""Leitura e gravação do config.json do hub."""
import json
import os
import tempfile
from pathlib import Path

from .consoles import CONSOLES
from .paths import CONFIG_FILE, ROOT, from_other_system, resolve_stored, store_path

DEFAULTS = {"tela_cheia": True, "consoles": {}}
LEGACY_EMULATORS = ROOT / "emulators.json"
LEGACY_GAMES = ROOT / "games.json"


class ConfigError(Exception):
    """Erro ao ler ou gravar o config.json (mostrado ao usuário como aviso)."""
    pass


def _write_atomic(path, data):
    """Grava o JSON num arquivo temporário e troca de uma vez, para nunca deixar o config.json pela metade.
    """
    # Grava num temporário e troca, para nunca deixar o arquivo pela metade.
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _migrate_legacy():
    """Converte emulators.json/games.json da versão 1 para o config.json."""
    data = {"tela_cheia": True, "consoles": {}}
    for legacy, key in ((LEGACY_EMULATORS, "emulador"), (LEGACY_GAMES, "pastas_jogos")):
        if not legacy.exists():
            continue
        try:
            values = json.loads(legacy.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for console_id, value in values.items():
            if console_id not in CONSOLES or not isinstance(value, str):
                continue
            path = resolve_stored(value)
            # Só migra o que ainda existe; o resto era a causa dos erros antigos.
            if path is None or not path.exists():
                continue
            if key == "emulador" and not path.is_file():
                continue
            entry = data["consoles"].setdefault(console_id, {})
            if key == "emulador":
                entry["emulador"] = store_path(path)
            else:
                entry["pastas_jogos"] = [store_path(path)]
    return data


class Config:
    """Preferências do hub guardadas em Sistema/config.json (tela cheia, emulador e pastas de jogos de cada
    console).
    """

    def __init__(self):
        """Começa com os valores padrão e lê o config.json, se existir."""
        self.data = json.loads(json.dumps(DEFAULTS))
        self.load()

    def load(self):
        """Lê o config.json (ou migra os arquivos da versão 1) e completa o que estiver faltando."""
        if not CONFIG_FILE.exists():
            if LEGACY_EMULATORS.exists() or LEGACY_GAMES.exists():
                self.data = _migrate_legacy()
                self.save()
            return
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ConfigError(f"Não foi possível ler config.json: {error}") from error
        if not isinstance(data, dict):
            raise ConfigError("config.json precisa conter um objeto JSON.")
        data.setdefault("tela_cheia", True)
        if not isinstance(data.get("consoles"), dict):
            data["consoles"] = {}
        self.data = data

    def save(self):
        """Salva as preferências no config.json."""
        try:
            _write_atomic(CONFIG_FILE, self.data)
        except OSError as error:
            raise ConfigError(f"Não foi possível salvar config.json: {error}") from error

    # ---- Preferências gerais -------------------------------------------------
    @property
    def fullscreen(self):
        """Se os emuladores devem abrir em tela cheia."""
        return bool(self.data.get("tela_cheia", True))

    @fullscreen.setter
    def fullscreen(self, value):
        """Liga/desliga a tela cheia dos emuladores e salva."""
        self.data["tela_cheia"] = bool(value)
        self.save()

    @property
    def click_sound(self):
        """Se toca o som de clique ao passar pelas caixas."""
        return bool(self.data.get("som_navegacao", True))

    @click_sound.setter
    def click_sound(self, value):
        """Liga/desliga o som de clique e salva."""
        self.data["som_navegacao"] = bool(value)
        self.save()

    @property
    def click_volume(self):
        """Volume do som de clique, de 0 a 100 (%)."""
        try:
            return max(0, min(100, int(self.data.get("volume_clique", 100))))
        except (TypeError, ValueError):
            return 100

    @click_volume.setter
    def click_volume(self, value):
        """Muda o volume do som de clique e salva."""
        self.data["volume_clique"] = max(0, min(100, int(value)))
        self.save()

    # ---- Por console ---------------------------------------------------------
    def _entry(self, console_id, create=False):
        """Configuração de um console no config.json (create=True cria a entrada se não existir)."""
        consoles = self.data["consoles"]
        if create:
            return consoles.setdefault(console_id, {})
        entry = consoles.get(console_id)
        return entry if isinstance(entry, dict) else {}

    def emulator_override(self, console_id):
        """Emulador escolhido manualmente (ou None para detecção automática)."""
        value = self._entry(console_id).get("emulador")
        if isinstance(value, str) and value.startswith("flatpak:"):
            return value
        return resolve_stored(value)

    def set_emulator(self, console_id, path):
        """Define o emulador escolhido para o console (None volta para a detecção automática)."""
        entry = self._entry(console_id, create=True)
        if path is None:
            entry.pop("emulador", None)
        elif isinstance(path, str) and path.startswith("flatpak:"):
            entry["emulador"] = path
        else:
            entry["emulador"] = store_path(path)
        self.save()

    def custom_arguments(self, console_id):
        """Argumentos personalizados do emulador do console, se o usuário definiu algum no config.json."""
        arguments = self._entry(console_id).get("argumentos")
        if isinstance(arguments, list) and all(isinstance(a, str) for a in arguments):
            return arguments
        return None

    def game_folders(self, console_id):
        """Pastas de jogos extras escolhidas pelo usuário para o console."""
        values = self._entry(console_id).get("pastas_jogos") or []
        if not isinstance(values, list):
            return []
        # Pastas escolhidas no outro sistema (Linux/Windows) não valem aqui.
        values = [value for value in values if not from_other_system(value)]
        return [path for path in (resolve_stored(value) for value in values) if path]

    def add_game_folder(self, console_id, path):
        """Adiciona uma pasta de jogos extra ao console e salva."""
        entry = self._entry(console_id, create=True)
        folders = entry.setdefault("pastas_jogos", [])
        resolved = Path(path).resolve()
        if resolved not in self.game_folders(console_id):
            folders.append(store_path(resolved))
            self.save()

    def remove_game_folder(self, console_id, path):
        """Remove uma pasta de jogos extra do console e salva."""
        entry = self._entry(console_id, create=True)
        target = Path(path)
        entry["pastas_jogos"] = [
            value
            for value in entry.get("pastas_jogos", [])
            if resolve_stored(value) != target
        ]
        self.save()
