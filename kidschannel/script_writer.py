"""Roteirista: cria o roteiro (narrado ou cantado) e passa por uma revisão de segurança infantil."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

from .llm import ClaudeJSON, LLMError

log = logging.getLogger(__name__)

NARRATION = "narration"
SONG = "song"


@dataclass
class Scene:
    narration: str  # no formato música, é a linha cantada
    image_prompt: str
    show_mascot: bool = True


@dataclass
class SongSection:
    name: str
    duration: float  # segundos
    lines: list[Scene] = field(default_factory=list)


@dataclass
class Script:
    series: str
    topic: str
    title: str
    description: str
    tags: list[str]
    scenes: list[Scene] = field(default_factory=list)
    format: str = NARRATION
    music_style: str = ""
    sections: list[SongSection] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.format == SONG:
            self.scenes = [line for section in self.sections for line in section.lines]

    @property
    def word_count(self) -> int:
        return sum(len(s.narration.split()) for s in self.scenes)

    @property
    def song_duration(self) -> float:
        return sum(s.duration for s in self.sections)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.format == SONG:
            data.pop("scenes")  # é derivado das seções
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Script":
        sections = [
            SongSection(name=s["name"], duration=float(s["duration"]), lines=[Scene(**line) for line in s["lines"]])
            for s in data.get("sections", [])
        ]
        return cls(
            series=data["series"],
            topic=data["topic"],
            title=data["title"],
            description=data["description"],
            tags=list(data.get("tags", [])),
            scenes=[Scene(**s) for s in data.get("scenes", [])],
            format=data.get("format", NARRATION),
            music_style=data.get("music_style", ""),
            sections=sections,
        )

    @classmethod
    def from_song_response(cls, raw: dict[str, Any], series_id: str) -> "Script":
        sections = [
            {
                "name": s["name"],
                "duration": s["duration_seconds"],
                "lines": [
                    {"narration": line["text"], "image_prompt": line["image_prompt"], "show_mascot": line["show_mascot"]}
                    for line in s["lines"]
                ],
            }
            for s in raw["sections"]
        ]
        return cls.from_dict({**raw, "series": series_id, "format": SONG, "sections": sections})


_META_PROPS = {
    "topic": {"type": "string", "description": "Tema em poucas palavras, usado para não repetir."},
    "title": {"type": "string"},
    "description": {"type": "string"},
    "tags": {"type": "array", "items": {"type": "string"}},
}
_IMAGE_PROPS = {"image_prompt": {"type": "string"}, "show_mascot": {"type": "boolean"}}

SCRIPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        **_META_PROPS,
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"narration": {"type": "string"}, **_IMAGE_PROPS},
                "required": ["narration", "image_prompt", "show_mascot"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["topic", "title", "description", "tags", "scenes"],
    "additionalProperties": False,
}

SONG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        **_META_PROPS,
        "music_style": {"type": "string", "description": "Como a música soa, em inglês."},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "duration_seconds": {"type": "number"},
                    "lines": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}, **_IMAGE_PROPS},
                            "required": ["text", "image_prompt", "show_mascot"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["name", "duration_seconds", "lines"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["topic", "title", "description", "tags", "music_style", "sections"],
    "additionalProperties": False,
}

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "approved": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["approved", "issues"],
    "additionalProperties": False,
}

# Camada extra e barata, além da revisão do modelo. Comparação sem acentos.
_BLOCKED_WORDS = [
    # "burro" fica de fora de propósito: também é o nome do animal.
    "morte", "morrer", "morreu", "matar", "matou", "arma", "armas", "faca",
    "revolver", "pistola", "tiro", "assassino", "demonio", "inferno", "diabo",
    "droga", "drogas", "cerveja", "vinho", "cigarro", "fumar", "sexy", "sexo",
    "idiota", "estupido", "otario", "odeio",
]


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn").lower()


def _common_checks(script: Script) -> list[str]:
    issues: list[str] = []
    if not script.title.strip() or len(script.title) > 70:
        issues.append("O título deve ter entre 1 e 70 caracteres.")
    for i, scene in enumerate(script.scenes, 1):
        if not scene.narration.strip() or not scene.image_prompt.strip():
            issues.append(f"A cena/linha {i} está sem texto ou sem descrição de imagem.")
    all_text = _strip_accents(" ".join([script.title, script.description] + [s.narration for s in script.scenes]))
    for word in _BLOCKED_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", all_text):
            issues.append(f"Evite a palavra/tema '{word}' em conteúdo infantil.")
    return issues


def local_checks(script: Script, min_words: int, max_words: int) -> list[str]:
    """Regras objetivas (formato narrado) que não dependem do modelo."""
    issues: list[str] = []
    if not 4 <= len(script.scenes) <= 8:
        issues.append(f"O roteiro deve ter entre 4 e 8 cenas (tem {len(script.scenes)}).")
    if not min_words <= script.word_count <= max_words:
        issues.append(
            f"A narração total deve ter entre {min_words} e {max_words} palavras (tem {script.word_count})."
        )
    return issues + _common_checks(script)


def song_checks(script: Script, min_seconds: float, max_seconds: float) -> list[str]:
    """Regras objetivas do formato música (limites da API de música e do ritmo cantado)."""
    issues: list[str] = []
    if not 3 <= len(script.sections) <= 6:
        issues.append(f"A música deve ter de 3 a 6 seções (tem {len(script.sections)}).")
    if not min_seconds <= script.song_duration <= max_seconds:
        issues.append(
            f"A soma das durações deve ficar entre {min_seconds:.0f} e {max_seconds:.0f} segundos "
            f"(está em {script.song_duration:.1f})."
        )
    for section in script.sections:
        if not 3 <= section.duration <= 30:
            issues.append(f"A seção '{section.name}' deve durar de 3 a 30 segundos.")
        if not 1 <= len(section.lines) <= 6:
            issues.append(f"A seção '{section.name}' deve ter de 1 a 6 linhas.")
        words = sum(len(line.narration.split()) for line in section.lines)
        if section.duration and words / section.duration > 3.0:
            issues.append(
                f"A seção '{section.name}' tem palavras demais para {section.duration:.0f}s "
                "(máx. 3 palavras por segundo cantado): aumente a duração ou encurte a letra."
            )
        for line in section.lines:
            if len(line.narration) > 70:
                issues.append(f"Linha longa demais para cantar: '{line.narration[:40]}...'")
    if len({s.image_prompt for s in script.scenes}) > 10:
        issues.append("Use no máximo 10 descrições de imagem diferentes (repita as do refrão).")
    return issues + _common_checks(script)


# ---------------------------------------------------------------- prompts
def _rules(cfg: dict[str, Any]) -> str:
    return """Regras de conteúdo (obrigatórias):
