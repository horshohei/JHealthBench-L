"""Request builders and response readers for each provider.

Builders take the model entry of the condition table and return (url, headers, body). The body is
`send` from the table plus the message payload; nothing else is added. API keys are read from the
environment at send time and never written to disk.
"""
import json
import os
import time
import urllib.error
import urllib.request

ANTHROPIC_VERSION = '2023-06-01'


# ----------------------------------------------------------------- message payloads
def _answer_input(provider, messages):
    if provider in ('openai_responses',):
        return {'input': [{'role': m['role'], 'content': m['content']} for m in messages]}
    if provider in ('openai_chat', 'vllm_chat', 'anthropic_messages'):
        return {'messages': [{'role': m['role'], 'content': m['content']} for m in messages]}
    if provider == 'gemini_generate':
        role = {'user': 'user', 'assistant': 'model'}
        return {'contents': [{'role': role[m['role']], 'parts': [{'text': m['content']}]} for m in messages]}
    raise ValueError(provider)


def _grader_input(provider, prompt, cache_mode):
    """One user message whose text is prompt.prefix + prompt.suffix.

    explicit_breakpoint / anthropic_cache_control split the same text into two content blocks so
    the provider can cache the prefix; the concatenated text is unchanged.
    """
    if provider == 'openai_responses':
        if cache_mode == 'explicit_breakpoint':
            content = [{'type': 'input_text', 'text': prompt.prefix,
                        'prompt_cache_breakpoint': {'mode': 'explicit'}},
                       {'type': 'input_text', 'text': prompt.suffix}]
        else:
            content = prompt.text
        return {'input': [{'role': 'user', 'content': content}]}
    if provider == 'anthropic_messages':
        if cache_mode == 'anthropic_cache_control':
            content = [{'type': 'text', 'text': prompt.prefix, 'cache_control': {'type': 'ephemeral'}},
                       {'type': 'text', 'text': prompt.suffix}]
        else:
            content = prompt.text
        return {'messages': [{'role': 'user', 'content': content}]}
    if provider in ('openai_chat', 'vllm_chat'):
        return {'messages': [{'role': 'user', 'content': prompt.text}]}
    if provider == 'gemini_generate':
        return {'contents': [{'role': 'user', 'parts': [{'text': prompt.text}]}]}
    raise ValueError(provider)


def _url(m):
    p = m['provider']
    if p == 'openai_responses':
        return m.get('endpoint', 'https://api.openai.com/v1/responses')
    if p == 'openai_chat':
        return m.get('endpoint', 'https://api.openai.com/v1/chat/completions')
    if p == 'anthropic_messages':
        return m.get('endpoint', 'https://api.anthropic.com/v1/messages')
    if p == 'gemini_generate':
        base = m.get('endpoint', 'https://generativelanguage.googleapis.com/v1beta')
        return f"{base}/models/{m['send']['model']}:generateContent"
    if p == 'vllm_chat':
        # HB_VLLM_ENDPOINT: same server configuration started on another port/GPU (infrastructure only;
        # the request body is unchanged). Set only for the client of that server.
        return (os.environ.get('HB_VLLM_ENDPOINT') or m['endpoint']).rstrip('/') + '/v1/chat/completions'
    raise ValueError(p)


def build(m, kind, *, messages=None, prompt=None, extra_send=None):
    """kind = 'answer' (messages = conversation) or 'grade' (prompt = RenderedPrompt)."""
    p = m['provider']
    body = dict(m['send'])
    if p == 'gemini_generate':
        body.pop('model')  # the model is part of the URL for generateContent
    if extra_send:
        body.update(extra_send)
    if kind == 'answer':
        payload = _answer_input(p, messages)
    elif kind == 'grade':
        payload = _grader_input(p, prompt, m.get('grading_cache', 'none'))
    else:
        raise ValueError(kind)
    if p == 'openai_chat' and m.get('system_message') is not None:
        payload['messages'] = [{'role': 'system', 'content': m['system_message']}] + payload['messages']
    body.update(payload)
    headers = {'content-type': 'application/json'}
    if p == 'anthropic_messages':
        headers['anthropic-version'] = ANTHROPIC_VERSION
    return _url(m), headers, body


