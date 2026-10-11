"""Chamada ao Claude que devolve JSON validado por schema (structured outputs)."""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

log = logging.getLogger(__name__)

# Modelos que aceitam o fallback automático do lado do servidor
# (`fallbacks: "default"`) quando o classificador de segurança recusa.
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    pass


class ClaudeJSON:
    def __init__(self, model: str, effort: str = "medium", refusal_fallbacks: bool = True):
        self.model = model
        self.effort = effort
        self.use_fallbacks = refusal_fallbacks and model in _FALLBACK_MODELS
        self.client = anthropic.Anthropic(max_retries=4)

    def generate(self, system: str, prompt: str, schema: dict[str, Any], max_tokens: int = 16000) -> dict[str, Any]:
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": schema},
            },
        )
        try:
            if self.use_fallbacks:
                response = self.client.beta.messages.create(
                    betas=[_FALLBACK_BETA], fallbacks="default", **kwargs
                )
            else:
                response = self.client.messages.create(**kwargs)
        except anthropic.AuthenticationError as exc:
            raise LLMError("ANTHROPIC_API_KEY inválida ou ausente.") from exc
        except anthropic.BadRequestError as exc:
            raise LLMError(f"Requisição rejeitada pela API: {exc.message}") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Limite de uso da API atingido; tente mais tarde.") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Erro da API Anthropic ({exc.status_code}): {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Falha de conexão com a API Anthropic.") from exc

        if response.stop_reason == "refusal":
            category = getattr(getattr(response, "stop_details", None), "category", None)
            raise LLMError(f"O modelo recusou o pedido (categoria: {category}).")
        if response.stop_reason == "max_tokens":
            raise LLMError("Resposta cortada por max_tokens.")

        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Resposta não é JSON válido: {text[:200]!r}") from exc