- Vocabulário simples, tom alegre e acolhedor, que um pai ou professor ficaria feliz de mostrar.
- Informação sempre verdadeira; na dúvida, escolha outro fato.
- Nada assustador, violento, triste demais, perigoso de imitar, com marcas, pessoas reais, \
personagens de terceiros, comidas ou produtos à venda.
- Sem estereótipos; inclua diversidade de forma natural.
- Não peça para curtir, se inscrever, comentar ou clicar em nada, nem peça dados pessoais.
- Não use emojis nem hashtags no texto falado ou cantado."""


def _image_rules(mascot_name: str) -> str:
    return f""""image_prompt" é uma descrição VISUAL em inglês de uma única ilustração: quem aparece, \
o que está fazendo, cenário e cores, com muita expressão e movimento. Nunca peça texto, letras ou \
números escritos na imagem. "show_mascot" indica se {mascot_name} aparece (não descreva a aparência dele; \
ela é adicionada automaticamente)."""


_META_RULES = """Metadados:
- "title": título em português, até 60 caracteres, curioso e honesto (sem caça-clique, sem caixa alta exagerada, sem hashtags).
- "description": 2 ou 3 frases para os pais explicando o que a criança aprende.
- "tags": 8 a 12 palavras-chave em português.
- "topic": o tema em poucas palavras."""


def _writer_system(cfg: dict[str, Any]) -> str:
    ch = cfg["channel"]
    mascot = ch["mascot"]
    llm = cfg["llm"]
    return f"""Você é o roteirista do canal infantil do YouTube "{ch['name']}", que publica Shorts verticais \