def auth_headers(m):
    env = m.get('api_key_env')
    if not env:
        return {}
    key = os.environ.get(env)
    if not key:
        raise RuntimeError(f'environment variable {env} is not set')
    p = m['provider']
    if p == 'anthropic_messages':
        return {'x-api-key': key}
    if p == 'gemini_generate':
        return {'x-goog-api-key': key}
    return {'authorization': f'Bearer {key}'}


# ----------------------------------------------------------------- transport
class TransportError(Exception):
    def __init__(self, message, status=None, retryable=True, body=None):
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.body = body


def http_post(url, headers, body, timeout):
    data = json.dumps(body, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        text = e.read().decode('utf-8', 'replace')
        retryable = e.code in (408, 409, 429) or e.code >= 500
        raise TransportError(f'HTTP {e.code}', status=e.code, retryable=retryable, body=text) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise TransportError(f'{type(e).__name__}: {e}', retryable=True) from None


# ----------------------------------------------------------------- response readers
def read(provider, raw):
    """Return dict(text, finish, truncated, refusal, usage) from a raw provider response."""
    if provider == 'openai_responses':
        texts, refusal = [], False
        for item in raw.get('output', []):
            if item.get('type') == 'message':
                for c in item.get('content', []):
                    if c.get('type') == 'output_text':
                        texts.append(c.get('text', ''))
                    elif c.get('type') == 'refusal':
                        refusal = True
        status = raw.get('status')
        reason = (raw.get('incomplete_details') or {}).get('reason')
        u = raw.get('usage') or {}
        return {'text': ''.join(texts), 'finish': reason or status, 'refusal': refusal,
                'truncated': status == 'incomplete' and reason == 'max_output_tokens',
                'usage': {'input': u.get('input_tokens'),
                          'cached': (u.get('input_tokens_details') or {}).get('cached_tokens'),
                          'cache_write': (u.get('input_tokens_details') or {}).get('cache_write_tokens'),
                          'output': u.get('output_tokens'),
                          'reasoning': (u.get('output_tokens_details') or {}).get('reasoning_tokens')}}
    if provider in ('openai_chat', 'vllm_chat'):
        ch = (raw.get('choices') or [{}])[0]
        msg = ch.get('message') or {}
        u = raw.get('usage') or {}
        return {'text': msg.get('content') or '', 'finish': ch.get('finish_reason'),
                'refusal': bool(msg.get('refusal')), 'truncated': ch.get('finish_reason') == 'length',
                'usage': {'input': u.get('prompt_tokens'),
                          'cached': (u.get('prompt_tokens_details') or {}).get('cached_tokens'),
                          'cache_write': None, 'output': u.get('completion_tokens'),
                          'reasoning': (u.get('completion_tokens_details') or {}).get('reasoning_tokens')}}
    if provider == 'anthropic_messages':
        texts = [c.get('text', '') for c in raw.get('content', []) if c.get('type') == 'text']
        u = raw.get('usage') or {}
        stop = raw.get('stop_reason')
        return {'text': ''.join(texts), 'finish': stop, 'refusal': stop == 'refusal',
                'truncated': stop == 'max_tokens',
                'usage': {'input': u.get('input_tokens'), 'cached': u.get('cache_read_input_tokens'),
                          'cache_write': u.get('cache_creation_input_tokens'), 'output': u.get('output_tokens'),
                          'reasoning': (u.get('output_tokens_details') or {}).get('thinking_tokens')}}
    if provider == 'gemini_generate':
        cand = (raw.get('candidates') or [{}])[0]
        parts = (cand.get('content') or {}).get('parts', [])
        texts = [p.get('text', '') for p in parts if not p.get('thought')]
        u = raw.get('usageMetadata') or {}
        fin = cand.get('finishReason')
        return {'text': ''.join(texts), 'finish': fin, 'refusal': fin in ('SAFETY', 'PROHIBITED_CONTENT', 'BLOCKLIST'),
                'truncated': fin == 'MAX_TOKENS',
                'usage': {'input': u.get('promptTokenCount'), 'cached': u.get('cachedContentTokenCount'),
                          'cache_write': None, 'output': u.get('candidatesTokenCount'),
                          'reasoning': u.get('thoughtsTokenCount')}}
    raise ValueError(provider)


def backoff(attempt, base=2.0, cap=120.0):
    return min(cap, base ** attempt)


def sleep(seconds):
    time.sleep(seconds)
