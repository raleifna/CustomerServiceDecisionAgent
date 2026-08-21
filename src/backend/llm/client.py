import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from openai import OpenAI

from src.backend.llm.telemetry import telemetry


PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(ENV_FILE)

# Every call goes through the OpenAI-compatible API — a single external
# dependency that can hang or go down. Bound how long we wait per call
# (timeout), how many times the SDK retries transient failures on its own
# (max_retries), and — since the SDK has no notion of "this provider is
# down right now" across calls — a small in-process circuit breaker below,
# so a struggling API degrades to fast failures instead of every request
# piling up behind a slow/hanging one.
_REQUEST_TIMEOUT_SECONDS = 15.0
_SDK_MAX_RETRIES = 2


class _CircuitBreaker:
    """Per-process, in-memory only — good enough for a single backend
    instance. A multi-instance deployment would need this state shared
    (e.g. Redis) to actually protect the fleet; out of scope here.
    """

    def __init__(self, failure_threshold: int = 5, cooldown_seconds: float = 30.0):
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    def before_call(self) -> None:
        with self._lock:
            if self._opened_at is None:
                return
            if time.monotonic() - self._opened_at < self._cooldown_seconds:
                raise RuntimeError(
                    "LLM 服务暂时不可用（连续失败次数过多，熔断中），请稍后重试"
                )
            # Cooldown elapsed: let this call through as a half-open trial.
            # record_success()/record_failure() below decide whether to
            # close the circuit again or keep it open.

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._opened_at = None

    def record_failure(self) -> bool:
        """Returns True the moment this failure trips the breaker open."""
        with self._lock:
            self._consecutive_failures += 1
            if (
                self._consecutive_failures >= self._failure_threshold
                and self._opened_at is None
            ):
                self._opened_at = time.monotonic()
                return True
            return False


_circuit = _CircuitBreaker()


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
    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=_REQUEST_TIMEOUT_SECONDS,
        max_retries=_SDK_MAX_RETRIES,
    )


def _create_completion(client: OpenAI, **kwargs: Any) -> Any:
    """Route every actual API call through the circuit breaker and telemetry."""
    _circuit.before_call()
    started = time.monotonic()
    try:
        response = client.chat.completions.create(**kwargs)
    except Exception:
        latency_ms = (time.monotonic() - started) * 1000
        telemetry.record(success=False, latency_ms=latency_ms)
        if _circuit.record_failure():
            telemetry.record_circuit_trip()
        raise
    else:
        latency_ms = (time.monotonic() - started) * 1000
        telemetry.record(success=True, latency_ms=latency_ms)
        _circuit.record_success()
        return response


def chat(
    user_prompt: str,
    *,
    system_prompt: str = "你是一名专业、准确、友善的电商售后客服。",
    temperature: float = 0.2,
) -> str:
    """Send one non-streaming chat request and return the response text."""
    client = get_llm_client()
    _, _, model = _load_llm_settings()

    response = _create_completion(
        client,
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

    response = _create_completion(
        client,
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


def chat_with_tools(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_executor: Callable[[str, dict[str, Any]], Any],
    *,
    system_prompt: str,
    temperature: float = 0.2,
    max_rounds: int = 4,
) -> str:
    """Run a bounded tool-calling loop and return the final assistant text.

    tool_executor(name, arguments) must return a JSON-serializable result and
    do the actual lookup itself — tool execution is plain Python code, not
    LLM output, so it stays deterministic and auditable. The LLM only ever
    decides *which* tool to call and how to phrase the final answer.
    """
    client = get_llm_client()
    _, _, model = _load_llm_settings()

    conversation: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        *messages,
    ]

    for _ in range(max_rounds):
        response = _create_completion(
            client,
            model=model,
            messages=conversation,
            tools=tools,
            temperature=temperature,
            stream=False,
        )
        message = response.choices[0].message

        if not message.tool_calls:
            content = message.content
            if not content:
                raise RuntimeError("LLM 返回了空内容")
            return content.strip()

        conversation.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        },
                    }
                    for tool_call in message.tool_calls
                ],
            }
        )

        for tool_call in message.tool_calls:
            try:
                arguments = json.loads(tool_call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            result = tool_executor(tool_call.function.name, arguments)
            conversation.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

    raise RuntimeError("工具调用轮数超过上限，仍未得到最终回复")
