"""One optional provider, bounded requests and deliberately sanitized failures."""
import os
import time
import copy
import logging
from typing import Literal
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
from groq import Groq, APIConnectionError, APITimeoutError, APIStatusError
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

logger = logging.getLogger('flood_lens.copilot')


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    api_key: SecretStr = Field(default=SecretStr(""), exclude=True, repr=False)
    model: str = Field(default="openai/gpt-oss-120b", pattern=r"^[A-Za-z0-9_./:-]{1,120}$")
    base_url: str = Field(default="https://api.groq.com", exclude=True, repr=False)
    timeout_seconds: float = Field(default=25, ge=1, le=30)
    budget_seconds: float = Field(default=60, ge=3, le=60)
    max_completion_tokens: int = Field(default=4096, ge=512, le=8192)
    reasoning_effort: Literal['none','default','low','medium','high'] | None = None
    application_retrieval: bool = True
    query_planning: Literal['semantic', 'local'] = 'semantic'
    query_max_completion_tokens: int = Field(default=1024, ge=128, le=2048)
    retrieval_rounds: int = Field(default=2, ge=1, le=4)
    max_tool_calls: int = Field(default=6, ge=1, le=12)
    retrieval_page_size: int = Field(default=5, ge=1, le=5)
    max_rate_limit_retries: int = Field(default=1, ge=0, le=2)
    max_retry_wait_seconds: float = Field(default=3, ge=0, le=10)
    max_list_records: int = Field(default=1000, ge=1, le=5000)
    response_cache_ttl_seconds:float=Field(default=300,ge=0,le=3600)
    response_cache_entries:int=Field(default=64,ge=1,le=512)
    max_concurrent_requests:int=Field(default=2,ge=1,le=8)
    queue_wait_seconds:float=Field(default=2,ge=0,le=10)
    provider_cooldown_seconds:float=Field(default=30,ge=0,le=300)

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
        # One source of defaults: absent environment values use the validated
        # model fields, rather than a second copied list of model/URL/budgets.
        environment = {'enabled': 'LLM_ENABLED', 'model': 'LLM_MODEL', 'base_url': 'LLM_BASE_URL',
            'timeout_seconds': 'LLM_TIMEOUT_SECONDS', 'budget_seconds': 'LLM_BUDGET_SECONDS',
            'max_completion_tokens': 'LLM_MAX_COMPLETION_TOKENS', 'reasoning_effort': 'LLM_REASONING_EFFORT',
            'application_retrieval': 'LLM_APPLICATION_RETRIEVAL', 'query_planning': 'LLM_QUERY_PLANNING',
            'query_max_completion_tokens': 'LLM_QUERY_MAX_COMPLETION_TOKENS',
            'retrieval_rounds': 'LLM_RETRIEVAL_ROUNDS', 'max_tool_calls': 'LLM_MAX_TOOL_CALLS',
            'retrieval_page_size': 'LLM_RETRIEVAL_PAGE_SIZE', 'max_rate_limit_retries': 'LLM_MAX_RATE_LIMIT_RETRIES',
            'max_retry_wait_seconds': 'LLM_MAX_RETRY_WAIT_SECONDS', 'max_list_records': 'COPILOT_MAX_LIST_RECORDS',
            'response_cache_ttl_seconds': 'LLM_RESPONSE_CACHE_TTL_SECONDS', 'response_cache_entries': 'LLM_RESPONSE_CACHE_ENTRIES',
            'max_concurrent_requests': 'LLM_MAX_CONCURRENT_REQUESTS', 'queue_wait_seconds': 'LLM_QUEUE_WAIT_SECONDS',
            'provider_cooldown_seconds': 'LLM_PROVIDER_COOLDOWN_SECONDS'}
        values = {field: os.environ[name] for field, name in environment.items() if os.getenv(name)}
        values['api_key'] = SecretStr(os.getenv('LLM_API_KEY', '') or os.getenv('GROQ_API_KEY', ''))
        return cls(**values)

    @property
    def ready(self):
        return self.enabled and bool(self.api_key.get_secret_value().strip())


def provider_status():
    try:
        config = LLMConfig.from_env()
        return {"provider": "groq", "enabled": config.enabled, "configured": config.ready,
                "model": config.model if config.enabled else None,
                "query_planning": config.query_planning,
                "fallback_available": True, "status": "configured" if config.ready else "deterministic"}
    except ValueError:
        return {"provider": "groq", "enabled": False, "configured": False,
                "fallback_available": True, "status": "invalid_configuration"}


