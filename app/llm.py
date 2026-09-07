"""LLM factory: Ollama local or any OpenAI-compatible cloud provider."""
from dataclasses import dataclass

from llama_index.embeddings.ollama import OllamaEmbedding

from app.config import (
    EMBED_MODEL,
    OLLAMA_URL,
    TEMPERATURE,
    get_provider_config,
    llm_request_timeout,
)


@dataclass
class CompletionResponse:
    text: str


class OpenAICompatibleLLM:
    """Direct OpenAI SDK client — accepts any model id (OpenRouter, Gemini, OmniRoute, …)."""

    def __init__(self, model, api_key, base_url, temperature, timeout, default_headers=None):
        from openai import OpenAI

        self.model = model
        self.temperature = temperature
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,
            default_headers=default_headers or {},
        )

    def complete(self, prompt):
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self.temperature,
        )
        text = response.choices[0].message.content or ""
        return CompletionResponse(text=text.strip())


def get_llm(provider=None, model=None):
    cfg = get_provider_config(provider=provider, model_override=model)
    timeout = llm_request_timeout(cfg["provider"])

    if cfg["provider"] == "ollama":
        from llama_index.llms.ollama import Ollama

        print(f"--- LLM: ollama model={cfg['model']} timeout={timeout}s ---")
        return Ollama(
            model=cfg["model"],
            base_url=cfg["base_url"],
            temperature=TEMPERATURE,
            request_timeout=timeout,
        )

    print(
        f"--- LLM: {cfg['provider']} model={cfg['model']} base={cfg['base_url']} timeout={timeout}s ---"
    )
    return OpenAICompatibleLLM(
        model=cfg["model"],
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
        temperature=TEMPERATURE,
        timeout=timeout,
        default_headers=cfg.get("headers") or {},
    )


def get_embed_model():
    return OllamaEmbedding(model_name=EMBED_MODEL, base_url=OLLAMA_URL)
