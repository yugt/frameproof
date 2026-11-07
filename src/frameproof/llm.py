"""Thin model clients. Claude goes through the Anthropic SDK; everything else
through the OpenAI SDK (optionally pointed at an OpenAI-compatible base URL).

Only two operations are needed: text in -> text out, with optional PNG images.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass

from .config import PROVIDERS


@dataclass
class Reply:
    text: str
    model: str
    stop_reason: str | None = None


def _split(spec: str) -> tuple[str, str]:
    if not spec:
        raise RuntimeError(
            "No model configured. Set FP_CHECKER_MODELS / FP_JUDGE_MODEL / FP_IMPLEMENTER_MODEL "
            'as "<provider>:<model>", e.g. "anthropic:claude-opus-5-5" or "google:<model>"; list ids with `frameproof models`.'
        )
    provider, _, model = spec.partition(":")
    if not model:
        raise RuntimeError(f"Model spec {spec!r} must look like provider:model")
    return provider, model


def family(spec: str) -> str:
    """Coarse model family, used for the independence warning."""
    if not spec:
        return ""
    provider, model = _split(spec)
    m = model.lower()
    for fam in ("claude", "gpt", "gemini", "qwen", "llama", "deepseek", "nemotron", "grok", "mistral", "subq"):
        if fam in m:
            return fam
    if provider == "anthropic":
        return "claude"
    return PROVIDERS[provider].family if provider in PROVIDERS else provider


def complete(spec: str, system: str, prompt: str, images: list[bytes] | None = None,
             max_tokens: int = 16000, effort: str = "high") -> Reply:
    provider, model = _split(spec)
    if provider == "anthropic":
        return _anthropic(model, system, prompt, images or [], max_tokens, effort)
    if provider in PROVIDERS:
        return _openai(provider, model, system, prompt, images or [], max_tokens)
    raise RuntimeError(f"Unknown provider {provider!r}; known: anthropic, {', '.join(PROVIDERS)}")


def _anthropic(model: str, system: str, prompt: str, images: list[bytes], max_tokens: int, effort: str) -> Reply:
    import anthropic

    client = anthropic.Anthropic()
    content: list[dict] = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                     "data": base64.standard_b64encode(png).decode()}}
        for png in images
    ]
    content.append({"type": "text", "text": prompt})
    # Streaming avoids HTTP timeouts on long outputs. Server-side fallback re-runs a
    # refused request on Anthropic's recommended fallback model.
    with client.beta.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": effort},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError(f"{model} refused: {getattr(msg, 'stop_details', None)}")
    text = "".join(b.text for b in msg.content if b.type == "text")
    return Reply(text=text, model=model, stop_reason=msg.stop_reason)


def openai_client(provider: str):
    from openai import OpenAI

    prov = PROVIDERS[provider]
    key = prov.key()
    if not key:
        raise RuntimeError(f"No API key for {provider}: set one of {', '.join(prov.key_envs)} (or put it in secrets.env)")
    return OpenAI(api_key=key, base_url=prov.url()) if prov.url() else OpenAI(api_key=key)


def list_models(provider: str) -> list[str]:
    """Model ids the key can reach, via the OpenAI-compatible /models route."""
    if provider == "anthropic":
        import anthropic

        return sorted(m.id for m in anthropic.Anthropic().models.list())
    return sorted(m.id for m in openai_client(provider).models.list())


def _openai(provider: str, model: str, system: str, prompt: str, images: list[bytes], max_tokens: int) -> Reply:
    client = openai_client(provider)
    content: list[dict] = [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}
        for png in images
    ]
    content.append({"type": "text", "text": prompt})
    resp = client.chat.completions.create(
        model=model,
        # OpenAI's own API wants max_completion_tokens; compatible endpoints take max_tokens.
        **({"max_completion_tokens": max_tokens} if provider == "openai" else {"max_tokens": max_tokens}),
        messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
    )
    choice = resp.choices[0]
    return Reply(text=choice.message.content or "", model=model, stop_reason=choice.finish_reason)


_FENCE = re.compile(r"```(?:json|html)?\s*\n(.*?)```", re.S)


def extract_block(text: str) -> str:
    """Last fenced block if present, else the whole text."""
    blocks = _FENCE.findall(text)
    return (blocks[-1] if blocks else text).strip()


def extract_json(text: str):
    body = extract_block(text)
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        # Fall back to the outermost {...} or [...] span.
        for open_, close in (("[", "]"), ("{", "}")):
            i, j = body.find(open_), body.rfind(close)
            if 0 <= i < j:
                try:
                    return json.loads(body[i : j + 1])
                except json.JSONDecodeError:
                    pass
        raise
