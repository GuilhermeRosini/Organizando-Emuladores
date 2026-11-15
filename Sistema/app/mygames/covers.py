"""Capas dos jogos.

Ordem de procura:
1. Uma imagem com o mesmo nome do jogo ao lado do arquivo (ex.: "Mario.sfc" + "Mario.png").
2. Capa já baixada em Sistema/capas/<console>/.
3. Download do acervo público libretro-thumbnails (o mesmo usado pelo RetroArch).
"""
import difflib
import hashlib
import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request

from .paths import SYSTEM_DIR

COVERS_DIR = SYSTEM_DIR / "capas"
USER_AGENT = "MyGames-Hub"
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
INDEX_MAX_AGE = 30 * 24 * 3600
MISS_RETRY = 7 * 24 * 3600

# Repositório do libretro-thumbnails para cada console (e extensões especiais).
REPOS = {
    "ps2": "Sony_-_PlayStation_2",
    "super-nintendo": "Nintendo_-_Super_Nintendo_Entertainment_System",
    "gameboy-advance": "Nintendo_-_Game_Boy_Advance",
    "gamecube": "Nintendo_-_GameCube",
    "nintendo-64": "Nintendo_-_Nintendo_64",
    "neo-geo": "SNK_-_Neo_Geo",
}
REPOS_BY_EXTENSION = {
    ".gb": "Nintendo_-_Game_Boy",
    ".gbc": "Nintendo_-_Game_Boy_Color",
    ".sgb": "Nintendo_-_Game_Boy",
}

# Abreviações comuns de região em nomes de ROM (GoodTools) → nome no libretro.
REGIONS = {
    "u": "usa", "us": "usa", "usa": "usa", "e": "europe", "eu": "europe", "europe": "europe",
    "j": "japan", "jp": "japan", "japan": "japan", "b": "brazil", "br": "brazil", "brazil": "brazil",
    "w": "world", "world": "world", "f": "france", "g": "germany", "s": "spain", "i": "italy",
    "ue": "usa", "ju": "japan", "uj": "usa",
}
REGION_PREFERENCE = ["usa", "world", "brazil", "europe", "japan"]


def _request(url):
    """Monta o pedido HTTP com um nome de cliente genérico."""
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT})


def _fetch_json(url):
    """Baixa e lê um JSON da internet."""
    with urllib.request.urlopen(_request(url), timeout=20) as response:
        return json.load(response)


