"""App configuration from environment."""
from __future__ import annotations

import os

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

CLOUD_PROVIDERS = {"openai", "openrouter", "gemini", "omnirouter", "groq", "anthropic", "deepseek"}

PROVIDER_META = {
    "ollama": {
        "label": "Local Ollama",
        "key_env": None,
        "model_env": "LLM_MODEL",
        "base_env": "OLLAMA_URL",
        "default_base": "http://localhost:11434",
        "default_model": "qwen3.5:9b",
        "suggested_models": ["qwen3.5:9b", "qwen2.5:7b", "gemma3:27b"],
    },
    "openai": {
        "label": "OpenAI",
        "key_env": "OPENAI_API_KEY",
        "model_env": "OPENAI_MODEL",
        "base_env": "OPENAI_BASE_URL",
        "default_base": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "suggested_models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"],
    },
    "openrouter": {
        "label": "OpenRouter",
        "key_env": "OPENROUTER_API_KEY",
        "model_env": "OPENROUTER_MODEL",
        "base_env": "OPENROUTER_BASE_URL",
        "default_base": "https://openrouter.ai/api/v1",
        "default_model": "google/gemini-2.5-flash",
        "suggested_models": [
            "google/gemini-2.5-flash",
            "anthropic/claude-3.5-sonnet",
            "openai/gpt-4o-mini",
        ],
    },
    "gemini": {
        "label": "Google Gemini",
        "key_env": "GEMINI_API_KEY",
        "model_env": "GEMINI_MODEL",
        "base_env": "GEMINI_BASE_URL",
        "default_base": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-2.5-flash",
        "suggested_models": ["gemini-2.5-flash", "gemini-2.0-flash"],
    },
    "omnirouter": {
        "label": "OmniRoute",
        "key_env": "OMNIROUTER_API_KEY",
        "model_env": "OMNIROUTER_MODEL",
        "base_env": "OMNIROUTER_BASE_URL",
        "default_base": "http://localhost:20128/v1",
        "default_model": "auto",
        "suggested_models": ["auto", "google/gemini-2.5-flash", "anthropic/claude-3.5-sonnet"],
        "allow_local_no_key": True,
    },
    "groq": {
        "label": "Groq",
        "key_env": "GROQ_API_KEY",
        "model_env": "GROQ_MODEL",
        "base_env": "GROQ_BASE_URL",
        "default_base": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "suggested_models": ["llama-3.3-70b-versatile", "mixtral-8x7b-32768"],
    },
    "anthropic": {
        "label": "Anthropic Claude",
        "key_env": "ANTHROPIC_API_KEY",
        "model_env": "ANTHROPIC_MODEL",
        "base_env": "ANTHROPIC_BASE_URL",
        "default_base": "https://api.anthropic.com/v1",
        "default_model": "claude-3-5-sonnet-20241022",
        "suggested_models": ["claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022"],
    },
    "deepseek": {
        "label": "DeepSeek",
        "key_env": "DEEPSEEK_API_KEY",
        "model_env": "DEEPSEEK_MODEL",
        "base_env": "DEEPSEEK_BASE_URL",
        "default_base": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
        "suggested_models": ["deepseek-chat", "deepseek-reasoner"],
    },
}


def reload_env():
    """Re-read .env so provider/key changes apply without restarting the server."""
    if load_dotenv:
        load_dotenv(override=True)


def _env(key, default=""):
    value = os.getenv(key)
    return value if value not in (None, "") else default


def _build_provider_entry(provider_id):
    meta = PROVIDER_META[provider_id]
    model = _env(meta["model_env"], meta["default_model"])
    base_url = _env(meta["base_env"], meta["default_base"])
    api_key = _env(meta["key_env"], "") if meta["key_env"] else ""

    headers = {}
    if provider_id == "openrouter":
        headers = {
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "Educator Assessment",
        }

    return {
        "id": provider_id,
        "label": meta["label"],
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "headers": headers,
        "suggested_models": meta["suggested_models"],
    }


def _is_provider_available(provider_id, entry):
    meta = PROVIDER_META[provider_id]
    if provider_id == "ollama":
        return True
    if entry["api_key"]:
        return True
    if meta.get("allow_local_no_key") and "localhost" in (entry["base_url"] or ""):
        return True
    return False


def list_available_providers():
    """Return providers that have an active key (or local Ollama / local OmniRoute)."""
    reload_env()
    providers = []
    default_provider = _env("LLM_PROVIDER", "ollama").lower()

    for provider_id in PROVIDER_META:
        entry = _build_provider_entry(provider_id)
        if not _is_provider_available(provider_id, entry):
            continue
        providers.append({
            "id": entry["id"],
            "label": entry["label"],
            "model": entry["model"],
            "models": _unique_models(entry["model"], entry["suggested_models"]),
            "is_default": provider_id == default_provider,
        })

    return providers


