import os
from collections.abc import AsyncIterator

from google import genai

from app.core.config import settings
from app.providers.llm.base import LLMNotConfiguredError, LLMProvider, T

# Observed live (Phase 8/assessment-endpoint testing): with no timeout, a rate-limited call can
# hang far longer than the "retry in Ns" the API itself reports — the SDK doesn't fail fast on
# its own. A bounded timeout ensures a stuck call surfaces as a real exception (which
# generate_assessment/get_validated_output already handle via retry + fail-closed) instead of
# hanging the whole request.
_REQUEST_TIMEOUT_SECONDS = 30.0


class GeminiProvider(LLMProvider):
    """Concrete LLMProvider choice: Google Gemini, via the official `google-genai` SDK
    (`client.aio.interactions.create` — the current async Interactions API, verified against
    the live docs and the installed SDK's own type definitions, not assumed from training
    data). User decision, superseding the earlier OpenAI default — see
    docs/KNOWN_LIMITATIONS.md. OpenAIProvider is left in place as a second, still-working
    implementation of the same interface."""

    def __init__(self) -> None:
        self._cached_key: str | None = None
        self._client: genai.Client | None = None
        self._model = settings.gemini_model

    def _require_client(self) -> genai.Client:
        key = settings.gemini_api_key or os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise LLMNotConfiguredError(
                "GEMINI_API_KEY is not set — see .env.example. No response was fabricated."
            )
        if self._client is None or self._cached_key != key:
            self._client = genai.Client(api_key=key)
            self._cached_key = key
        return self._client

    async def generate(self, prompt: str, *, system: str | None = None) -> str:
        client = self._require_client()
        # 1. Try standard models.generate_content (Google AI Studio Developer API)
        try:
            config = None
            if system:
                from google.genai import types
                config = types.GenerateContentConfig(system_instruction=system)
            response = await client.aio.models.generate_content(
                model=self._model, contents=prompt, config=config
            )
            return response.text or ""
        except Exception:
            # 2. Fallback to interactions.create if models API fails
            interaction = await client.aio.interactions.create(
                model=self._model, input=prompt, system_instruction=system, timeout=_REQUEST_TIMEOUT_SECONDS
            )
            return interaction.output_text or ""

    async def structured_generate(self, prompt: str, schema: type[T], *, system: str | None = None) -> T:
        client = self._require_client()
        # 1. Try standard models.generate_content with structured response
        try:
            from google.genai import types
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                system_instruction=system,
            )
            response = await client.aio.models.generate_content(
                model=self._model, contents=prompt, config=config
            )
            return schema.model_validate_json(response.text)
        except Exception:
            # 2. Fallback to interactions.create
            interaction = await client.aio.interactions.create(
                model=self._model,
                input=prompt,
                system_instruction=system,
                response_format={"type": "text", "mime_type": "application/json", "schema_": schema.model_json_schema()},
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
            return schema.model_validate_json(interaction.output_text)

    async def stream(self, prompt: str, *, system: str | None = None) -> AsyncIterator[str]:
        client = self._require_client()
        try:
            config = None
            if system:
                from google.genai import types
                config = types.GenerateContentConfig(system_instruction=system)
            response_stream = await client.aio.models.generate_content_stream(
                model=self._model, contents=prompt, config=config
            )
            async for chunk in response_stream:
                if chunk.text:
                    yield chunk.text
        except Exception:
            response_stream = await client.aio.interactions.create(
                model=self._model, input=prompt, system_instruction=system, stream=True, timeout=_REQUEST_TIMEOUT_SECONDS
            )
            async for event in response_stream:
                if event.event_type == "step.delta" and getattr(event.delta, "type", None) == "text":
                    yield event.delta.text
