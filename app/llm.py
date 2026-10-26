"""Gemini 호출 공통 엔진.

- 선생님 개인 키(암호화 저장)로 호출합니다.
- 글자만 보내거나, 시험지 사진/PDF 페이지 이미지를 함께 보낼 수 있습니다 (1단계 기출 분석에서 사용).
- JSON으로 답을 받을 수 있고, 429(분당 한도)·5xx 는 잠시 기다렸다가 다시 시도합니다.
- Gemini 3.x 는 생각(thinking) 토큰이 답변 예산을 나눠 쓰므로 thinkingLevel 을 기본 low 로 둡니다.
"""
import base64
import json
import os
import time
from dataclasses import dataclass

import httpx

from .config import DEFAULT_GEMINI_MODEL

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class LLMError(Exception):
    """선생님에게 그대로 보여줄 수 있는 한국어 오류."""


@dataclass
class Image:
    data: bytes
    mime: str = "image/png"


def _extract_text(body: dict) -> str:
    candidates = body.get("candidates") or []
    if not candidates:
        reason = (body.get("promptFeedback") or {}).get("blockReason")
        if reason:
            raise LLMError(f"Gemini가 요청을 거절했습니다 (사유: {reason}). 내용을 확인해 주세요.")
        raise LLMError("Gemini 응답이 비어 있습니다. 잠시 뒤 다시 시도해 주세요.")
    cand = candidates[0]
    finish = cand.get("finishReason")
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if finish == "MAX_TOKENS":
        raise LLMError("답변이 너무 길어 중간에 잘렸습니다. 한 번에 넣는 분량을 줄여 주세요.")
    if finish == "SAFETY":
        raise LLMError("Gemini 안전 필터에 걸려 답변을 받지 못했습니다.")
    if not text.strip():
        raise LLMError("Gemini가 빈 답변을 보냈습니다. 다시 시도해 주세요.")
    return text


def generate(
    api_key: str | None,
    prompt: str,
    *,
    system: str | None = None,
    images: list[Image] | None = None,
    model: str | None = None,
    json_mode: bool = False,
    temperature: float = 0.4,
    max_tokens: int = 16384,
    thinking: str = "low",
    retries: int = 3,
    timeout: float = 180.0,
):
    """Gemini 한 번 호출. json_mode=True 면 파싱한 dict/list 를, 아니면 문자열을 돌려줍니다."""
    if not api_key:
        raise LLMError("Gemini API 키가 없습니다. 설정 화면에서 키를 먼저 넣어 주세요.")
    if os.getenv("FAKE_LLM") == "1":  # 개발용: 실제 호출 없이 가짜 답 (배포 환경에서는 켜지 않음)
        from .devfake import fake_generate

        return fake_generate(prompt, system=system, json_mode=json_mode)

    parts: list[dict] = [{"text": prompt}]
    for img in images or []:
        parts.append({"inline_data": {"mime_type": img.mime, "data": base64.b64encode(img.data).decode()}})

    gen_cfg: dict = {"temperature": temperature, "maxOutputTokens": max_tokens}
    if thinking:
        gen_cfg["thinkingConfig"] = {"thinkingLevel": thinking}
    if json_mode:
        gen_cfg["responseMimeType"] = "application/json"

    payload: dict = {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen_cfg}
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}

    url = API.format(model=model or DEFAULT_GEMINI_MODEL)
    last_error = "알 수 없는 오류"
    for attempt in range(retries):
        try:
            resp = httpx.post(url, json=payload, headers={"x-goog-api-key": api_key}, timeout=timeout)
        except httpx.HTTPError as e:
            last_error = f"Gemini 서버에 연결하지 못했습니다 ({type(e).__name__})."
            time.sleep(2 * (attempt + 1))
            continue

        if resp.status_code == 200:
            text = _extract_text(resp.json())
            if not json_mode:
                return text
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                last_error = "Gemini 답변을 JSON으로 읽지 못했습니다."
                continue

        detail = ""
        try:
            detail = resp.json().get("error", {}).get("message", "")
        except ValueError:
            pass
        if resp.status_code in (400, 403) and "API key" in detail:
            raise LLMError("Gemini API 키가 올바르지 않습니다. 설정 화면에서 키를 다시 확인해 주세요.")
        if resp.status_code == 404:
            raise LLMError(f"'{model or DEFAULT_GEMINI_MODEL}' 모델을 찾을 수 없습니다. 설정에서 모델을 바꿔 주세요.")
        if resp.status_code == 429:
            last_error = "Gemini 사용 한도(분당/일일)에 걸렸습니다. 잠시 뒤 다시 시도해 주세요."
            time.sleep(15 * (attempt + 1))
            continue
        if resp.status_code >= 500:
            last_error = f"Gemini 서버 오류({resp.status_code})입니다. 잠시 뒤 다시 시도해 주세요."
            time.sleep(4 * (attempt + 1))
            continue
        raise LLMError(f"Gemini 호출 실패 ({resp.status_code}): {detail[:200]}")

    raise LLMError(last_error)


