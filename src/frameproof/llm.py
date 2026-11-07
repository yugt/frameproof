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
    for fam in ("claude", "gpt", "gemini", "qwen", "llama", "deepseek", "nemotron", "grok", "mistral", "subq",
                "kimi", "glm", "minimax", "muse"):
        if fam in m:
            return fam
    if provider == "anthropic":
        return "claude"
    return PROVIDERS[provider].family if provider in PROVIDERS else provider


def complete(spec: str, system: str, prompt: str, images: list[bytes] | None = None,
             max_tokens: int = 16000, effort: str = "high") -> Reply:
    provider, model = _split(spec)
    if is_decision(spec):
        raise RuntimeError(f"{spec} is a decision model: it can judge frames or vote on checks, "
                           "but it cannot write text (checks, code)")
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


# ------------------------------------------------------------ decision models
#
# SubQ Snap is a decision model: it does not write text. POST /v1/systemone takes a
# `state` (text), up to 4 images and up to 128 named questions, and returns a
# probability per question (noul = yes/no, choice, score on 2-10 ordered levels).
# Chat/responses requests are refused with 400. Docs: docs.subq.ai/endpoints/decisions-api/

DECISION_MAX_IMAGES = 4
DECISION_MAX_QUESTIONS = 128


def is_decision(spec: str) -> bool:
    """Decision models answer questions with probabilities instead of writing text."""
    if not spec or ":" not in spec:
        return False
    provider, model = _split(spec)
    return provider == "subq" and "snap" in model.lower()


def _decision_image(png: bytes, max_w: int = 960) -> dict:
    """JPEG, at most `max_w` wide: well under the 1.6 MP point where Snap charges more tokens."""
    import io

    from PIL import Image

    im = Image.open(io.BytesIO(png)).convert("RGB")
    im.thumbnail((max_w, max_w))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return {"mediaType": "image/jpeg", "base64": base64.b64encode(buf.getvalue()).decode()}


def decide(spec: str, state: str, questions: dict[str, dict], images: list[bytes] | None = None,
           timeout: float = 120.0, retries: int = 2) -> dict:
    """Ask a decision model. Returns {"answers": {name: {...}}, "usage": {...}, "model": ...}.

    Each answer has `type` plus `noul` (P(yes)), or `choice`/`probabilities`/`confidence`,
    or `score` (probability-weighted level index, 0-based)/`legend`/`probabilities`/`confidence`.
    """
    import time
    import urllib.error
    import urllib.request

    provider, model = _split(spec)
    if provider not in PROVIDERS:
        raise RuntimeError(f"Unknown provider {provider!r}")
    images = images or []
    if len(images) > DECISION_MAX_IMAGES:
        raise ValueError(f"decision models take at most {DECISION_MAX_IMAGES} images, got {len(images)}")
    if not 1 <= len(questions) <= DECISION_MAX_QUESTIONS:
        raise ValueError(f"decision models take 1-{DECISION_MAX_QUESTIONS} questions, got {len(questions)}")
    prov = PROVIDERS[provider]
    key = prov.key()
    if not key:
        raise RuntimeError(f"No API key for {provider}: set one of {', '.join(prov.key_envs)} (or put it in secrets.env)")
    body: dict = {"model": model, "state": state, "questions": questions}
    if images:
        body["images"] = [_decision_image(p) for p in images]
    url = (prov.url() or "").rstrip("/") + "/systemone"
    data = json.dumps(body).encode()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            text = e.read().decode(errors="replace")
            try:
                detail = json.loads(text).get("detail", text)
            except ValueError:
                detail = text
            raise RuntimeError(f"{spec} decision request failed ({e.code}): {str(detail)[:300]}") from None
    raise RuntimeError("unreachable")


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
