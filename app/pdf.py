"""PDF 출력 공통 엔진 (WeasyPrint).

모든 출력물은 templates/pdf/_base.html 을 이어받아
나눔고딕 · 뉴트럴톤 · 학원 로고 머리말 · 쪽 번호를 똑같이 씁니다.

- 그리기는 따로 띄운 프로세스에서 (서버 메모리가 불어나지 않게)
- 지문이 많은 자료는 chunk_key 로 몇 개씩 나눠 그린 뒤 한 파일로 합침 (쪽 번호는 이어서)
"""
import io
import multiprocessing as mp

from .config import BASE_DIR
from .web import templates

FONT_DIR = (BASE_DIR / "static" / "fonts").as_uri()
CHUNK = 6  # 한 번에 그릴 지문 수


def _draw(html: str) -> bytes:
    from . import pdf_worker

    try:
        with mp.get_context("spawn").Pool(1) as pool:
            return pool.apply(pdf_worker.render, (html, str(BASE_DIR)))
    except (OSError, mp.ProcessError):  # 프로세스를 못 띄우는 환경이면 그냥 여기서
        return pdf_worker.render(html, str(BASE_DIR))


def page_counts(template: str, ctxs: list[dict]) -> list[int]:
    """같은 틀을 여러 설정으로 그려 보고 쪽 수만 돌려줌 (따로 띄운 프로세스에서 한 번에)."""
    from . import pdf_worker

    tpl = templates.env.get_template(template)
    htmls = [tpl.render(font_dir=FONT_DIR, **c) for c in ctxs]
    try:
        with mp.get_context("spawn").Pool(1) as pool:
            return pool.apply(pdf_worker.count_pages, (htmls, str(BASE_DIR)))
    except (OSError, mp.ProcessError):
        return pdf_worker.count_pages(htmls, str(BASE_DIR))


def _pages(pdf: bytes) -> int:
    import pypdfium2 as pdfium

    return len(pdfium.PdfDocument(pdf))


def _merge(parts: list[bytes]) -> bytes:
    import pypdfium2 as pdfium

    out = pdfium.PdfDocument.new()
    for part in parts:
        out.import_pages(pdfium.PdfDocument(part))
    buf = io.BytesIO()
    out.save(buf)
    return buf.getvalue()


def render_pdf(template: str, ctx: dict, chunk_key: str | None = None, chunk: int = CHUNK) -> bytes:
    tpl = templates.env.get_template(template)
    items = ctx.get(chunk_key) if chunk_key else None
    if not items or len(items) <= chunk:
        return _draw(tpl.render(font_dir=FONT_DIR, **ctx))
    parts, page = [], 0
    for start in range(0, len(items), chunk):
        sub = {**ctx, chunk_key: items[start:start + chunk], "item_offset": start, "page_offset": page, "chunked": True}
        part = _draw(tpl.render(font_dir=FONT_DIR, **sub))
        page += _pages(part)
        parts.append(part)
    return _merge(parts)
