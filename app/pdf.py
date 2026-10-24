"""PDF 출력 공통 엔진 (WeasyPrint).

모든 출력물은 templates/pdf/_base.html 을 이어받아
나눔고딕 · 뉴트럴톤 · 학원 로고/이름 머리말 · 쪽 번호를 똑같이 씁니다.
"""
from .config import BASE_DIR
from .web import templates

FONT_DIR = (BASE_DIR / "static" / "fonts").as_uri()


def render_pdf(template: str, ctx: dict) -> bytes:
    from weasyprint import HTML  # 무거운 라이브러리라 쓸 때 불러옴

    html = templates.env.get_template(template).render(font_dir=FONT_DIR, **ctx)
    return HTML(string=html, base_url=str(BASE_DIR)).write_pdf()
