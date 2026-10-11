"""Roteirista: cria o roteiro do Short e passa por uma revisão de segurança infantil."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

from .llm import ClaudeJSON, LLMError

log = logging.getLogger(__name__)


@dataclass
class Scene:
    narration: str
    image_prompt: str
    show_mascot: bool = True


@dataclass
class Script:
    series: str
    topic: str
    title: str
    description: str
    tags: list[str]
    scenes: list[Scene] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        return sum(len(s.narration.split()) for s in self.scenes)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Script":
        scenes = [Scene(**s) for s in data.get("scenes", [])]
        return cls(
            series=data["series"],
            topic=data["topic"],
            title=data["title"],
            description=data["description"],
            tags=list(data.get("tags", [])),
            scenes=scenes,
        )


SCRIPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "topic": {"type": "string", "description": "Tema em poucas palavras, usado para não repetir."},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "narration": {"type": "string"},
                    "image_prompt": {"type": "string"},
                    "show_mascot": {"type": "boolean"},
                },
                "required": ["narration", "image_prompt", "show_mascot"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["topic", "title", "description", "tags", "scenes"],
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


def local_checks(script: Script, min_words: int, max_words: int) -> list[str]:
    """Regras objetivas que não dependem do modelo."""
    issues: list[str] = []
    if not 4 <= len(script.scenes) <= 8:
        issues.append(f"O roteiro deve ter entre 4 e 8 cenas (tem {len(script.scenes)}).")
    if not min_words <= script.word_count <= max_words:
        issues.append(
            f"A narração total deve ter entre {min_words} e {max_words} palavras (tem {script.word_count})."
        )
    if not script.title.strip() or len(script.title) > 70:
        issues.append("O título deve ter entre 1 e 70 caracteres.")
    for i, scene in enumerate(script.scenes, 1):
        if not scene.narration.strip() or not scene.image_prompt.strip():
            issues.append(f"A cena {i} está sem narração ou sem descrição de imagem.")
    all_text = _strip_accents(" ".join([script.title, script.description] + [s.narration for s in script.scenes]))
    for word in _BLOCKED_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", all_text):
            issues.append(f"Evite a palavra/tema '{word}' em conteúdo infantil.")
    return issues


def _writer_system(cfg: dict[str, Any]) -> str:
    ch = cfg["channel"]
    mascot = ch["mascot"]
    return f"""Você é o roteirista do canal infantil do YouTube "{ch['name']}", que publica Shorts verticais \
para {ch['audience']}, em português do Brasil. O apresentador é {mascot['name']}, {mascot['description_pt']}. \
O bordão de despedida é: "{mascot['catchphrase']}".

Escreva roteiros curtos, calorosos e educativos, que um pai ou professor ficaria feliz de mostrar a uma criança.

Regras de conteúdo (obrigatórias):
- Vocabulário simples, frases curtas (até 12 palavras), tom alegre e acolhedor.
- Informação sempre verdadeira e cientificamente correta; na dúvida, escolha outro fato.
- Nada assustador, violento, triste demais, perigoso de imitar, com marcas, pessoas reais, \
personagens de terceiros, comidas ou produtos à venda.
- Sem estereótipos; inclua diversidade de forma natural.
- Não peça para curtir, se inscrever, comentar ou clicar em nada, nem peça dados pessoais.
- Não use emojis nem hashtags na narração.

Formato do Short:
- 4 a 8 cenas. Cada cena tem 1 ou 2 frases de narração (o que será falado em voz alta).
- A primeira cena é um gancho: uma pergunta ou surpresa logo na primeira frase.
- A última cena fecha com o bordão de {mascot['name']}.
- Total de {cfg['llm'].get('min_words', 60)} a {cfg['llm'].get('max_words', 140)} palavras faladas (cerca de 30 a 55 segundos).
- Escreva números por extenso na narração (ex.: "três"), pois ela vai para um narrador de voz.

Para cada cena, "image_prompt" é uma descrição VISUAL em inglês de uma única ilustração: \
quem aparece, o que está fazendo, cenário e cores. Nunca peça texto, letras ou números escritos na imagem. \
"show_mascot" indica se {mascot['name']} aparece na cena (não descreva a aparência dele; ela é adicionada automaticamente).

Metadados:
- "title": título em português, até 60 caracteres, curioso e honesto (sem caça-clique, sem caixa alta exagerada, sem hashtags).
- "description": 2 ou 3 frases para os pais explicando o que a criança aprende.
- "tags": 8 a 12 palavras-chave em português.
- "topic": o tema em poucas palavras."""


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
    parts.append("Escreva o roteiro completo do episódio.")
    return "\n\n".join(parts)


_REVIEW_SYSTEM = """Você é revisor(a) de qualidade e segurança de conteúdo infantil (3 a 8 anos) no YouTube, \
seguindo a COPPA e os princípios de qualidade do YouTube para conteúdo infantil.

Reprove ("approved": false) se houver QUALQUER um destes problemas:
- Informação factualmente errada ou duvidosa.
- Algo assustador, violento, perigoso de imitar, sexualizado, triste demais ou inadequado à idade.
- Publicidade, marcas, pessoas reais, personagens protegidos por direitos autorais.
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
        min_words, max_words = llm_cfg.get("min_words", 60), llm_cfg.get("max_words", 140)
        feedback: list[str] = []
        attempts = int(llm_cfg.get("max_attempts", 3))
        for attempt in range(1, attempts + 1):
            log.info("Escrevendo roteiro (%s), tentativa %d/%d", series["id"], attempt, attempts)
            raw = self.llm.generate(
                _writer_system(self.cfg),
                _writer_prompt(series, recent_topics, topic, feedback),
                SCRIPT_SCHEMA,
            )
            script = Script.from_dict({**raw, "series": series["id"]})

            feedback = local_checks(script, min_words, max_words)
            if not feedback:
                feedback = self.review(script)
            if not feedback:
                log.info("Roteiro aprovado: %s", script.title)
                return script
            log.warning("Roteiro reprovado: %s", feedback)
        raise LLMError(f"Nenhum roteiro aprovado após {attempts} tentativas. Últimos problemas: {feedback}")

    def review(self, script: Script) -> list[str]:
        payload = {
            "titulo": script.title,
            "descricao": script.description,
            "cenas": [{"narracao": s.narration, "imagem": s.image_prompt} for s in script.scenes],
        }
        result = self.llm.generate(
            _REVIEW_SYSTEM,
            "Revise este roteiro de Short infantil:\n\n" + json.dumps(payload, ensure_ascii=False, indent=2),
            REVIEW_SCHEMA,
        )
        if result.get("approved") and not result.get("issues"):
            return []
        return result.get("issues") or ["Revisão reprovou sem detalhar o motivo."]


def offline_script(cfg: dict[str, Any], series: dict[str, Any], topic: str | None = None) -> Script:
    """Roteiro fixo para testar o pipeline sem chamar nenhuma API."""
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