para {ch['audience']}, em português do Brasil. O apresentador é {mascot['name']}, {mascot['description_pt']}. \
O bordão de despedida é: "{mascot['catchphrase']}".

Seu objetivo é fazer Shorts que as crianças queiram ver de novo e de novo, sem perder o valor educativo.

{_rules(cfg)}

Formato do Short narrado (feito para viralizar):
- GANCHO no primeiro segundo: a primeira frase é uma pergunta surpreendente ou um fato que dá vontade de saber mais.
- Ritmo rápido: 4 a 8 cenas, uma ideia por cena, frases de até 10 palavras, muita emoção na fala.
- Use repetição e convide a criança a participar ("faça o som!", "conte comigo!").
- A última cena fecha com o bordão de {mascot['name']} e, se possível, puxa de volta para o começo \
(ex.: repete a pergunta do início), para o vídeo funcionar em loop.
- Total de {llm.get('min_words', 50)} a {llm.get('max_words', 110)} palavras faladas.
- Escreva números por extenso na narração (ex.: "três"), pois ela vai para um narrador de voz.

{_image_rules(mascot['name'])}

{_META_RULES}"""


def _song_system(cfg: dict[str, Any]) -> str:
    ch = cfg["channel"]
    mascot = ch["mascot"]
    song = cfg.get("song", {})
    lo, hi = song.get("min_seconds", 20), song.get("max_seconds", 40)
    return f"""Você é compositor(a) de músicas infantis do canal do YouTube "{ch['name']}", que publica Shorts \
verticais para {ch['audience']}, em português do Brasil. O personagem principal é {mascot['name']}, \
{mascot['description_pt']}.

Seu objetivo é criar músicas ORIGINAIS, grudentas e educativas, que as crianças cantem junto e queiram \
ouvir de novo e de novo — o tipo de música que viraliza em Shorts infantis.

{_rules(cfg)}
- A música deve ser 100% original: não copie nem adapte letra, melodia ou refrão de músicas existentes \
(nada de paródias), nem cite artistas ou personagens conhecidos.

Como compor (feito para viralizar):
- A PRIMEIRA seção é o refrão (name "Chorus"), cantado logo no primeiro segundo — é o gancho.
- Refrão com 2 linhas curtas, muito repetitivo, com uma onomatopeia ou palavra divertida fácil de cantar junto.
- Estrutura: Chorus → Verse → Chorus → Outro curtinho que puxa o refrão de novo (para o vídeo dar loop).
- Linhas de 3 a 7 palavras, rimas simples, vocabulário de 3 a 6 anos.
- Ensine algo de verdade (sons dos animais, cores, números, partes do corpo, hábitos saudáveis, emoções).
- Convide gestos seguros: bater palmas, pular no lugar, rodar, fazer o som do bicho.
- Nomes das seções em inglês: "Chorus", "Verse", "Bridge" ou "Outro".
- "duration_seconds": de 4 a 12 segundos por seção, no máximo 2 palavras cantadas por segundo; \
total entre {lo} e {hi} segundos.
- Nas repetições do refrão, repita exatamente o mesmo texto e o mesmo image_prompt.
- "music_style": em inglês, 1 a 2 frases descrevendo o SOM: gênero infantil e ritmo (ex.: bouncy kids pop, \
Brazilian forró for kids, marchinha, kids reggae), instrumentos, andamento (100 a 130 BPM) e voz \
(ex.: cheerful female vocals, kids choir on the chorus). Não cite artistas.