def _strip_accents(text):
    """Remove acentos ("Pokémon" -> "Pokemon") para comparar nomes."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _base_name(name):
    """Nome sem tags "(USA)", "[!]" etc., com ", The" levado para a frente."""
    base = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", name).strip()
    base = re.sub(r"^[A-Z]{4}[_-]?\d{3}\.?\d{2}[.\s_-]*", "", base)  # código de série do disco
    base = re.sub(r"^(.*?), (The|A|An)(\s*-.*|$)", r"\2 \1\3", base)
    return base


def normalize(name):
    """Reduz um nome de jogo ao essencial (sem tags, acentos e pontuação) para comparar."""
    text = _strip_accents(_base_name(name)).casefold().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "", text)


def _regions(name):
    """Regiões citadas no nome do arquivo, ex.: "(U)" ou "(USA)" -> {"usa"}."""
    found = set()
    for group in re.findall(r"\(([^)]*)\)", name):
        for part in re.split(r"[,\s]+", group.casefold()):
            if part in REGIONS:
                found.add(REGIONS[part])
    return found


def _region_score(candidate, wanted):
    """Nota da capa candidata: mesma região do jogo primeiro, depois EUA, Mundo, Brasil..."""
    regions = _regions(candidate)
    if wanted and regions & wanted:
        return 0
    for position, region in enumerate(REGION_PREFERENCE):
        if region in regions:
            return 1 + position
    return 10


def best_match(title, file_stem, names):
    """Escolhe o nome do libretro que mais combina com o jogo."""
    if not names:
        return None
    wanted = _regions(file_stem)
    keys = [normalize(title), normalize(file_stem)]
    keys = [key for key in dict.fromkeys(keys) if key]
    by_key = {}
    for name in names:
        by_key.setdefault(normalize(name), []).append(name)

    def pick(candidates):
        """Entre capas do mesmo jogo, escolhe a melhor região e evita betas/demos."""
        # Revisões e versões beta/demo ficam por último.
        return min(
            candidates,
            key=lambda n: (_region_score(n, wanted), bool(re.search(r"\((Beta|Proto|Demo|Sample|Rev)", n)), len(n)),
        )

    for key in keys:
        if key in by_key:
            return pick(by_key[key])
    # Nome do arquivo sem o subtítulo ("Zelda" → "Legend of Zelda, The - ...").
    for key in keys:
        if len(key) >= 6:
            starts = [n for k, group in by_key.items() if k.startswith(key) for n in group]
            if starts:
                shortest = min(len(normalize(n)) for n in starts)
                return pick([n for n in starts if len(normalize(n)) == shortest])
    # Só as primeiras palavras ("Bully Dublado v1.0" → "Bully").
    words = _strip_accents(_base_name(file_stem)).replace("_", " ").split()
    for count in range(len(words) - 1, 0, -1):
        key = normalize(" ".join(words[:count]))
        if len(key) >= 5 and key in by_key:
            return pick(by_key[key])
    for key in keys:
        close = difflib.get_close_matches(key, list(by_key), n=1, cutoff=0.86)
        if close:
            return pick(by_key[close[0]])
    return None


class CoverStore:
    """Encontra, guarda e baixa as capas dos jogos."""

    def __init__(self):
        """Começa sem nenhum índice de capas carregado."""
        self.indexes = {}

    # ---- Onde as capas ficam ---------------------------------------------------
    @staticmethod
    def _key(game):
        """Nome do arquivo da capa no cache (feito a partir do nome do arquivo do jogo)."""
        # Pelo nome do arquivo, não pelo caminho completo: assim as capas continuam
        # valendo depois de mover/copiar a pasta do hub para outro lugar.
        return hashlib.sha1(game.path.name.encode()).hexdigest()[:16]

    @staticmethod
    def _old_key(game):
        """Nome usado pelas versões antigas (pelo caminho completo), para aproveitar capas já baixadas."""
        return hashlib.sha1(str(game.path).encode()).hexdigest()[:16]

    def _cached(self, console, game):
        """Capa já baixada em Sistema/capas/<console>, se existir."""
        folder = COVERS_DIR / console.id
        for extension in IMAGE_EXTENSIONS:
            path = folder / f"{self._key(game)}{extension}"
            if path.is_file():
                return path
            # Capa baixada por uma versão antiga (nome pelo caminho completo).
            old = folder / f"{self._old_key(game)}{extension}"
            if old.is_file():
                try:
                    old.replace(path)
                    return path
                except OSError:
                    return old
        return None

    @staticmethod
    def local(game):
        """Imagem do usuário ao lado do jogo, com o mesmo nome."""
        for extension in IMAGE_EXTENSIONS:
            for candidate in (game.path.with_suffix(extension), game.path.parent / f"{game.title}{extension}"):
                if candidate.is_file():
                    return candidate
        return None

    def find(self, console, game):
        """Capa disponível agora, sem usar a internet."""
        return self.local(game) or self._cached(console, game)

    # ---- Download ----------------------------------------------------------------
    def _index(self, repo):
        """Lista de capas disponíveis no libretro-thumbnails para o console (guardada por 30 dias)."""
        if repo in self.indexes:
            return self.indexes[repo]
        path = COVERS_DIR / "indices" / f"{repo}.json"
        names = None
        try:
            if time.time() - path.stat().st_mtime < INDEX_MAX_AGE:
                names = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            names = None
        if names is None:
            root = _fetch_json(f"https://api.github.com/repos/libretro-thumbnails/{repo}/git/trees/master")
            folder = next((item for item in root.get("tree", []) if item["path"] == "Named_Boxarts"), None)
            names = []
            if folder:
                tree = _fetch_json(folder["url"])
                names = [item["path"][:-4] for item in tree.get("tree", []) if item["path"].endswith(".png")]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(names, ensure_ascii=False), encoding="utf-8")
        self.indexes[repo] = names
        return names

    def download(self, console, game):
        """Baixa a capa (bloqueante; chame fora da interface). Devolve o caminho ou None."""
        found = self.find(console, game)
        if found:
            return found
        repo = REPOS_BY_EXTENSION.get(game.path.suffix.casefold()) or REPOS.get(console.id)
        if not repo:
            return None
        folder = COVERS_DIR / console.id
        miss = folder / f"{self._key(game)}.sem-capa"
        try:
            if time.time() - miss.stat().st_mtime < MISS_RETRY:
                return None
        except OSError:
            pass
        name = best_match(game.title, game.path.stem, self._index(repo))
        folder.mkdir(parents=True, exist_ok=True)
        if not name:
            miss.touch()
            return None
        url = (
            f"https://raw.githubusercontent.com/libretro-thumbnails/{repo}/master/Named_Boxarts/"
            + urllib.parse.quote(name + ".png")
        )
        target = folder / f"{self._key(game)}.png"
        temporary = target.with_suffix(".part")
        try:
            with urllib.request.urlopen(_request(url), timeout=20) as response:
                temporary.write_bytes(response.read())
        except OSError:
            temporary.unlink(missing_ok=True)
            miss.touch()
            return None
        temporary.replace(target)
        miss.unlink(missing_ok=True)
        return target
