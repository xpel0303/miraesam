"""설명회 슬라이드 → PowerPoint(.pptx). 글·도형·차트 모두 PowerPoint에서 바로 고칠 수 있게 (그림으로 굽지 않음).
글꼴은 윈도·맥 어디에나 있는 '맑은 고딕'(없으면 PowerPoint가 비슷한 글꼴로 바꿈)."""
import io

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Pt

from ..config import BASE_DIR
from .spec import PALETTE

FONT = "맑은 고딕"
NAVY, SKY, INK, INK2, CREAM, LIGHT = "1B2A5C", "2563EB", "1F2740", "5C6680", "FFF4E6", "F3F6FC"
W, H = Emu(12192000), Emu(6858000)  # 16:9
PX = 12192000 / 1280  # 화면 1px → EMU (화면 슬라이드와 같은 배치)
ICON = BASE_DIR / "static" / "icons" / "icon-192.png"


def _rgb(h: str) -> RGBColor:
    return RGBColor.from_string(h.lstrip("#").upper())


def px(v: float) -> Emu:
    return Emu(int(v * PX))


def _box(slide, x, y, w, h, fill=None, radius=False, line=None):
    shp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE, px(x), px(y), px(w), px(h))
    if radius:
        shp.adjustments[0] = 0.12
    if fill:
        shp.fill.solid()
        shp.fill.fore_color.rgb = _rgb(fill)
    else:
        shp.fill.background()
    if line:
        shp.line.color.rgb = _rgb(line)
        shp.line.width = Pt(2.5)
    else:
        shp.line.fill.background()
    shp.shadow.inherit = False
    return shp


