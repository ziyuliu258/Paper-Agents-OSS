from __future__ import annotations

import hashlib
import math
from typing import Sequence

import httpx
from openai import OpenAI
from openai import AsyncOpenAI

from utils.config import (
    get_embedding_model,
    get_httpx_client_kwargs,
    load_runtime_config,
)

_client: AsyncOpenAI | None = None
_sync_client: OpenAI | None = None
_client_signature: tuple[str, ...] | None = None
_sync_client_signature: tuple[str, ...] | None = None


def _get_client() -> AsyncOpenAI:
    global _client, _client_signature
    embedding_provider = (
        load_runtime_config().get("providers", {}).get("embedding", {})
    )
    signature = (
        str(embedding_provider.get("base_url") or ""),
        str(embedding_provider.get("api_key") or ""),
        str(get_httpx_client_kwargs()),
    )
    if _client is None or _client_signature != signature:
        http_client = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=30), **get_httpx_client_kwargs())
        _client = AsyncOpenAI(
            base_url=str(embedding_provider.get("base_url") or ""),
            api_key=str(embedding_provider.get("api_key") or ""),
            http_client=http_client,
            max_retries=0,
        )
        _client_signature = signature
    return _client


def _get_sync_client() -> OpenAI:
    global _sync_client, _sync_client_signature
    embedding_provider = (
        load_runtime_config().get("providers", {}).get("embedding", {})
    )
    signature = (
        str(embedding_provider.get("base_url") or ""),
        str(embedding_provider.get("api_key") or ""),
        str(get_httpx_client_kwargs()),
    )
    if _sync_client is None or _sync_client_signature != signature:
        http_client = httpx.Client(timeout=httpx.Timeout(120, connect=30), **get_httpx_client_kwargs())
        _sync_client = OpenAI(
            base_url=str(embedding_provider.get("base_url") or ""),
            api_key=str(embedding_provider.get("api_key") or ""),
            http_client=http_client,
            max_retries=0,
        )
        _sync_client_signature = signature
    return _sync_client


def cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _fallback_embed_text(text: str, dims: int = 64) -> list[float]:
    vector = [0.0] * dims
    tokens = [token for token in text.lower().split() if token]
    if not tokens:
        return vector
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = digest[0] % dims
        sign = 1.0 if digest[1] % 2 == 0 else -1.0
        weight = 1.0 + (digest[2] / 255.0)
        vector[index] += sign * weight
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


async def embed_texts(
    texts: list[str], model: str | None = None
) -> list[list[float]]:
    resolved_model = str(model or get_embedding_model()).strip() or get_embedding_model()
    try:
        client = _get_client()
        response = await client.embeddings.create(model=resolved_model, input=texts)
        return [item.embedding for item in response.data]
    except Exception:
        return [_fallback_embed_text(text) for text in texts]


def embed_texts_sync(texts: list[str], model: str | None = None) -> list[list[float]]:
    resolved_model = str(model or get_embedding_model()).strip() or get_embedding_model()
    try:
        client = _get_sync_client()
        response = client.embeddings.create(model=resolved_model, input=texts)
        return [item.embedding for item in response.data]
    except Exception:
        return [_fallback_embed_text(text) for text in texts]
