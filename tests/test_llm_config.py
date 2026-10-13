import httpx
import pytest
from groq import Groq
from pydantic import SecretStr

from app.llm import GroqProvider, LLMConfig, ProviderFailure, provider_status


def test_default_is_disabled_and_missing_credentials_are_not_configured(monkeypatch):
    for name in ('LLM_ENABLED','LLM_API_KEY','GROQ_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    assert provider_status()['configured'] is False
    monkeypatch.setenv('LLM_ENABLED','true')
    assert provider_status()['configured'] is False


def test_secrets_and_private_endpoint_are_not_public(monkeypatch):
    monkeypatch.setenv('LLM_ENABLED','true')
    monkeypatch.setenv('GROQ_API_KEY','unit-test-placeholder-never-a-real-key')
    config = LLMConfig.from_env()
    assert config.ready
    assert 'unit-test-placeholder' not in repr(config)
    assert 'unit-test-placeholder' not in str(config.model_dump())
    assert 'api_key' not in provider_status()
    assert 'base_url' not in provider_status()
    assert provider_status()['provider'] == 'groq'


def test_invalid_configuration_does_not_break_status(monkeypatch):
    monkeypatch.setenv('LLM_BASE_URL','https://name:password@example.com/v1')
    assert provider_status()['status'] == 'invalid_configuration'


@pytest.mark.parametrize('status,reason', [(429,'rate_limited'),(500,'provider_unavailable'),(401,'credentials_rejected')])
def test_sdk_http_failures_are_sanitized(monkeypatch, status, reason):
    client = Groq(api_key='unit-test-placeholder',base_url='https://api.groq.com',max_retries=0,
                    http_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status,json={'error':{'message':'PRIVATE TRACE AND KEY SHOULD NOT ESCAPE'}}))))
    monkeypatch.setattr('app.llm.Groq',lambda **kwargs:client)
    provider = GroqProvider(LLMConfig(enabled=True,api_key=SecretStr('unit-test-placeholder')))
    try:
        with pytest.raises(ProviderFailure) as error:
            provider.create(instructions='test',items=[],tools=[],schema={'type':'object','properties':{},'additionalProperties':False})
        assert error.value.reason == reason
        assert 'PRIVATE' not in str(error.value)
    finally:
        provider.close()


@pytest.mark.parametrize('choices',[None,[],{},'malformed'])
def test_malformed_native_sdk_response_is_rejected(monkeypatch,choices):
    client=Groq(api_key='unit-test-placeholder',max_retries=0,http_client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'id':'test','object':'chat.completion','created':0,'model':'openai/gpt-oss-120b','choices':choices}))))
    monkeypatch.setattr('app.llm.Groq',lambda **kwargs:client)
    provider=GroqProvider(LLMConfig(enabled=True,api_key=SecretStr('unit-test-placeholder')))
    try:
        with pytest.raises(ProviderFailure):
            provider.create(instructions='test',items=[],tools=[],schema={},tool_choice='none')
    finally:provider.close()


def test_sdk_initialization_failure_is_sanitized(monkeypatch):
    def fail(**kwargs):raise ValueError('PRIVATE configuration trace')
    monkeypatch.setattr('app.llm.Groq',fail)
    with pytest.raises(ProviderFailure,match='provider_unavailable') as error:
        GroqProvider(LLMConfig(enabled=True,api_key=SecretStr('unit-test-placeholder')))
    assert 'PRIVATE' not in str(error.value)


def test_sdk_request_timeout_and_no_storage(monkeypatch):
    seen=[]
    def handle(request):
        import json
        seen.append(json.loads(request.content))
        raise httpx.ReadTimeout('private failure',request=request)
    client=Groq(api_key='unit-test-placeholder',base_url='https://api.groq.com',max_retries=0,
                  http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr('app.llm.Groq',lambda **kwargs:client)
    provider=GroqProvider(LLMConfig(enabled=True,api_key=SecretStr('unit-test-placeholder')))
    try:
        with pytest.raises(ProviderFailure,match='timeout'):
            provider.create(instructions='test',items=[],tools=[],schema={'type':'object','properties':{},'additionalProperties':False})
        assert len(seen)==1
        assert seen[0]['store'] is False
        assert {t['type'] for t in seen[0]['tools']} == set()
    finally:
        provider.close()
