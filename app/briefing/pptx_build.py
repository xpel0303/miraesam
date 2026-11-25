"""설명회 슬라이드 → PowerPoint(.pptx). 홈페이지·사이트 슬라이드와 같은 톤.

- 배경(점무늬·파스텔 그라데이션·물방울 캐릭터·장식)은 사이트 슬라이드에서 떠 둔 그림 static/briefing/bg-*.jpg
  (슬라이드 모양을 바꾸면 tools/briefing_bg.py 로 다시 뜸)
- 글·카드·차트는 PowerPoint에서 바로 고칠 수 있게 도형으로. 제목 '주아(Jua)', 본문 '나눔스퀘어라운드(NanumSquareRound)'.
  컴퓨터에 두 글꼴이 없으면 비슷한 글꼴로 보이므로, 사이트의 '글꼴 받기'(/briefing/fonts.zip)로 한 번 설치.
- 좌표는 화면 슬라이드(1280×720 px)와 같게 px 로 적음.
"""
import io

from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

from ..config import BASE_DIR
from .spec import PALETTE

TITLE_FONT, BODY_FONT = "Jua", "NanumSquareRound"
NAVY, INK, INK2, INK3, LINE, LINE2 = "1B2A5C", "1F2740", "5C6680", "98A1B5", "DDE6F3", "EAF0F8"
PASTEL = ["E6F0FC", "FFF6DC", "EAF6F1", "F3EEFD", "FDEDF1", "FFF0E6"]
W, H = Emu(12192000), Emu(6858000)  # 16:9
PX = 12192000 / 1280
BG = BASE_DIR / "static" / "briefing"
ICON = BASE_DIR / "static" / "icons" / "icon-192.png"


def _rgb(h: str) -> RGBColor:
    return RGBColor.from_string(h.lstrip("#").upper())


def px(v: float) -> Emu:
    return Emu(int(v * PX))


def fpt(v: float) -> Pt:
    """화면 글자 크기(px) → pt."""
    return Pt(round(v * 0.75, 1))


def _font(run, face: str, size_px: float, color: str = INK, bold: bool = False):
    f = run.font
    f.name, f.size, f.bold, f.color.rgb = face, fpt(size_px), bold, _rgb(color)
    rpr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):  # 한글(동아시아) 글꼴도 같은 글꼴로
        el = rpr.find(qn(tag))
        if el is None:
            el = etree.SubElement(rpr, qn(tag))
            latin = rpr.find(qn("a:latin"))
            if latin is not None:
                latin.addnext(el)
        el.set("typeface", face)


def _card(slide, x, y, w, h, fill="FFFFFF", line=LINE, radius=28, shadow=True):
    shp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, px(x), px(y), px(w), px(h))
    shp.adjustments[0] = min(0.5, radius / max(1, min(w, h)))
    shp.fill.solid()
    shp.fill.fore_color.rgb = _rgb(fill)
    if line:
        shp.line.color.rgb = _rgb(line)
        shp.line.width = Pt(1.5)
    else:
        shp.line.fill.background()
    if not shadow:
        shp.shadow.inherit = False
    else:  # 부드러운 그림자 (홈 카드처럼)
        sp = shp._element.spPr
        eff = etree.SubElement(sp, qn("a:effectLst"))
        sh = etree.SubElement(eff, qn("a:outerShdw"), blurRad="190500", dist="76200", dir="5400000", algn="t", rotWithShape="0")
        clr = etree.SubElement(sh, qn("a:srgbClr"), val=NAVY)
        etree.SubElement(clr, qn("a:alpha"), val="14000")
    shp.text_frame.text = ""
    return shp


