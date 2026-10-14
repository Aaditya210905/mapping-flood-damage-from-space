"""One optional provider, bounded requests and deliberately sanitized failures."""
import os
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
from groq import Groq, APIConnectionError, APITimeoutError, APIStatusError
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    api_key: SecretStr = Field(default=SecretStr(""), exclude=True, repr=False)
    model: str = Field(default="openai/gpt-oss-120b", pattern=r"^[A-Za-z0-9_./:-]{1,120}$")
    base_url: str = Field(default="https://api.groq.com", exclude=True, repr=False)
    timeout_seconds: float = Field(default=10, ge=1, le=30)
    budget_seconds: float = Field(default=30, ge=3, le=60)
    max_completion_tokens: int = Field(default=4096, ge=512, le=8192)

    @field_validator("base_url")
    @classmethod
    def valid_endpoint(cls, value):
        if value:
            parsed = urlsplit(value)
            if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("Invalid LLM endpoint configuration")
            if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
                raise ValueError("Remote LLM endpoints require HTTPS")
        return value

    @classmethod
    def from_env(cls):
        return cls(enabled=os.getenv("LLM_ENABLED", "false").lower() in ("true", "1", "yes"),
                   api_key=SecretStr(os.getenv("LLM_API_KEY", "") or os.getenv("GROQ_API_KEY", "")),
                   model=os.getenv("LLM_MODEL", "openai/gpt-oss-120b"),
                   base_url=os.getenv("LLM_BASE_URL") or "https://api.groq.com",
                   timeout_seconds=os.getenv("LLM_TIMEOUT_SECONDS", "10"),
                   budget_seconds=os.getenv("LLM_BUDGET_SECONDS", "30"),
                   max_completion_tokens=os.getenv("LLM_MAX_COMPLETION_TOKENS", "4096"))

    @property
    def ready(self):
        return self.enabled and bool(self.api_key.get_secret_value().strip())


def provider_status():
    try:
        config = LLMConfig.from_env()
        return {"provider": "groq", "enabled": config.enabled, "configured": config.ready,
                "model": config.model if config.enabled else None,
                "fallback_available": True, "status": "configured" if config.ready else "deterministic"}
    except ValueError:
        return {"provider": "groq", "enabled": False, "configured": False,
                "fallback_available": True, "status": "invalid_configuration"}


class ProviderFailure(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class ToolCall(SimpleNamespace):
    def model_dump(self, **kwargs):
        return vars(self)


def _messages(instructions, items):
    """Convert the small internal transcript to Groq chat-completion messages."""
    messages = [{"role": "system", "content": instructions}]
    for item in items:
        if item.get("type") == "function_call":
            call = {"id": item["call_id"], "type": "function", "function": {"name": item["name"], "arguments": item["arguments"]}}
            if messages[-1].get("role") == "assistant" and "tool_calls" in messages[-1]:
                messages[-1]["tool_calls"].append(call)
            else:
                messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
        elif item.get("type") == "function_call_output":
            messages.append({"role": "tool", "tool_call_id": item["call_id"], "content": item["output"]})
        else:
            messages.append(item)
    return messages


class GroqProvider:
    def __init__(self, config):
        self.config = config
        self.usage = {"provider_requests": 0, "prompt_tokens": None, "completion_tokens": None}
        self.deadline = time.monotonic() + config.budget_seconds
        try:
            self.client = Groq(api_key=config.api_key.get_secret_value(), base_url=config.base_url,
                               timeout=config.timeout_seconds, max_retries=0)
        except Exception:
            raise ProviderFailure("provider_unavailable") from None

    def create(self, *, instructions, items, tools, schema, tool_choice="auto"):
        remaining = self.deadline - time.monotonic()
        if remaining < 1:
            raise ProviderFailure("timeout")
        timeout = min(self.config.timeout_seconds, remaining)
        parameters = {"model": self.config.model, "messages": _messages(instructions, items),
                      "temperature": 0, "max_completion_tokens": self.config.max_completion_tokens if tool_choice == "none" else 1024, "store": False}
        # Groq does not combine local tool use with strict structured outputs.
        if tool_choice == "none":
            parameters["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "grounded_flood_answer", "strict": True, "schema": schema}}
        else:
            parameters.update(tools=[{"type": "function", "function": {
                "name": t["name"], "description": t["description"], "parameters": t["parameters"]}} for t in tools],
                tool_choice=tool_choice, parallel_tool_calls=True)
            if tool_choice == "required" and len(tools) == 1:
                parameters["tool_choice"] = {"type": "function", "function": {"name": tools[0]["name"]}}
                parameters["parallel_tool_calls"] = False
        try:
            self.usage["provider_requests"] += 1
            response = self.client.with_options(timeout=httpx.Timeout(timeout, connect=min(5, timeout))).chat.completions.create(**parameters)
        except (APITimeoutError, TimeoutError):
            raise ProviderFailure("timeout") from None
        except APIConnectionError:
            raise ProviderFailure("provider_unavailable") from None
        except APIStatusError as error:
            reason = "rate_limited" if error.status_code == 429 else "request_too_large" if error.status_code == 413 else "credentials_rejected" if error.status_code in (401, 403) else "provider_unavailable"
            if error.status_code == 400:
                try:
                    if error.response.json().get('error', {}).get('code') == 'tool_use_failed':
                        reason = 'invalid_tool_call'
                except (ValueError, AttributeError):
                    pass
            raise ProviderFailure(reason) from None
        except Exception:
            raise ProviderFailure("provider_unavailable") from None
        usage = getattr(response, "usage", None)
        for key in ("prompt_tokens", "completion_tokens"):
            value = getattr(usage, key, None)
            if type(value) is int and value >= 0:
                self.usage[key] = (self.usage[key] or 0) + value
        if not isinstance(getattr(response, "choices", None), list) or not response.choices:
            raise ProviderFailure("malformed_response")
        choice = response.choices[0]
        if getattr(choice, "finish_reason", None) not in ("stop", "tool_calls"):
            raise ProviderFailure("incomplete_response")
        try:
            calls = [ToolCall(type="function_call", name=c.function.name, arguments=c.function.arguments, call_id=c.id)
                     for c in (choice.message.tool_calls or []) if c.type == "function"]
            return SimpleNamespace(output=calls, output_text=choice.message.content or "")
        except (TypeError, AttributeError):
            raise ProviderFailure("malformed_response") from None

    def close(self):
        try:
            self.client.close()
        except Exception:
            pass  # Cleanup must never replace a validated answer or safe fallback.
