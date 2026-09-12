from __future__ import annotations

import logging
import time
from typing import Any

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from financial_ai.config import get_settings
from financial_ai.utils.exceptions import EmbeddingError

logger = logging.getLogger(__name__)

_OPENAI_COSTS: dict[str, float] = {
    "text-embedding-3-large": 0.13,
    "text-embedding-3-small": 0.02,
    "text-embedding-ada-002": 0.10,
}


class EmbeddingProvider:
    @property
    def dimensions(self) -> int:
        raise NotImplementedError

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    async def embed_batch_async(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


# OPENAI PROVIDER
class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(self) -> None:
        from openai import AsyncOpenAI, OpenAI

        settings = get_settings()

        if not settings.OPENAI_API_KEY:
            raise EmbeddingError(
                "OPENAI_API_KEY is required for OpenAI embedding provider. "
                "Set it in .env or switch EMBEDDING_PROVIDER=local."
            )
        api_key = settings.OPENAI_API_KEY.get_secret_value()
        self._model = settings.EMBEDDING_MODEL
        self._dimensions = settings.EMBEDDING_DIMENSIONS
        self._client = OpenAI(api_key=api_key)
        self._async_client = AsyncOpenAI(api_key=api_key)

        logger.info(
            "OpenAI embedding provider initialized - model=%s dims=%d",
            self._model,
            self._dimensions,
        )

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @retry(
        retry=retry_if_exception_type(EmbeddingError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        try:
            t0 = time.monotonic()
            response = self._client.embeddings.create(
                model=self._model,
                input=texts,
                dimensions=self._dimensions,
            )
            latency = time.monotonic() - t0

            embeddings = [item.embedding for item in response.data]
            tokens_used = response.usage.total_tokens
            cost = self._estimate_cost(tokens_used)

            logger.info(
                "OpenAI embed_batch - texts=%d tokens=%d cost=$%.4f latency=%.2fs",
                len(texts),
                tokens_used,
                cost,
                latency,
            )
            self._validate_dimensions(embeddings)
            return embeddings

        except Exception as exc:
            raise EmbeddingError(
                f"OpenAI embedding failed for batch of {len(texts)} texts: {exc}"
            ) from exc

    @retry(
        retry=retry_if_exception_type(EmbeddingError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    async def embed_batch_async(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        try:
            t0 = time.monotonic()
            response = await self._async_client.embeddings.create(
                model=self._model, input=texts, dimensions=self._dimensions
            )
            latency = time.monotonic() - t0

            embeddings = [item.embedding for item in response.data]
            tokens_used = response.usage.total_tokens
            cost = self._estimate_cost(tokens_used)

            logger.info(
                "OpenI embed_batch_async - texts=%d cost=%.4f latency=%.2fs",
                len(texts),
                tokens_used,
                cost,
                latency,
            )
            self._validate_dimensions(embeddings)
            return embeddings

        except Exception as exc:
            raise EmbeddingError(
                f"OpenAI async embedding failed for batch of {len(texts)} texts: {exc}"
            ) from exc

    # INTERNAL
    def _estimate_cost(self, tokens: int) -> float:
        rate = _OPENAI_COSTS.get(self._model, 0.13)
        return (tokens / 1_000_000) * rate

    def _validate_dimensions(self, embeddings: list[list[float]]) -> None:
        if not embeddings:
            return
        actual = len(embeddings[0])
        if actual != self._dimensions:
            raise EmbeddingError(
                f"Dimension mismatch: expected {self._dimensions}, "
                f"got {actual} from OpenAI. "
                f"Check EMBEDDING_DIMENSIONS in .env"
            )


# LOCAL EMBEDDING
class LocalEmbeddingProvider(EmbeddingProvider):
    def __init__(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise ImportError(
                "sentence-transformers is required for local embeddings. "
                "Install it with: pip install sentence-transformers"
            ) from e

        settings = get_settings()

        self._model_name = settings.EMBEDDING_MODEL
        self._dimensions = settings.EMBEDDING_DIMENSIONS

        logger.info(
            "Loading local embedding model '%s' (this may take a moment)...",
            self._model_name,
        )
        try:
            self._model = SentenceTransformer(self._model_name)
        except Exception as exc:
            raise EmbeddingError(f"Failed to load model '{self._model_name}': {exc}") from exc

        logger.info(
            "Local embedding provider ready - model=%s dims=%d", self._model_name, self._dimensions
        )

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        try:
            t0 = time.monotonic()
            raw = self._model.encode(
                texts, convert_to_numpy=True, show_progress_bar=False, batch_size=32
            )
            latency = time.monotonic() - t0

            embeddings = [vec.tolist() for vec in raw]

            logger.info("Local embed_batch - texts=%d latency=%.2fs", len(texts), latency)
            return embeddings

        except Exception as exc:
            raise EmbeddingError(
                f"Local embedding failed for batch of {len(texts)} texts: {exc}"
            ) from exc

    async def embed_batch_async(self, texts: list[str]) -> list[list[float]]:
        import asyncio

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.embed_batch, texts)


# EMBEDDING CLIENT
class EmbeddingClient:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._provider = self._build_provider()

    def _build_provider(self) -> EmbeddingProvider:
        settings = self._settings
        provider_name = settings.EMBEDDING_PROVIDER

        if provider_name == "openai":
            if not settings.OPENAI_API_KEY:
                if settings.APP_ENV not in ("development", "testing"):
                    logger.warning(
                        "OPENAI_API_KEY not set - falling back to local "
                        "embedding provider for %s environment",
                        settings.APP_ENV,
                    )
                    return LocalEmbeddingProvider()
                else:
                    raise EmbeddingError("OPENAI_API_KEY is required in production. Set in .env")
            return OpenAIEmbeddingProvider()
        return LocalEmbeddingProvider()

    @property
    def dimensions(self) -> int:
        return self._provider.dimensions

    @property
    def provider_name(self) -> str:
        return type(self._provider).__name__

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        settings = self._settings
        batch_size = settings.EMBEDDING_BATCH_SIZE
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (len(texts) + batch_size - 1) // batch_size

            logger.debug("Embedding batch %d/%d (%d texts)", batch_num, total_batches, len(batch))

            embeddings = await self._provider.embed_batch_async(batch)
            all_embeddings.extend(embeddings)

        return all_embeddings

    async def embed_chunks(self, chunks: list[Any]) -> list[Any]:
        if not chunks:
            return []

        texts = [c.chunk_text for c in chunks]
        embeddings = await self.embed_texts(texts)

        if len(embeddings) != len(chunks):
            raise EmbeddingError(
                f"Embedding count mismatch: got {len(embeddings)} "
                f"embeddings for {len(chunks)} chunks"
            )

        for chunk, embedding in zip(chunks, embeddings, strict=False):
            chunk.embedding = embedding

        logger.info("Populated embeddings for %d chunks via %s", len(chunks), self.provider_name)
        return chunks

    async def embed_query(self, query: str) -> list[float]:
        results = await self.embed_texts([query])
        return results[0]