# ---------------------------------------------------------------- 그림 만들기 (Gemini 이미지 모델, 'Nano Banana')

IMAGE_MODELS = ["gemini-3.1-flash-image-preview", "gemini-3-pro-image-preview", "gemini-2.5-flash-image"]
_image_model_ok: str | None = None  # 한 번 성공한 모델은 기억


def generate_image(api_key: str | None, prompt: str, *, aspect: str = "16:9", timeout: float = 240.0) -> bytes:
    """그림 한 장(PNG/JPEG 바이트). 최신 이미지 모델부터 차례로 시도 (계정에서 못 쓰는 모델은 건너뜀)."""
    global _image_model_ok
    if not api_key:
        raise LLMError("Gemini API 키가 없습니다. 설정 화면에서 키를 먼저 넣어 주세요.")
    if os.getenv("FAKE_LLM") == "1":
        from .devfake import fake_image

        return fake_image(prompt, aspect)
    payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
               "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": aspect}}}
    models = ([_image_model_ok] if _image_model_ok else []) + [m for m in IMAGE_MODELS if m != _image_model_ok]
    last_error = "그림을 만들지 못했습니다."
    for model in models:
        for attempt in range(3):
            try:
                resp = httpx.post(API.format(model=model), json=payload, headers={"x-goog-api-key": api_key}, timeout=timeout)
            except httpx.HTTPError as e:
                last_error = f"Gemini 서버에 연결하지 못했습니다 ({type(e).__name__})."
                time.sleep(3 * (attempt + 1))
                continue
            if resp.status_code == 200:
                body = resp.json()
                for cand in body.get("candidates") or []:
                    for part in (cand.get("content") or {}).get("parts") or []:
                        data = (part.get("inlineData") or part.get("inline_data") or {}).get("data")
                        if data:
                            _image_model_ok = model
                            return base64.b64decode(data)
                last_error = "Gemini가 그림 대신 빈 답을 보냈습니다 (안전 필터일 수 있어요)."
                break
            detail = ""
            try:
                detail = resp.json().get("error", {}).get("message", "")
            except ValueError:
                pass
            if resp.status_code in (400, 403) and "API key" in detail:
                raise LLMError("Gemini API 키가 올바르지 않습니다. 설정 화면에서 키를 다시 확인해 주세요.")
            if resp.status_code in (404, 400, 403):  # 이 계정에서 못 쓰는 모델 → 다음 모델
                last_error = f"그림 모델을 쓸 수 없습니다 ({model}: {detail[:120]}). 결제(유료 등급)가 켜진 키가 필요할 수 있어요."
                break
            if resp.status_code == 429:
                last_error = "그림 만들기 사용 한도에 걸렸습니다. 잠시 뒤 다시 시도해 주세요. (무료 키는 그림 모델을 못 쓸 수 있어요)"
                time.sleep(15 * (attempt + 1))
                continue
            last_error = f"그림 만들기 실패 ({resp.status_code}): {detail[:150]}"
            time.sleep(4 * (attempt + 1))
    raise LLMError(last_error)
