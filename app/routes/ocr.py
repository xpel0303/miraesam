"""PDF·사진 → 지문 글자 (지문 넣는 칸마다 붙는 '파일에서 지문 가져오기' 버튼)."""
import asyncio

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import JSONResponse

from .. import llm
from ..config import DEFAULT_GEMINI_MODEL
from ..models import Teacher
from ..security import decrypt
from ..web import current_teacher

router = APIRouter()

MAX_PAGES = 8
MAX_BYTES = 25 * 1024 * 1024

SYSTEM = """당신은 영어 시험지·교재 사진에서 영어 지문을 글자 그대로 옮겨 적는 사람입니다. 옮긴 글만 답합니다 (설명·머리말 없이).
- 영어 지문(본문)만 옮깁니다. 문제 번호, 우리말 발문, 선택지(①~⑤), 쪽 번호·머리글, 출처 표시, 손글씨·밑줄 표시·필기는 빼세요.
- 지문 속 글자는 고치지 말고 그대로 (철자·문장부호·대소문자 포함). 빈칸(____)이나 (A)(B)·①~⑤가 본문 안에 있으면 그대로 둡니다.
- 줄 끝에서 끊긴 낱말(hyphen-\\nation)은 이어 붙이고, 줄바꿈은 문단이 바뀔 때만 넣습니다.
- 지문이 여러 개면 지문 사이에 '=====' 한 줄을 넣어 나눕니다.
- 영어 지문이 없으면 '(지문 없음)' 이라고만 답합니다."""


def _images(files: list[tuple[str, bytes]]) -> list[llm.Image]:
    from ..exam import pages

    raw = []
    for name, data in files:
        if name.lower().endswith(".pdf") or data[:4] == b"%PDF":
            raw += pages.render_pdf(data)
        else:
            raw.append(pages.open_image(data))
        if len(raw) > MAX_PAGES:
            raise ValueError(f"한 번에 {MAX_PAGES}쪽까지 가져올 수 있어요.")
    out = []
    for im in raw:
        out.append(llm.Image(pages.to_jpeg(pages.normalize(im)), "image/jpeg"))
    return out


@router.post("/api/ocr")
async def ocr(files: list[UploadFile], teacher: Teacher = Depends(current_teacher)):
    key = decrypt(teacher.gemini_key_enc)
    if not key:
        return JSONResponse({"error": "Gemini API 키가 없어요. 설정 화면에서 키를 먼저 넣어 주세요."}, status_code=400)
    got, total = [], 0
    for f in files or []:
        data = await f.read()
        total += len(data)
        if total > MAX_BYTES:
            return JSONResponse({"error": "파일이 너무 커요 (25MB까지)."}, status_code=400)
        if data:
            got.append((f.filename or "", data))
    if not got:
        return JSONResponse({"error": "파일을 골라 주세요."}, status_code=400)
    try:
        imgs = await asyncio.to_thread(_images, got)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"파일을 읽지 못했어요: {e}"}, status_code=400)
    try:
        text = await asyncio.to_thread(
            llm.generate, key, f"사진 {len(imgs)}장에서 영어 지문을 옮겨 적어 주세요.", system=SYSTEM, images=imgs,
            model=teacher.gemini_model or DEFAULT_GEMINI_MODEL, temperature=0.1, max_tokens=12000)
    except llm.LLMError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    text = str(text).strip()
    if text.startswith("(지문 없음)"):
        return JSONResponse({"error": "파일에서 영어 지문을 찾지 못했어요."}, status_code=422)
    parts = [p.strip() for p in text.split("=====") if p.strip()]
    return {"text": "\n\n".join(parts), "passages": parts}