def _circle(slide, x, y, d, fill, text="", size=22):
    c = slide.shapes.add_shape(MSO_SHAPE.OVAL, px(x), px(y), px(d), px(d))
    c.fill.solid()
    c.fill.fore_color.rgb = _rgb(fill)
    c.line.color.rgb = _rgb("D5DDEB")
    c.line.width = Pt(1.25)
    c.shadow.inherit = False
    if text:
        tf = c.text_frame
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        r = p.add_run()
        r.text = str(text)
        _font(r, TITLE_FONT, size, NAVY)
    return c


def _text(slide, x, y, w, h, text, size, face=BODY_FONT, color=INK, bold=False, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=None):
    tb = slide.shapes.add_textbox(px(x), px(y), px(w), px(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    for i, line in enumerate(str(text).split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        if spacing:
            p.line_spacing = spacing
        r = p.add_run()
        r.text = line
        _font(r, face, size, color, bold)
    return tb


def _dots(slide, x, y, w, h, items, size=22):
    """작은 점 목록 (그래프 옆 해설·둘 비교)."""
    tb = slide.shapes.add_textbox(px(x), px(y), px(w), px(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    for i, t in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(size * 0.55)
        r1 = p.add_run()
        r1.text = "●  "
        _font(r1, BODY_FONT, size * 0.55, "7FA3E0")
        r2 = p.add_run()
        r2.text = t
        _font(r2, BODY_FONT, size, INK, True)
    return tb


def _bg(slide, name: str):
    slide.shapes.add_picture(str(BG / f"bg-{name}.jpg"), 0, 0, W, H)


def _head(slide, s):
    tb = _text(slide, 72, 46, 888, 70, s.get("title", ""), 50, TITLE_FONT, NAVY)
    star = tb.text_frame.paragraphs[0].add_run()  # 제목 옆 작은 반짝이 (화면 슬라이드처럼)
    star.text = "  ✦"
    _font(star, BODY_FONT, 26, "F2C14E")
    if s.get("sub"):
        _text(slide, 72, 122, 1136, 40, s["sub"], 23, BODY_FONT, INK2)


def _foot(slide, i, n, name, own_logo=False):
    if not own_logo:  # 학원 로고를 올렸으면 오른쪽 위에만 (바닥은 이름만)
        slide.shapes.add_picture(str(ICON), px(72), px(668), px(28), px(28))
    _text(slide, 72 if own_logo else 108, 670, 600, 26, name, 18, TITLE_FONT, INK3)
    pg = _card(slide, 1138, 666, 70, 30, "FFFFFF", LINE, radius=15, shadow=False)
    tf = pg.text_frame
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = f"{i} / {n}"
    _font(r, BODY_FONT, 15, INK2, True)


def _chart(slide, ch, x, y, w, h):
    data = CategoryChartData()
    data.categories = [it["label"] for it in ch["items"]]
    data.add_series(ch.get("unit") or "값", [it["value"] for it in ch["items"]])
    donut = ch["type"] == "donut"
    gf = slide.shapes.add_chart(XL_CHART_TYPE.DOUGHNUT if donut else XL_CHART_TYPE.BAR_CLUSTERED, px(x), px(y), px(w), px(h), data)
    c = gf.chart
    c.has_title = False
    c.font.name, c.font.size = BODY_FONT, fpt(19)
    plot = c.plots[0]
    plot.has_data_labels = True
    dl = plot.data_labels
    dl.font.size, dl.font.bold, dl.font.name = fpt(18), True, BODY_FONT
    dl.font.color.rgb = _rgb(NAVY)
    for k, pt in enumerate(plot.series[0].points):
        pt.format.fill.solid()
        pt.format.fill.fore_color.rgb = _rgb(PALETTE[k % len(PALETTE)])
        if donut:
            pt.format.line.color.rgb = _rgb("FFFFFF")
            pt.format.line.width = Pt(2)
    if donut:
        c.has_legend = True
        c.legend.position = XL_LEGEND_POSITION.RIGHT
        c.legend.include_in_layout = False
        c.legend.font.size, c.legend.font.name, c.legend.font.bold = fpt(22), BODY_FONT, True
        dl.number_format, dl.number_format_is_linked = "0%", False
        dl.show_percentage, dl.show_value = True, False
    else:
        c.has_legend = False
        plot.gap_width = 55
        plot.vary_by_categories = False
        cat = c.category_axis
        cat.reverse_order = True
        cat.tick_labels.font.size, cat.tick_labels.font.name, cat.tick_labels.font.bold = fpt(19), BODY_FONT, True
        cat.format.line.fill.background()
        va = c.value_axis
        va.visible = False
        va.has_major_gridlines = False
    return gf


DEFAULT_LOGO = BASE_DIR / "static" / "logo-word.png"


def logo_png(data_url: str | None) -> bytes | None:
    """설정에서 올린 학원 로고(data URL) → PowerPoint에 넣을 수 있는 PNG/JPG. 못 읽으면 None."""
    import base64

    if not data_url or not data_url.startswith("data:") or "," not in data_url:
        return None
    head, b64 = data_url.split(",", 1)
    try:
        raw = base64.b64decode(b64)
    except ValueError:
        return None
    try:
        if "svg" in head:  # SVG → PDF → PNG (투명 바탕)
            import pypdfium2 as pdfium

            from .. import pdf_worker
            html = ('<style>@page{size:600px 300px;margin:0}body{margin:0;display:flex;align-items:center;justify-content:center;height:300px}'
                    f'img{{max-width:600px;max-height:300px}}</style><img src="{data_url}">')
            doc = pdfium.PdfDocument(pdf_worker.render(html, str(BASE_DIR)))
            im = doc[0].render(scale=2, fill_color=(255, 255, 255, 0)).to_pil()
            bbox = im.getbbox()
            if bbox:
                im = im.crop(bbox)
        else:
            from PIL import Image
            im = Image.open(io.BytesIO(raw))
            im.load()
        out = io.BytesIO()
        im.save(out, "PNG")
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return None


def _brand(slide, logo: bytes, big: bool):
    """오른쪽 위 로고 (화면 슬라이드 .lg 와 같은 자리·크기)."""
    from PIL import Image
    w0, h0 = Image.open(io.BytesIO(logo)).size
    mh, mw = (72, 280) if big else (60, 240)
    k = min(mh / h0, mw / w0)
    w, h = w0 * k, h0 * k
    right, top = (64, 44) if big else (60, 40)
    slide.shapes.add_picture(io.BytesIO(logo), px(1280 - right - w), px(top + (mh - h) / 2), px(w), px(h))


def build(title: str, slides: list[dict], academy_name: str, academy_logo: str | None = None) -> bytes:
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H
    blank = prs.slide_layouts[6]
    n = len(slides)
    name = academy_name or "미래샘 영어연구소"
    own = logo_png(academy_logo)
    logo = own or DEFAULT_LOGO.read_bytes()
    for i, s in enumerate(slides, 1):
        sl = prs.slides.add_slide(blank)
        lay = s.get("layout")
        if lay in ("cover", "closing", "section"):
            _bg(sl, lay)
            if lay == "section":
                chip = _card(sl, 96, 230, 60 + 18 * len(name), 42, "FFFFFF", LINE, radius=21, shadow=False)
                _text(sl, 96, 238, 60 + 18 * len(name), 30, name, 22, TITLE_FONT, NAVY, align=PP_ALIGN.CENTER)
                del chip
                _text(sl, 96, 290, 760, 170, s.get("title", ""), 68, TITLE_FONT, NAVY, anchor=MSO_ANCHOR.TOP, spacing=1.0)
                if s.get("sub"):
                    _text(sl, 96, 392, 740, 60, s["sub"], 28, BODY_FONT, INK2)
                _foot(sl, i, n, name, bool(own))
            else:
                if not own:
                    sl.shapes.add_picture(str(ICON), px(96), px(170), px(40), px(40))
                _text(sl, 96 if own else 146, 176, 600, 34, name, 24, TITLE_FONT, NAVY)
                _text(sl, 96, 236, 760, 190, s.get("title", ""), 72 if lay == "cover" else 64, TITLE_FONT, NAVY, spacing=1.0)
                y = 440
                if s.get("sub"):
                    _text(sl, 96, y, 740, 80, s["sub"], 28, BODY_FONT, INK2)
                    y += 90
                if s.get("foot"):
                    fw = min(740, 70 + 21 * len(s["foot"]))
                    _card(sl, 96, y, fw, 56, "FFFFFF", LINE, radius=28)
                    _text(sl, 96, y + 13, fw, 34, s["foot"], 24, BODY_FONT, NAVY, True, PP_ALIGN.CENTER)
        else:
            _bg(sl, "plain")
            _head(sl, s)
            top, bottom = 178, 634
            if lay == "bullets":
                items = s.get("bullets") or []
                sm = len(items) > 4 or max((len(b) for b in items), default=0) > 30
                rh, gap, fs, d = (60, 11, 22, 38) if sm else (80, 16, 27, 48)
                for k, b in enumerate(items):
                    y = top + k * (rh + gap)
                    _card(sl, 72, y, 1136, rh, "FFFFFF", LINE, radius=rh / 2)
                    _circle(sl, 90, y + (rh - d) / 2, d, PASTEL[k % 6], k + 1, fs - 2)
                    _text(sl, 90 + d + 20, y, 1136 - d - 60, rh, b, fs, BODY_FONT, INK, True, anchor=MSO_ANCHOR.MIDDLE)
            elif lay == "stats":
                its = s.get("items") or []
                k = max(1, len(its))
                gap = 22
                bw = (1136 - gap * (k - 1)) / k
                for j, it in enumerate(its):
                    x = 72 + j * (bw + gap)
                    _card(sl, x, 240, bw, 330, PASTEL[j % 4], LINE)
                    _text(sl, x, 300, bw, 100, it.get("value", ""), 72 if len(str(it.get("value", ""))) <= 6 else 54, TITLE_FONT, NAVY, align=PP_ALIGN.CENTER)
                    _text(sl, x + 10, 414, bw - 20, 40, it.get("label", ""), 24, BODY_FONT, INK, True, PP_ALIGN.CENTER)
                    if it.get("note"):
                        _text(sl, x + 16, 460, bw - 32, 60, it["note"], 18, BODY_FONT, INK2, align=PP_ALIGN.CENTER)
            elif lay == "chart":
                has_t = bool(s.get("bullets"))
                cw = 690 if has_t else 1136
                _card(sl, 72, top, cw, bottom - top, "FFFFFF", LINE)
                _chart(sl, s["chart"], 96, top + 24, cw - 48, bottom - top - 48)
                if has_t:
                    _card(sl, 790, top, 418, bottom - top, PASTEL[1], LINE)
                    _dots(sl, 820, top + 40, 360, bottom - top - 60, s["bullets"], 22)
            elif lay == "two":
                for j, side in enumerate((s.get("left") or {}, s.get("right") or {})):
                    x = 72 + j * 582
                    nb = len(side.get("bullets") or [])
                    _card(sl, x, top, 554, max(260, 120 + 52 * nb), PASTEL[0] if j else "FFFFFF", "A9C4EC" if j else LINE)
                    _text(sl, x + 38, top + 32, 480, 50, side.get("head", ""), 34, TITLE_FONT, NAVY)
                    _dots(sl, x + 38, top + 100, 480, bottom - top - 130, side.get("bullets") or [], 22)
            elif lay == "table":
                head, rows = s.get("head") or [], s.get("rows") or []
                cols = max([len(head)] + [len(r) for r in rows] + [1])
                nr = len(rows) + (1 if head else 0)
                sm = len(rows) > 5
                rh = 42 if sm else 52
                _card(sl, 72, top, 1136, rh * nr + 8, "FFFFFF", LINE, radius=24)
                tbl = sl.shapes.add_table(nr, cols, px(76), px(top + 4), px(1128), px(rh * nr)).table
                tbl.first_row = bool(head)
                for r, row in enumerate(([head] if head else []) + rows):
                    is_head = bool(head) and r == 0
                    for c in range(cols):
                        cell = tbl.cell(r, c)
                        cell.text = str(row[c]) if c < len(row) else ""
                        cell.margin_left = cell.margin_right = px(18)
                        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
                        for p in cell.text_frame.paragraphs:
                            for run in p.runs:
                                _font(run, TITLE_FONT if is_head else BODY_FONT, (19 if sm else 22) if is_head else (17 if sm else 21), NAVY if is_head else INK)
                        cell.fill.solid()
                        cell.fill.fore_color.rgb = _rgb(PASTEL[0] if is_head else ("F9FBFE" if r % 2 == 0 else "FFFFFF"))
                tbl_el = tbl._tbl
                tbl_el.tblPr.set("bandRow", "0")
            elif lay == "timeline":
                steps = s.get("steps") or []
                k = max(1, len(steps))
                gap = 18
                sw = (1136 - gap * (k - 1)) / k
                _card(sl, 92, top + 26, 1096, 12, "FFFFFF", LINE, radius=6, shadow=False)
                for j, st in enumerate(steps):
                    x = 72 + j * (sw + gap)
                    _card(sl, x, top + 76, sw, 200, "FFFFFF", LINE)
                    _circle(sl, x + sw / 2 - 24, top + 8, 48, PASTEL[j % 6], j + 1, 22)
                    _text(sl, x + 14, top + 104, sw - 28, 44, st.get("when", ""), 28, TITLE_FONT, NAVY, align=PP_ALIGN.CENTER)
                    _text(sl, x + 18, top + 156, sw - 36, bottom - top - 180, st.get("what", ""), 21, BODY_FONT, INK, align=PP_ALIGN.CENTER)  # noqa: E501
            _foot(sl, i, n, name, bool(own))
        _brand(sl, logo, lay in ("cover", "closing", "section"))
        if s.get("notes"):
            sl.notes_slide.notes_text_frame.text = s["notes"]
    prs.core_properties.title = title
    prs.core_properties.author = name
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


_fonts_zip: bytes | None = None


def fonts_zip() -> bytes:
    """설명회 PowerPoint 글꼴 설치 파일 (주아 + 나눔스퀘어라운드 TTF, 둘 다 무료 OFL). 사이트 글꼴 파일에서 만들어 둠."""
    global _fonts_zip
    if _fonts_zip:
        return _fonts_zip
    import zipfile

    from fontTools.ttLib import TTFont
    fd = BASE_DIR / "static" / "fonts"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(fd / "Jua-Regular.ttf", "Jua-Regular.ttf")
        for src, out in (("NanumSquareRoundR.woff2", "NanumSquareRoundR.ttf"), ("NanumSquareRoundB.woff2", "NanumSquareRoundB.ttf"),
                         ("NanumSquareRoundEB.woff2", "NanumSquareRoundEB.ttf")):
            f = TTFont(fd / src)
            f.flavor = None
            tb = io.BytesIO()
            f.save(tb)
            z.writestr(out, tb.getvalue())
        z.writestr("설치 방법.txt", "1. 압축을 풉니다.\n2. 글꼴 파일(.ttf) 4개를 모두 골라 오른쪽 클릭 → '설치'(윈도) 또는 두 번 눌러 '서체 설치'(맥).\n"
                                  "3. PowerPoint를 껐다가 다시 열면 설명회 자료가 사이트와 같은 글꼴로 보입니다.\n\n"
                                  "글꼴: 주아(Jua, 우아한형제들), 나눔스퀘어라운드(네이버) — 둘 다 SIL Open Font License, 무료.\n")
    _fonts_zip = buf.getvalue()
    return _fonts_zip