def _text(slide, x, y, w, h, text, size, bold=False, color=INK, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP):
    tb = slide.shapes.add_textbox(px(x), px(y), px(w), px(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    for i, line in enumerate(str(text).split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        r = p.add_run()
        r.text = line
        f = r.font
        f.name, f.size, f.bold, f.color.rgb = FONT, Pt(size), bold, _rgb(color)
    return tb


def _bullets(slide, x, y, w, h, items, size, color=INK):
    tb = slide.shapes.add_textbox(px(x), px(y), px(w), px(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    for i, t in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(size * 0.7)
        r1 = p.add_run()
        r1.text = "●  "
        r1.font.size, r1.font.color.rgb, r1.font.name = Pt(size * 0.6), _rgb(SKY), FONT
        r2 = p.add_run()
        r2.text = t
        r2.font.size, r2.font.bold, r2.font.color.rgb, r2.font.name = Pt(size), True, _rgb(color), FONT
    return tb


def _head(slide, s):
    _box(slide, 72, 40, 56, 6, SKY, radius=True)
    _text(slide, 72, 56, 1136, 64, s.get("title", ""), 30, True, NAVY)
    if s.get("sub"):
        _text(slide, 72, 120, 1136, 40, s["sub"], 16, False, INK2)


def _foot(slide, i, n, name):
    slide.shapes.add_picture(str(ICON), px(72), px(668), px(24), px(24))
    _text(slide, 104, 670, 600, 24, name, 11, False, "9AA3B8")
    _text(slide, 1008, 670, 200, 24, f"{i} / {n}", 11, False, "9AA3B8", PP_ALIGN.RIGHT)


def _chart(slide, ch, x, y, w, h):
    data = CategoryChartData()
    data.categories = [it["label"] for it in ch["items"]]
    data.add_series(ch.get("unit") or "값", [it["value"] for it in ch["items"]])
    if ch["type"] == "donut":
        gf = slide.shapes.add_chart(XL_CHART_TYPE.DOUGHNUT, px(x), px(y), px(w), px(h), data)
        c = gf.chart
        c.has_title = False
        c.has_legend = True
        c.legend.position = XL_LEGEND_POSITION.RIGHT
        c.legend.include_in_layout = False
        c.legend.font.size, c.legend.font.name = Pt(16), FONT
        plot = c.plots[0]
        plot.has_data_labels = True
        plot.data_labels.number_format = "0%"
        plot.data_labels.number_format_is_linked = False
        plot.data_labels.show_percentage = True
        plot.data_labels.show_value = False
        plot.data_labels.font.size, plot.data_labels.font.bold = Pt(14), True
        for i, pt in enumerate(plot.series[0].points):
            pt.format.fill.solid()
            pt.format.fill.fore_color.rgb = _rgb(PALETTE[i % len(PALETTE)])
    else:
        gf = slide.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, px(x), px(y), px(w), px(h), data)
        c = gf.chart
        c.has_title = False
        c.has_legend = False
        plot = c.plots[0]
        plot.gap_width = 60
        plot.vary_by_categories = False
        plot.has_data_labels = True
        plot.data_labels.font.size, plot.data_labels.font.bold = Pt(13), True
        for k, pt in enumerate(plot.series[0].points):  # 화면과 같은 색
            pt.format.fill.solid()
            pt.format.fill.fore_color.rgb = _rgb(PALETTE[k % len(PALETTE)])
        cat = c.category_axis
        cat.reverse_order = True  # 위에서부터 큰 순서
        cat.tick_labels.font.size, cat.tick_labels.font.name = Pt(13), FONT
        cat.format.line.fill.background()
        va = c.value_axis
        va.visible = False
        va.has_major_gridlines = False
    return gf


def build(title: str, slides: list[dict], academy_name: str) -> bytes:
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H
    blank = prs.slide_layouts[6]
    n = len(slides)
    name = academy_name or "미래샘 영어연구소"
    for i, s in enumerate(slides, 1):
        sl = prs.slides.add_slide(blank)
        lay = s.get("layout")
        if lay in ("cover", "closing"):
            bg = sl.background.fill
            bg.gradient()
            bg.gradient_angle = 45
            bg.gradient_stops[0].color.rgb = _rgb(NAVY)
            bg.gradient_stops[1].color.rgb = _rgb("3D5BB8")
            sl.shapes.add_picture(str(ICON), px(96), px(150), px(84), px(84))
            _text(sl, 96, 260, 1088, 180, s.get("title", ""), 44 if lay == "cover" else 40, True, "FFFFFF", anchor=MSO_ANCHOR.BOTTOM)
            if s.get("sub"):
                _text(sl, 96, 452, 1088, 60, s["sub"], 20, False, "E6ECFA")
            if s.get("foot"):
                b = _box(sl, 96, 540, 560, 52, None, radius=True, line="AFC0EA")
                b.adjustments[0] = 0.5
                _text(sl, 120, 552, 520, 30, s["foot"], 16, False, "FFFFFF")
            _text(sl, 700, 664, 508, 30, name, 13, False, "C9D3EE", PP_ALIGN.RIGHT)
        elif lay == "section":
            sl.background.fill.solid()
            sl.background.fill.fore_color.rgb = _rgb(CREAM)
            _box(sl, 96, 270, 96, 8, SKY, radius=True)
            _text(sl, 96, 300, 1088, 90, s.get("title", ""), 42, True, NAVY)
            if s.get("sub"):
                _text(sl, 96, 400, 1088, 50, s["sub"], 20, False, INK2)
            _foot(sl, i, n, name)
        else:
            _head(sl, s)
            top, bottom = 190, 636
            if lay == "bullets":
                items = s.get("bullets") or []
                big = len(items) <= 4 and max((len(b) for b in items), default=0) <= 32
                _bullets(sl, 72, top, 1136, bottom - top, items, 22 if big else 18)
            elif lay == "stats":
                its = s.get("items") or []
                k = max(1, len(its))
                gap = 24
                bw = (1136 - gap * (k - 1)) / k
                for j, it in enumerate(its):
                    x = 72 + j * (bw + gap)
                    _box(sl, x, 250, bw, 300, LIGHT, radius=True)
                    _text(sl, x, 300, bw, 90, it.get("value", ""), 40, True, NAVY, PP_ALIGN.CENTER)
                    _text(sl, x, 400, bw, 40, it.get("label", ""), 17, True, INK, PP_ALIGN.CENTER)
                    if it.get("note"):
                        _text(sl, x + 16, 446, bw - 32, 60, it["note"], 13, False, INK2, PP_ALIGN.CENTER)
            elif lay == "chart":
                has_t = bool(s.get("bullets"))
                cw = 700 if has_t else 1136
                _chart(sl, s["chart"], 72, top, cw, bottom - top)
                if has_t:
                    _bullets(sl, 820, top + 40, 388, bottom - top - 40, s["bullets"], 17)
            elif lay == "two":
                for j, side in enumerate((s.get("left") or {}, s.get("right") or {})):
                    x = 72 + j * 584
                    _box(sl, x, top, 552, bottom - top, "EAF1FF" if j else LIGHT, radius=True, line=SKY if j else None)
                    _text(sl, x + 40, top + 32, 472, 50, side.get("head", ""), 22, True, NAVY)
                    _bullets(sl, x + 40, top + 100, 472, bottom - top - 130, side.get("bullets") or [], 17)
            elif lay == "table":
                head, rows = s.get("head") or [], s.get("rows") or []
                cols = max([len(head)] + [len(r) for r in rows] + [1])
                nr = len(rows) + (1 if head else 0)
                rh = min(56, (bottom - top) / max(1, nr))
                tbl = sl.shapes.add_table(nr, cols, px(72), px(top), px(1136), px(rh * nr)).table
                fs = 15 if len(rows) <= 5 else 12
                all_rows = ([head] if head else []) + rows
                for r, row in enumerate(all_rows):
                    for c in range(cols):
                        cell = tbl.cell(r, c)
                        cell.text = str(row[c]) if c < len(row) else ""
                        for p in cell.text_frame.paragraphs:
                            for run in p.runs:
                                run.font.size, run.font.name = Pt(fs), FONT
                                run.font.bold = bool(head and r == 0)
                                run.font.color.rgb = _rgb("FFFFFF" if head and r == 0 else INK)
                        cell.fill.solid()
                        cell.fill.fore_color.rgb = _rgb(NAVY if head and r == 0 else ("F7F9FD" if r % 2 == 0 else "FFFFFF"))
            elif lay == "timeline":
                steps = s.get("steps") or []
                k = max(1, len(steps))
                _box(sl, 72, 262, 1136, 6, "DCE5F5", radius=True)
                sw = 1136 / k
                for j, st in enumerate(steps):
                    x = 72 + j * sw
                    dot = sl.shapes.add_shape(MSO_SHAPE.OVAL, px(x), px(248), px(34), px(34))
                    dot.fill.solid()
                    dot.fill.fore_color.rgb = _rgb(SKY)
                    dot.line.color.rgb = _rgb("FFFFFF")
                    dot.line.width = Pt(4)
                    _text(sl, x, 310, sw - 20, 40, st.get("when", ""), 18, True, NAVY)
                    _text(sl, x, 356, sw - 20, 250, st.get("what", ""), 15, False, INK)
            _foot(sl, i, n, name)
        if s.get("notes"):
            sl.notes_slide.notes_text_frame.text = s["notes"]
    prs.core_properties.title = title
    prs.core_properties.author = name
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