Cada linha da letra vira uma cena do vídeo:
{_image_rules(mascot['name'])}

{_META_RULES}"""


def _writer_prompt(series: dict[str, Any], recent_topics: list[str], topic: str | None, feedback: list[str]) -> str:
    parts = [
        f"Série: {series['name']}",
        f"Proposta da série: {series['brief'].strip()}",
    ]
    if series.get("examples"):
        parts.append("Exemplos de temas desta série (apenas inspiração): " + "; ".join(series["examples"]))
    if topic:
        parts.append(f"Tema obrigatório deste episódio: {topic}")
    elif recent_topics:
        parts.append(
            "Temas já publicados (NÃO repita nem faça algo muito parecido): " + "; ".join(recent_topics)
        )
        parts.append("Escolha um tema novo e interessante para esta série.")
    if feedback:
        parts.append(
            "A versão anterior foi reprovada pela revisão. Corrija estes pontos:\n- " + "\n- ".join(feedback)
        )
    parts.append("Escreva o episódio completo.")
    return "\n\n".join(parts)


_REVIEW_SYSTEM = """Você é revisor(a) de qualidade e segurança de conteúdo infantil (3 a 8 anos) no YouTube, \
seguindo a COPPA e os princípios de qualidade do YouTube para conteúdo infantil.

Reprove ("approved": false) se houver QUALQUER um destes problemas:
- Informação factualmente errada ou duvidosa.
- Algo assustador, violento, perigoso de imitar, sexualizado, triste demais ou inadequado à idade.
- Publicidade, marcas, pessoas reais, personagens protegidos por direitos autorais.
- Letra ou refrão copiado/adaptado de música existente (paródia).
- Pedidos de curtir/inscrever/comentar/clicar, ou pedidos de dados pessoais.
- Linguagem difícil demais, frases longas ou tom negativo.
- Título enganoso ou sensacionalista.
- Descrições de imagem que pedem texto escrito ou que poderiam gerar algo assustador.

