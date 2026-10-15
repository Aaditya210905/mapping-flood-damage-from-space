"""Private reusable OAuth state and bounded Sentinel Hub transport."""
from collections import OrderedDict
import hashlib
import json
import math
import os
import threading
import time

import httpx
from app.settings import ACQUISITION

_tokens=OrderedDict()
_lock=threading.Lock()


def token_key(url):
    credentials=[url,os.getenv('CDSE_CLIENT_ID',''),os.getenv('CDSE_CLIENT_SECRET','')]
    return hashlib.sha256(json.dumps(credentials).encode()).hexdigest()


def access_token(url,*,invalidate=None):
    client_id,secret=os.getenv('CDSE_CLIENT_ID'),os.getenv('CDSE_CLIENT_SECRET')
    if not client_id or not secret:
        raise ValueError('Live CDSE access needs CDSE_CLIENT_ID and CDSE_CLIENT_SECRET from a Sentinel Hub OAuth client')
    key=token_key(url)
    with _lock:
        cached=_tokens.get(key)
        if cached and cached[1]>time.monotonic() and cached[0]!=invalidate:
            _tokens.move_to_end(key)
            return cached[0]
        _tokens.pop(key,None)
        try:
            with httpx.Client(timeout=30) as client:
                response=client.post(url,data={'grant_type':'client_credentials','client_id':client_id,'client_secret':secret})
            if response.status_code!=200:raise ValueError('CDSE authentication failed; check OAuth credentials and service availability')
            body=response.json()
            token=body['access_token']
            if not isinstance(token,str) or not token:raise ValueError('CDSE returned an invalid authentication token')
            ttl=float(body.get('expires_in',0))
            if math.isfinite(ttl) and ttl>0:
                expiry=time.monotonic()+ttl-min(ACQUISITION.token_expiry_margin_seconds,ttl/2)
                _tokens[key]=(token,expiry)
                while len(_tokens)>8:_tokens.popitem(last=False)
            return token
        except (httpx.HTTPError,KeyError,TypeError,ValueError) as error:
            if isinstance(error,ValueError) and str(error).startswith('CDSE '):raise
            raise ValueError('CDSE authentication is unavailable; no credentials or raw provider error are exposed') from None


def retry_delay(response,attempt,*,milliseconds=False,max_wait_seconds=None):
    value=response.headers.get('retry-after')
    if value is not None:
        try:
            delay=float(value)/(1000 if milliseconds else 1)
            if math.isfinite(delay) and delay>=0:return delay
        except (ValueError,TypeError):pass
    bound=ACQUISITION.max_retry_wait_seconds if max_wait_seconds is None else max_wait_seconds
    return min(2**attempt,bound)


def request_with_retry(client,method,url,*,milliseconds=False,retries=None,max_wait_seconds=None,
                       retry_transport_errors=False,**kwargs):
    retries=ACQUISITION.http_retries if retries is None else retries
    max_wait_seconds=ACQUISITION.max_retry_wait_seconds if max_wait_seconds is None else max_wait_seconds
    for attempt in range(retries+1):
        # Billable processing keeps transport retries disabled. Read-only
        # catalogs, signatures and historical geometry opt in explicitly.
        try:
            response=client.request(method,url,**kwargs)
        except (httpx.TimeoutException,httpx.NetworkError):
            if not retry_transport_errors or attempt==retries:raise
            time.sleep(min(2**attempt,max_wait_seconds))
            continue
        if response.status_code not in (429,500,502,503,504) or attempt==retries:return response
        delay=retry_delay(response,attempt,milliseconds=milliseconds,max_wait_seconds=max_wait_seconds)
        if delay>max_wait_seconds:return response
        time.sleep(delay)