class ProviderFailure(Exception):
    def __init__(self, reason, retry_after_seconds=None, http_status=None):
        self.reason = reason
        self.retry_after_seconds = retry_after_seconds
        self.http_status = http_status
        super().__init__(reason)


class ToolCall(SimpleNamespace):
    def model_dump(self, **kwargs):
        return vars(self)


def strict_schema(schema):
    """Provider schema requires defaulted fields too; Python stays compatible
    with older saved drafts that omitted those fields."""
    schema = copy.deepcopy(schema)
    def visit(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            node.pop("default", None)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(schema)
    return schema


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
        self.usage = {"provider_requests": 0, "prompt_tokens": None, "completion_tokens": None, "rate_limit_retries": 0}
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
                      "temperature": 0, "max_completion_tokens": self.config.max_completion_tokens if tool_choice == "none" else self.config.query_max_completion_tokens, "store": False}
        if self.config.reasoning_effort is not None:
            parameters['reasoning_effort']=self.config.reasoning_effort
        # Groq does not combine local tool use with strict structured outputs.
        if tool_choice == "none":
            parameters["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "grounded_flood_answer", "strict": True, "schema": strict_schema(schema)}}
        else:
            parameters.update(tools=[{"type": "function", "function": {
                "name": t["name"], "description": t["description"], "parameters": strict_schema(t["parameters"])}} for t in tools],
                tool_choice=tool_choice, parallel_tool_calls=True)
            if tool_choice == "required" and len(tools) == 1:
                parameters["tool_choice"] = {"type": "function", "function": {"name": tools[0]["name"]}}
                parameters["parallel_tool_calls"] = False
        for attempt in range(self.config.max_rate_limit_retries + 1):
            remaining = self.deadline - time.monotonic()
            if remaining < 1:
                raise ProviderFailure("timeout")
            timeout = min(self.config.timeout_seconds, remaining)
            try:
                self.usage["provider_requests"] += 1
                response = self.client.with_options(timeout=httpx.Timeout(timeout, connect=min(5, timeout))).chat.completions.create(**parameters)
                break
            except (APITimeoutError, TimeoutError):
                raise ProviderFailure("timeout") from None
            except APIConnectionError:
                raise ProviderFailure("provider_unavailable") from None
            except APIStatusError as error:
                reason = "rate_limited" if error.status_code == 429 else "request_too_large" if error.status_code == 413 else "credentials_rejected" if error.status_code in (401, 403) else "provider_unavailable"
                retry_after = None
                if error.status_code == 429:
                    try:
                        import math
                        retry_after = float(error.response.headers.get("retry-after", "nan"))
                        if not math.isfinite(retry_after) or retry_after < 0:
                            retry_after = None
                    except (ValueError, TypeError):
                        retry_after = None
                    if (retry_after is not None and attempt < self.config.max_rate_limit_retries
                            and retry_after <= self.config.max_retry_wait_seconds
                            and self.deadline - time.monotonic() > retry_after + 1):
                        self.usage["rate_limit_retries"] += 1
                        time.sleep(retry_after)
                        continue
                if error.status_code == 400:
                    reason = 'request_rejected'
                    try:
                        if error.response.json().get('error', {}).get('code') == 'tool_use_failed':
                            reason = 'invalid_tool_call'
                        elif error.response.json().get('error', {}).get('code') in ('json_validate_failed', 'json_schema_validation_failed'):
                            reason = 'structured_output_failed'
                            rejected=error.response.json().get('error',{}).get('failed_generation')
                            if isinstance(rejected,str):
                                from app.copilot_types import ProviderDraft
                                try:
                                    ProviderDraft.model_validate_json(rejected)
                                    diagnostic='JSON contract valid; provider-side schema validation rejected it'
                                except ValueError as validation:
                                    if hasattr(validation,'errors'):
                                        diagnostic=[{'path':list(item['loc']),'type':item['type']} for item in validation.errors(include_input=False)[:5]]
                                    else:diagnostic='Unparseable JSON'
                                # Log value-free locations only. Never release
                                # failed model prose, inputs or raw provider text.
                                logger.warning('Provider structured output rejected (%s characters): %s',len(rejected),diagnostic)
                    except (ValueError, AttributeError):
                        pass
                raise ProviderFailure(reason, retry_after, error.status_code) from None
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