Em "issues", liste cada problema de forma objetiva e acionável. Se estiver tudo certo, aprove com a lista vazia."""


class ScriptWriter:
    def __init__(self, cfg: dict[str, Any], llm: ClaudeJSON | None = None):
        self.cfg = cfg
        llm_cfg = cfg["llm"]
        self.llm = llm or ClaudeJSON(
            model=llm_cfg["model"],
            effort=llm_cfg.get("effort", "medium"),
            refusal_fallbacks=llm_cfg.get("refusal_fallbacks", True),
        )

    def write(self, series: dict[str, Any], recent_topics: list[str], topic: str | None = None) -> Script:
        llm_cfg = self.cfg["llm"]
        is_song = series.get("format", NARRATION) == SONG
        song_cfg = self.cfg.get("song", {})
        feedback: list[str] = []
        attempts = int(llm_cfg.get("max_attempts", 3))
        for attempt in range(1, attempts + 1):
            log.info("Escrevendo %s (%s), tentativa %d/%d",
                     "música" if is_song else "roteiro", series["id"], attempt, attempts)
            prompt = _writer_prompt(series, recent_topics, topic, feedback)
            if is_song:
                raw = self.llm.generate(_song_system(self.cfg), prompt, SONG_SCHEMA)
                script = Script.from_song_response(raw, series["id"])
                feedback = song_checks(script, song_cfg.get("min_seconds", 20), song_cfg.get("max_seconds", 40))
            else:
                raw = self.llm.generate(_writer_system(self.cfg), prompt, SCRIPT_SCHEMA)
                script = Script.from_dict({**raw, "series": series["id"]})
                feedback = local_checks(script, llm_cfg.get("min_words", 50), llm_cfg.get("max_words", 110))
            if not feedback:
                feedback = self.review(script)
            if not feedback:
                log.info("Aprovado: %s", script.title)
                return script
            log.warning("Reprovado: %s", feedback)
        raise LLMError(f"Nenhum roteiro aprovado após {attempts} tentativas. Últimos problemas: {feedback}")

    def review(self, script: Script) -> list[str]:
        payload: dict[str, Any] = {"titulo": script.title, "descricao": script.description}
        if script.format == SONG:
            payload["estilo_musical"] = script.music_style
            payload["letra"] = [
                {"secao": s.name, "segundos": s.duration,
                 "linhas": [{"texto": line.narration, "imagem": line.image_prompt} for line in s.lines]}
                for s in script.sections
            ]
            kind = "esta música de Short infantil"
        else:
            payload["cenas"] = [{"narracao": s.narration, "imagem": s.image_prompt} for s in script.scenes]
            kind = "este roteiro de Short infantil"
        result = self.llm.generate(
            _REVIEW_SYSTEM,
            f"Revise {kind}:\n\n" + json.dumps(payload, ensure_ascii=False, indent=2),
            REVIEW_SCHEMA,
        )
        if result.get("approved") and not result.get("issues"):
            return []
        return result.get("issues") or ["Revisão reprovou sem detalhar o motivo."]


# ---------------------------------------------------------------- roteiros fixos (modo offline)
def offline_script(cfg: dict[str, Any], series: dict[str, Any], topic: str | None = None) -> Script:
    """Roteiro fixo para testar o pipeline sem chamar nenhuma API."""
    if series.get("format", NARRATION) == SONG:
        return offline_song(series)
    mascot = cfg["channel"]["mascot"]
    topic = topic or "o polvo tem três corações"
    lines = [
        ("Você sabia que o polvo tem três corações?", "an octopus waving hello under the sea", False),
        (f"Oi, amiguinhos! Eu sou o {mascot['name']} e hoje vamos descobrir juntos.", "waving at the viewer in a sunny meadow", True),
        ("Dois corações ajudam o polvo a respirar debaixo da água.", "a friendly octopus breathing near colorful corals", False),
        ("E o terceiro coração cuida de todo o resto do corpinho.", "a happy octopus swimming among fish", False),
        ("Que bicho incrível, não é? Conte para alguém o que você aprendeu!", "smiling next to a friendly octopus at the beach", True),
        (mascot["catchphrase"], "waving goodbye at sunset", True),
    ]
    return Script(
        series=series["id"],
        topic=topic,
        title="O polvo tem três corações?",
        description="Uma curiosidade divertida sobre o polvo para crianças pequenas.",
        tags=["polvo", "curiosidades", "animais", "infantil"],
        scenes=[Scene(narration=n, image_prompt=p, show_mascot=m) for n, p, m in lines],
    )


def offline_song(series: dict[str, Any]) -> Script:
    duck = ("a happy yellow duckling splashing in a blue pond", False)
    chorus = [("Quá quá quá, o patinho quer nadar", *duck),
              ("Quá quá quá, vem comigo cantar!", "dancing and clapping next to the pond", True)]
    sections = [
        SongSection("Chorus", 7.0, [Scene(*line) for line in chorus]),
        SongSection("Verse", 7.0, [
            Scene("Muu muu muu, a vaquinha diz bom dia", "a smiling cow waving on a green farm", False),
            Scene("Au au au, o cachorro faz folia", "a playful puppy jumping with joy", False),
        ]),
        SongSection("Chorus", 7.0, [Scene(*line) for line in chorus]),
        SongSection("Outro", 5.0, [
            Scene("Bate palma, bate o pé", "clapping hands with farm animals", True),
            Scene("E de novo: quá quá quá!", *duck),
        ]),
    ]
    return Script(
        series=series["id"],
        topic="os sons dos animais da fazenda",
        title="Quá quá quá! A música dos bichinhos",
        description="Uma música para cantar junto e aprender os sons dos animais da fazenda.",
        tags=["música infantil", "sons dos animais", "pato", "vaca", "cachorro"],
        format=SONG,
        music_style="Bouncy kids pop with ukulele, glockenspiel and hand claps, 120 BPM, cheerful female vocals.",
        sections=sections,
    )
