import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI


PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(ENV_FILE)


def _load_llm_settings() -> tuple[str, str, str]:
    """Load and validate the LLM settings from the project-level .env file."""
    api_key = os.getenv("API_KEY")
    base_url = os.getenv("BASE_URL", "https://api.deepseek.com")
    model = os.getenv("MODEL", "deepseek-chat")

    if not api_key:
        raise RuntimeError("缺少 API_KEY，请检查项目根目录下的 .env 文件")

    return api_key, base_url, model


def get_llm_client() -> OpenAI:
    """Create an OpenAI-compatible client using the configured provider."""
    api_key, base_url, _ = _load_llm_settings()
    return OpenAI(api_key=api_key, base_url=base_url)


def chat(
    user_prompt: str,
    *,
    system_prompt: str = "你是一名专业、准确、友善的电商售后客服。",
    temperature: float = 0.2,
) -> str:
    """Send one non-streaming chat request and return the response text."""
    client = get_llm_client()
    _, _, model = _load_llm_settings()

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        stream=False,
    )

    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("LLM 返回了空内容")

    return content.strip()


def chat_json(
    user_prompt: str,
    *,
    system_prompt: str,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """Send one chat request and parse a JSON object response."""
    client = get_llm_client()
    _, _, model = _load_llm_settings()

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        response_format={"type": "json_object"},
        stream=False,
    )

    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("LLM 返回了空内容")

    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise RuntimeError("LLM 返回的 JSON 顶层结构不是对象")

    return parsed
