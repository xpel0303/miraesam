"""설명회 PowerPoint 배경 그림(static/briefing/bg-*.jpg)을 사이트 슬라이드(templates/briefing/_slides.html)에서 다시 뜹니다.
글·카드는 숨기고 바탕·그라데이션·물방울 캐릭터·장식만. 실행: python3 tools/briefing_bg.py (DATABASE_URL 아무거나 있어도 됨)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    import pypdfium2 as pdfium

    from app import pdf_worker
    from app.briefing.spec import chart_geom
    from app.config import BASE_DIR
    from app.web import templates

    tpl = templates.env.from_string('''{% import "briefing/_slides.html" as sl with context %}{{ sl.css(font_dir) }}
<style>@page{size:1280px 720px;margin:0} body{margin:0} .hero,.ft,.hd,.body{display:none !important}</style>
{{ sl.slide(s, 1, 1, None, geom) }}''')
    for lay, name in (("cover", "cover"), ("section", "section"), ("closing", "closing"), ("bullets", "plain")):
        html = tpl.render(font_dir=(BASE_DIR / "static" / "fonts").as_uri(), static_url=(BASE_DIR / "static").as_uri(),
                          s={"layout": lay, "title": "", "sub": "", "bullets": []}, geom=chart_geom)
        doc = pdfium.PdfDocument(pdf_worker.render(html, str(BASE_DIR)))
        doc[0].render(scale=2.5).to_pil().convert("RGB").save(BASE_DIR / "static" / "briefing" / f"bg-{name}.jpg", "JPEG", quality=86, optimize=True)
        print("saved", name)
