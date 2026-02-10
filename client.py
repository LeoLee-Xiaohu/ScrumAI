"""LLM client supporting OpenAI-compatible and Google Genai backends.

Mirrors the dual-provider setup in scrumai-forge:
- src/lib/openai-client.ts (OpenAI-compatible, used for scoring & brainstorm)
- Google Genai (existing provider in this repo)
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Protocol

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

logger = logging.getLogger(__name__)


class LLMClient(Protocol):
    """Protocol for LLM client implementations."""

    def chat(self, system_prompt: str, messages: list[dict[str, str]]) -> str:
        """Send a chat request and return the raw text response."""
        ...


class OpenAICompatibleClient:
    """OpenAI-compatible API client.

    Supports any OpenAI-compatible endpoint (OpenAI, DeepSeek, Groq, Together, etc.)
    Mirrors: src/lib/openai-client.ts in scrumai-forge
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        from openai import OpenAI

        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.base_url = base_url or os.getenv(
            "OPENAI_BASE_URL", "https://api.openai.com/v1"
        )
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o")
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def chat(self, system_prompt: str, messages: list[dict[str, str]]) -> str:
        all_messages = [{"role": "system", "content": system_prompt}, *messages]
        response = self.client.chat.completions.create(
            model=self.model,
            messages=all_messages,  # type: ignore[arg-type]
            max_tokens=4096,
        )
        content = response.choices[0].message.content
        return content or ""


class GoogleGenaiClient:
    """Google Genai client (existing provider)."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        from google import genai

        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.0-flash-exp")
        self.client = genai.Client(api_key=self.api_key)

    def chat(self, system_prompt: str, messages: list[dict[str, str]]) -> str:
        # Combine system prompt and messages into a single content string
        parts = [system_prompt]
        for msg in messages:
            role = msg["role"]
            parts.append(f"\n\n[{role.upper()}]: {msg['content']}")
        combined = "\n".join(parts)

        response = self.client.models.generate_content(
            model=self.model, contents=combined
        )
        return response.text or ""


def get_client(provider: str | None = None) -> LLMClient:
    """Create an LLM client based on provider name or environment.

    Priority: explicit provider > LLM_PROVIDER env var > auto-detect from available keys.
    """
    provider = provider or os.getenv("LLM_PROVIDER", "").lower()

    if provider == "openai":
        return OpenAICompatibleClient()
    if provider in ("gemini", "google"):
        return GoogleGenaiClient()

    # Auto-detect
    if os.getenv("OPENAI_API_KEY"):
        return OpenAICompatibleClient()
    if os.getenv("GEMINI_API_KEY"):
        return GoogleGenaiClient()

    raise ValueError(
        "No LLM provider configured. Set LLM_PROVIDER=openai or LLM_PROVIDER=gemini, "
        "and provide the corresponding API key (OPENAI_API_KEY or GEMINI_API_KEY)."
    )


def load_prompt(name: str) -> str:
    """Load a prompt template from the prompts/ directory.

    Args:
        name: Prompt filename without extension (e.g., "brainstorm", "issue_scoring")

    Returns:
        The prompt template string.
    """
    prompt_dir = Path(__file__).parent / "prompts"
    prompt_file = prompt_dir / f"{name}.md"
    if not prompt_file.exists():
        raise FileNotFoundError(f"Prompt not found: {prompt_file}")
    return prompt_file.read_text(encoding="utf-8")


def extract_json(text: str) -> dict | None:
    """Extract JSON from LLM response (handles code blocks or raw JSON).

    Mirrors: parseScoreResponse() in src/lib/issue-scorer.ts
    """
    # Try ```json code block first
    json_match = re.search(r"```json\s*([\s\S]*?)```", text)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try raw JSON object
    json_match = re.search(r"\{[\s\S]*\}", text)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            pass

    return None


def parse_structured_response[T: BaseModel](text: str, model_class: type[T]) -> T:
    """Parse LLM response into a Pydantic model.

    Args:
        text: Raw LLM response text
        model_class: Pydantic model class to validate against

    Returns:
        Validated Pydantic model instance

    Raises:
        ValueError: If JSON cannot be extracted or fails validation
    """
    data = extract_json(text)
    if data is None:
        raise ValueError(f"No valid JSON found in response:\n{text[:500]}")
    return model_class.model_validate(data)
