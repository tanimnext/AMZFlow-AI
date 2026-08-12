"""Shared LLM client for every Gemini/OpenAI-compatible chat-completion
provider used by this tool.

Both amazon_video_maker.py (script/title generation) and metadata_generator.py
(YouTube title/description/tags) used to have their own independent copies of
this exact dispatch logic, and they had already drifted apart (different
LongCat defaults, an empty-string-in-a-list bug, only one of the two files
handling an OpenRouter key pasted into the OpenAI field). This module is the
one place that knows how to talk to a provider, so "add a model" or "fix a
provider bug" only ever needs to happen here.
"""
import random
import time

import requests

OPENAI_COMPATIBLE_ENDPOINTS = {
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "openai": "https://api.openai.com/v1/chat/completions",
    # longcat and deepseek have user-configurable endpoints; callers pass
    # `endpoint=` explicitly for those.
}


class LLMCallError(Exception):
    """A single provider+key attempt failed.

    retryable=True  -> rate limit / server error / timeout / network hiccup;
                        worth retrying the SAME key briefly, then moving on.
    retryable=False -> bad auth / bad model name / malformed response; not
                        worth retrying the same key, but still worth trying
                        the next key or the next chain entry.
    """
    def __init__(self, message, retryable=True, status_code=None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


def _parse_openai_style(resp_json):
    return resp_json['choices'][0]['message']['content'].strip()


def _parse_gemini_style(resp_json):
    return resp_json['candidates'][0]['content']['parts'][0]['text'].strip()


def _one_attempt(provider, prompt, api_key, model, endpoint=None, timeout=30):
    """Single HTTP call. Returns the generated text, or raises LLMCallError."""
    try:
        if provider == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            resp = requests.post(
                url, headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=timeout
            )
        elif provider == "vertex_gemini":
            # `api_key` here is a short-lived OAuth access token (minted from
            # the service-account JSON by vertex_auth), not a static API key,
            # and `endpoint` is already the full generateContent URL with
            # project/location/model baked into the path.
            if not endpoint:
                raise LLMCallError("Vertex AI endpoint not configured", retryable=False)
            resp = requests.post(
                endpoint, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                # Unlike the AI Studio "gemini" endpoint above, Vertex AI
                # rejects a content part with no explicit role ("Please use
                # a valid role: user, model.").
                json={"contents": [{"role": "user", "parts": [{"text": prompt}]}]}, timeout=timeout
            )
        else:
            # openrouter / openai / longcat / deepseek are all OpenAI-compatible.
            ep = endpoint or OPENAI_COMPATIBLE_ENDPOINTS.get(provider)
            if not ep:
                raise LLMCallError(f"No endpoint configured for provider '{provider}'", retryable=False)
            # A key that's clearly an OpenRouter key (sk-or-...) pasted into a
            # different provider's field gets redirected there automatically,
            # uniformly across every provider (previously this was only
            # special-cased for the OpenAI field, and it silently ignored
            # whatever model the user had actually configured).
            use_model = model
            if provider != "openrouter" and api_key.startswith("sk-or-"):
                ep = OPENAI_COMPATIBLE_ENDPOINTS["openrouter"]
            resp = requests.post(
                ep, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": use_model, "messages": [{"role": "user", "content": prompt}]}, timeout=timeout
            )
    except requests.exceptions.Timeout as e:
        raise LLMCallError(f"timeout: {e}", retryable=True)
    except requests.exceptions.RequestException as e:
        raise LLMCallError(f"network error: {e}", retryable=True)

    if resp.status_code == 200:
        try:
            return _parse_gemini_style(resp.json()) if provider in ("gemini", "vertex_gemini") else _parse_openai_style(resp.json())
        except (KeyError, IndexError, ValueError, TypeError) as e:
            # e.g. a safety-blocked Gemini response with no candidates -- the
            # old code let this raise as an unguarded KeyError and swallowed
            # it in a bare except, indistinguishable from a network error.
            raise LLMCallError(f"unexpected response shape: {e}", retryable=False, status_code=200)

    if resp.status_code == 429 or resp.status_code >= 500:
        raise LLMCallError(f"HTTP {resp.status_code}: {resp.text[:200]}", retryable=True, status_code=resp.status_code)
    # Bad auth / bad model name / bad request. Not worth retrying this same
    # key, but the caller still moves on to the next key or chain entry.
    raise LLMCallError(f"HTTP {resp.status_code}: {resp.text[:200]}", retryable=False, status_code=resp.status_code)


def call_with_keys(provider, prompt, api_keys, model, endpoint=None, timeout=30, max_attempts_per_key=2):
    """Tries each key in `api_keys` in turn. A retryable failure gets one
    short backoff-retry on the SAME key before moving to the next key; a
    fatal failure (bad model, bad auth) moves to the next key immediately.
    Raises LLMCallError if every key failed."""
    if not api_keys:
        raise LLMCallError(f"No API keys configured for provider '{provider}'", retryable=False)

    last_err = None
    for key in api_keys:
        for attempt in range(1, max_attempts_per_key + 1):
            try:
                return _one_attempt(provider, prompt, key, model, endpoint=endpoint, timeout=timeout)
            except LLMCallError as e:
                last_err = e
                tag = "RETRY" if e.retryable else "SKIP"
                print(f"[LLM][{tag}] {provider} key {key[:6]}... attempt {attempt}/{max_attempts_per_key}: {e}")
                if e.retryable and attempt < max_attempts_per_key:
                    time.sleep(min(4.0, 0.75 * (2 ** (attempt - 1))) + random.uniform(0, 0.4))
                    continue
                break  # exhausted retries on this key (or fatal) -- next key
    raise last_err or LLMCallError(f"All keys failed for provider '{provider}'", retryable=False)


PROVIDER_ORDER = ("gemini", "vertex_gemini", "openrouter", "openai", "deepseek", "longcat")


def build_chain(primary, provider_config, fallback_enabled=False, chain_raw="", order=PROVIDER_ORDER):
    """Builds the ordered chain call_chain() consumes.

    amazon_video_maker.py (script writing) and metadata_generator.py (YouTube
    title/description/tags) run as separate processes and each used to carry
    its own copy of this logic. They drifted exactly the way duplicated logic
    does: metadata_generator's copy never learned about "vertex_gemini", so a
    user who picked Vertex as their provider was silently downgraded to
    whichever id sat first in that file's tuple (longcat) for every metadata
    call -- even with no longcat key saved. Both callers now pass their own
    `provider_config` lookup into this one implementation.

    provider_config: callable(provider) -> (api_keys, default_model, endpoint)

    Order is: the primary provider, then the user's own manual chain (only if
    they opted in), then automatically every other provider that already has
    a usable credential saved. The primary keeps its slot even with no key --
    call_chain() then reports "no keys configured" for it, which surfaces the
    real problem instead of hiding it behind a silent substitution.
    """
    primary = primary if primary in order else order[-1]
    seen = {primary}
    keys, model, endpoint = provider_config(primary)
    chain = [{"provider": primary, "model": model, "api_keys": keys, "endpoint": endpoint}]

    if fallback_enabled and chain_raw:
        for line in str(chain_raw).split("\n"):
            line = line.strip()
            if not line or "|" not in line:
                continue
            prov, _, mdl = line.partition("|")
            prov = prov.strip().lower()
            mdl = mdl.strip()
            if prov in seen or prov not in order:
                continue
            seen.add(prov)
            keys, default_model, endpoint = provider_config(prov)
            chain.append({"provider": prov, "model": mdl or default_model, "api_keys": keys, "endpoint": endpoint})

    for prov in order:
        if prov in seen:
            continue
        seen.add(prov)
        keys, default_model, endpoint = provider_config(prov)
        if keys:  # only providers that actually have a key/credential saved
            chain.append({"provider": prov, "model": default_model, "api_keys": keys, "endpoint": endpoint})
    return chain


def call_chain(prompt, chain, timeout=30):
    """chain: ordered list of dicts, each {"provider", "model", "api_keys",
    "endpoint"(optional)}. Tries each entry in order; an entry is exhausted
    only once every one of its keys has failed. Returns (text, provider_used).
    Raises the last LLMCallError if the entire chain is exhausted."""
    last_err = None
    for entry in chain:
        try:
            text = call_with_keys(
                entry["provider"], prompt, entry.get("api_keys") or [], entry.get("model", ""),
                endpoint=entry.get("endpoint"), timeout=timeout
            )
            return text, entry["provider"]
        except LLMCallError as e:
            last_err = e
            print(f"[LLM][CHAIN] provider '{entry['provider']}' exhausted, trying next chain entry if any...")
            continue
    raise last_err or LLMCallError("Empty LLM chain", retryable=False)