def _unique_models(default_model, suggested):
    seen = set()
    ordered = []
    for name in [default_model, *suggested]:
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def get_provider_config(provider=None, model_override=None):
    reload_env()
    provider_id = (provider or _env("LLM_PROVIDER", "ollama")).lower()
    if provider_id not in PROVIDER_META:
        raise ValueError(f"Unknown provider: {provider_id}")

    entry = _build_provider_entry(provider_id)
    if not _is_provider_available(provider_id, entry):
        raise ValueError(
            f"Provider '{provider_id}' is not configured. "
            f"Uncomment and set {PROVIDER_META[provider_id]['key_env']} in .env"
        )

    model = (model_override or entry["model"] or PROVIDER_META[provider_id]["default_model"]).strip()
    if not model:
        raise ValueError(f"No model selected for provider '{provider_id}'")

    api_key = entry["api_key"] or "local"
    return {
        "provider": provider_id,
        "api_key": api_key,
        "base_url": entry["base_url"],
        "model": model,
        "headers": entry["headers"],
    }


# Module-level defaults (startup / embeddings)
reload_env()
LLM_PROVIDER = _env("LLM_PROVIDER", "ollama").lower()
LLM_MODEL = _env("LLM_MODEL", "qwen3.5:9b")
MODEL_NAME = LLM_MODEL
EMBED_MODEL = _env("EMBED_MODEL", "nomic-embed-text")
OLLAMA_URL = _env("OLLAMA_URL", "http://localhost:11434")
TEMPERATURE = float(_env("TEMPERATURE", "0.1"))
TOP_K = int(_env("TOP_K", "3"))
REQUEST_TIMEOUT = float(_env("REQUEST_TIMEOUT", "600.0"))
# Extra headroom only for local Ollama (e.g. 6GB VRAM). Cloud/gateway providers keep REQUEST_TIMEOUT.
LOCAL_REQUEST_TIMEOUT = float(_env("LOCAL_REQUEST_TIMEOUT", "1800.0"))

VECTOR_DB_PATH = "./data/index"
CHROMA_PATH = os.path.join(VECTOR_DB_PATH, "chroma")
CHROMA_COLLECTION = "course_materials"
STUDENT_DIR = "./data/students"
REPORT_DIR = "./data/reports"
ASSIGNMENT_DIR = "./data/assignments"
RUBRIC_DIR = "./data/rubric"
TEXTBOOK_DIR = "./data/textbook"
SLIDES_DIR = "./data/slides"
STATIC_DIR = "./static"
ARCHIVE_DIR = "./data/archive"
DB_PATH = "./data/app.db"
DISPLAY_TZ_OFFSET = "+06:30"
SESSION_COOKIE = "eas_session"
SESSION_DAYS = 7
COURSE_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".txt", ".doc", ".pptx", ".ppt"}

# Slides + textbook. Retrieval uses slides first, then textbook for detail.
INDEX_INCLUDE_TEXTBOOK = _env("INDEX_INCLUDE_TEXTBOOK", "true").lower() in ("1", "true", "yes")
INDEX_INPUT_DIRS = [SLIDES_DIR] + ([TEXTBOOK_DIR] if INDEX_INCLUDE_TEXTBOOK else [])
CHUNK_SIZE = int(_env("CHUNK_SIZE", "800"))
CHUNK_OVERLAP = int(_env("CHUNK_OVERLAP", "80"))
EMBED_BATCH_SIZE = int(_env("EMBED_BATCH_SIZE", "16"))
SLIDES_TOP_K = int(_env("SLIDES_TOP_K", "3"))
TEXTBOOK_TOP_K = int(_env("TEXTBOOK_TOP_K", "2"))
GRADING_POLICY_PATH = "./data/grading_policy.txt"
MAX_CHARS_PER_ANSWER = int(_env("MAX_CHARS_PER_ANSWER", "12000"))
MAX_SUBMISSION_CHARS = int(_env("MAX_SUBMISSION_CHARS", "500000"))
GRADING_CHUNK_OVERLAP = int(_env("GRADING_CHUNK_OVERLAP", "400"))
WORD_COUNT_MIN = int(_env("WORD_COUNT_MIN", "1200"))
WORD_COUNT_TARGET_MAX = int(_env("WORD_COUNT_TARGET_MAX", "1500"))
WORD_COUNT_HARD_MAX = int(_env("WORD_COUNT_HARD_MAX", "2000"))
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".txt", ".doc"}


def llm_request_timeout(provider: str | None) -> float:
    """Per-request HTTP timeout. Extended only for local Ollama."""
    if (provider or "").strip().lower() == "ollama":
        return LOCAL_REQUEST_TIMEOUT
    return REQUEST_TIMEOUT


def job_hard_timeout_seconds(provider: str | None) -> float:
    """Whole-job cap. Slightly above HTTP timeout so the client error can surface first."""
    return llm_request_timeout(provider) + 15.0


def get_active_provider_config():
    """Backward-compatible helper used when no per-request override is supplied."""
    cfg = get_provider_config()
    if cfg["provider"] == "ollama":
        return None
    return cfg
