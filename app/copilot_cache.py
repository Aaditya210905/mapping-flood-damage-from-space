"""Bounded, process-local reuse of validated drafts and account cooldowns."""
from collections import OrderedDict
from app.settings import RUNTIME
from contextlib import contextmanager
import copy
import hashlib
import json
import threading
import time

from app.llm import ProviderFailure

_condition=threading.Condition()
_drafts=OrderedDict()
_cooldowns=OrderedDict()
_inflight=set()
_active=0


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def account(config):
    return digest([config.base_url,config.model,config.api_key.get_secret_value()])


def cooldown(config):
    key=account(config)
    with _condition:
        value=_cooldowns.get(key)
        if value and value[0]>time.monotonic():
            raise ProviderFailure(value[1],value[0]-time.monotonic(),value[2])
        _cooldowns.pop(key,None)


def failed(config,error):
    if config.provider_cooldown_seconds<=0:return
    if error.reason not in ('rate_limited','credentials_rejected'):return
    delay=error.retry_after_seconds if error.retry_after_seconds is not None else config.provider_cooldown_seconds
    if delay<=0:return
    with _condition:
        _cooldowns[account(config)]=(time.monotonic()+min(delay,3600),error.reason,error.http_status)
        while len(_cooldowns)>RUNTIME.provider_account_cache_entries:_cooldowns.popitem(last=False)


@contextmanager
def generation(key,config):
    global _active
    deadline=time.monotonic()+min(config.queue_wait_seconds,config.budget_seconds)
    cached=None
    owner=False
    with _condition:
        while True:
            value=_drafts.get(key) if key else None
            if value and value[0]>time.monotonic():
                _drafts.move_to_end(key)
                cached=copy.deepcopy(value[1])
                break
            if value:_drafts.pop(key,None)
            if (not key or key not in _inflight) and _active<config.max_concurrent_requests:
                _active+=1
                if key:_inflight.add(key)
                owner=True
                break
            remaining=deadline-time.monotonic()
            if remaining<=0:raise ProviderFailure('provider_busy')
            _condition.wait(remaining)
    try:
        yield cached
    finally:
        if owner:
            with _condition:
                _active-=1
                if key:_inflight.discard(key)
                _condition.notify_all()


def remember(key,draft,config):
    if not key or config.response_cache_ttl_seconds<=0:return
    with _condition:
        payload = draft.model_dump(mode='json') if hasattr(draft, 'model_dump') else copy.deepcopy(draft)
        _drafts[key]=(time.monotonic()+config.response_cache_ttl_seconds,payload)
        _drafts.move_to_end(key)
        while len(_drafts)>config.response_cache_entries:_drafts.popitem(last=False)


def forget(key):
    with _condition:_drafts.pop(key,None)


def clear():
    with _condition:
        _drafts.clear();_cooldowns.clear()
