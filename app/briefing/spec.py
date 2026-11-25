"""설명회 자료: 사이트 데이터(기출 분석·경향분석·학원 현황) + 원장님 메모 → AI가 슬라이드 구성.

그래프·표·숫자 칸은 AI가 숫자를 쓰지 않고 'data' 키로 고르면 여기서 실제 데이터로 채웁니다 (숫자가 틀리지 않게).

슬라이드 한 장 (JSON):
  {"layout": cover|section|bullets|stats|chart|two|table|timeline|closing,
   "title", "sub", "bullets": [..], "items": [{"value","label","note"}] (stats),
   "chart": {"type": bar|donut, "items": [{"label","value","color"}], "unit"},
   "left": {"head","bullets"}, "right": {...}, "head": [..], "rows": [[..]], "steps": [{"when","what"}],
   "data": 데이터 키(선택), "notes": 발표자 메모}
"""
import re
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

KINDS = {
    "exam": ("학교별 시험대비 설명회", "기출 분석·경향분석으로 '○○고 기말 대비' 설명회"),
    "academy": ("학원 입학·신규 설명회", "학원 소개·수업 방식·관리 방식(학부모 앱·리포트)·교재"),
    "season": ("학기·시즌 설명회", "방학 특강·새 학기 반 편성·일정 안내"),
}
LAYOUTS = ("cover", "section", "bullets", "stats", "chart", "two", "table", "timeline", "closing")
LENGTHS = {"short": ("짧게", 8), "normal": ("보통", 12), "long": ("길게", 16)}
PALETTE = ["#7FA3E0", "#F6C453", "#6CC3A6", "#AE97EC", "#F38FA6", "#F5A97F", "#7CC3E8", "#A9B1C2"]  # 홈페이지 파스텔 (조금 진하게)


def _t(v, n: int = 200) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def _num(v) -> float | None:
    try:
        return round(float(str(v).replace(",", "").replace("%", "").replace("점", "")), 2)
    except (TypeError, ValueError):
        return None


def _f(x) -> str:
    if x is None:
        return "-"
    return f"{round(x, 2):.2f}".rstrip("0").rstrip(".")


# ── 데이터 모으기 ────────────────────────────────────────────────────────────


def _paper_label(p) -> str:
    bits = []
    if p.year:
        bits.append(f"{p.year}")
    if p.semester:
        bits.append(f"{p.semester}학기")
    if p.term:
        bits.append(f"{p.term}차")
    return " ".join(bits) or p.title


def _exam_facts(db: Session, aid: int, inp: dict, lines: list[str], data: dict):
    from ..exam import spec as es
    from ..models import ExamPaper, School, SchoolTrend

    school = db.get(School, int(inp["school_id"])) if str(inp.get("school_id") or "").isdigit() else None
    if school is None or school.academy_id != aid:
        school = None
    grade = _t(inp.get("grade"), 6)
    ids = [int(x) for x in inp.get("paper_ids") or [] if str(x).isdigit()]
    q = select(ExamPaper).where(ExamPaper.academy_id == aid, ExamPaper.status == "done")
    q = q.where(ExamPaper.id.in_(ids)) if ids else q.where(ExamPaper.school_id == (school.id if school else -1))
    papers = sorted(db.execute(q).scalars(), key=lambda p: (p.year or 0, p.semester or 0, p.term or 0, p.id))[-6:]
    if school:
        lines.append(f"학교: {school.name}" + (f" {grade}" if grade else ""))
    if inp.get("exam_name"):
        lines.append(f"대비할 시험: {_t(inp['exam_name'], 60)}")
    trend_rows = []
    for p in papers:
        qs = [x for x in (p.questions or []) if isinstance(x, dict)]
        if not qs:
            continue
        qs = es.fill_points(qs)
        st = es.stats(qs)
        lab = _paper_label(p)
        key = f"p{p.id}"
        lines.append(f"\n[기출 {lab} — {p.title}] (데이터 키: types:{key}, mcsa:{key}, stats:{key}, grammar:{key})")
        lines += [f"- {n}" for n in es.notes(qs, st)]
        data[f"types:{key}"] = {"kind": "chart", "title": f"{lab} 유형별 배점", "chart": {
            "type": "bar", "unit": "점", "items": [{"label": t["type"], "value": t["points"]} for t in sorted(st["types"], key=lambda t: -t["points"])[:10]]}}
        data[f"mcsa:{key}"] = {"kind": "chart", "title": f"{lab} 객관식·서술형 배점", "chart": {
            "type": "donut", "unit": "점", "items": [{"label": "객관식", "value": st["mc_points"]}, {"label": "서술형", "value": st["sa_points"]}]}}
        data[f"stats:{key}"] = {"kind": "stats", "title": f"{lab} 한눈에", "items": [
            {"value": f"{st['n']}문항", "label": "전체 문항", "note": f"객관식 {st['mc']} · 서술형 {st['sa']}"},
            {"value": f"{st['sa_share']}%", "label": "서술형 비중", "note": f"{_f(st['sa_points'])}점 / {_f(st['total'])}점"},
            {"value": f"{st['passages']}개", "label": "지문 수", "note": f"평균 {st['avg_words']}단어"},
            {"value": f"{st['max_words']}", "label": "가장 긴 지문(단어)", "note": ""}]}
        gp = [x for x in qs if x.get("type") in ("어법", "서술형(어법)", "서술형(영작)", "서술형(어휘)") and x.get("point")]
        if gp:
            data[f"grammar:{key}"] = {"kind": "table", "title": f"{lab} 어법·서술형 포인트", "head": ["문항", "유형", "포인트"],
                                      "rows": [[_t(x.get("label"), 8), _t(x.get("type"), 12), _t(x.get("point"), 50)] for x in gp[:10]]}
        trend_rows.append((lab, st))
    if len(trend_rows) >= 2:
        data["trend:sa"] = {"kind": "chart", "title": "회차별 서술형 비중", "chart": {"type": "bar", "unit": "%",
                            "items": [{"label": lab, "value": st["sa_share"]} for lab, st in trend_rows]}}
        data["trend:words"] = {"kind": "chart", "title": "회차별 지문 평균 길이", "chart": {"type": "bar", "unit": "단어",
                               "items": [{"label": lab, "value": st["avg_words"]} for lab, st in trend_rows]}}
        types = []
        for _, st in trend_rows:
            for t in st["types"]:
                if t["type"] not in types:
                    types.append(t["type"])
        rows = []
        for ty in types[:12]:
            rows.append([ty] + [_f(next((t["points"] for t in st["types"] if t["type"] == ty), None)) for _, st in trend_rows])
        data["trend:types"] = {"kind": "table", "title": "회차별 유형 배점", "head": ["유형"] + [lab for lab, _ in trend_rows], "rows": rows}
        lines.append("\n여러 회차 비교 데이터 키: trend:sa (서술형 비중), trend:words (지문 길이), trend:types (유형별 배점 표)")
        lines.append("회차별 서술형 비중: " + ", ".join(f"{lab} {st['sa_share']}%" for lab, st in trend_rows))
    if school:
        tq = select(SchoolTrend).where(SchoolTrend.academy_id == aid, SchoolTrend.school_id == school.id)
        tr = next((x for x in db.execute(tq).scalars() if not grade or x.grade in ("", grade)), None)
        m = (tr.memo or {}) if tr else {}
        if m:
            lines.append("\n[학교 경향분석 메모]")
            for k, name in (("summary", "요약"), ("vocab", "어휘"), ("grammar", "어법"), ("reading", "독해"), ("writing", "서술형"), ("strategy", "대비 전략")):
                v = m.get(k)
                if v:
                    lines.append(f"- {name}: " + (" / ".join(map(str, v)) if isinstance(v, list) else str(v)))
    if not trend_rows and not any("경향분석" in x for x in lines):
        lines.append("(이 학교 기출 분석 데이터가 아직 없어요. 원장님 메모를 중심으로 구성하세요. 숫자를 지어내지 마세요.)")
    return school


def _academy_facts(db: Session, aid: int, lines: list[str], data: dict):
    from ..models import (Academy, CareBook, CareBookUse, CareRecord, CareReport, ClassGroup, ExamPaper, Homework, School,
                          Student)

    ac = db.get(Academy, aid)
    n_st = db.execute(select(func.count()).select_from(Student).where(Student.academy_id == aid, Student.active.is_(True))).scalar() or 0
    n_cls = db.execute(select(func.count()).select_from(ClassGroup).where(ClassGroup.academy_id == aid, ClassGroup.active.is_(True))).scalar() or 0
    n_sch = db.execute(select(func.count(func.distinct(Student.school_id))).where(Student.academy_id == aid, Student.active.is_(True),
                                                                                   Student.school_id.is_not(None))).scalar() or 0
    n_papers = db.execute(select(func.count()).select_from(ExamPaper).where(ExamPaper.academy_id == aid, ExamPaper.status == "done")).scalar() or 0
    n_psch = db.execute(select(func.count(func.distinct(ExamPaper.school_id))).where(ExamPaper.academy_id == aid, ExamPaper.status == "done")).scalar() or 0
    n_hw = db.execute(select(func.count()).select_from(Homework).where(Homework.academy_id == aid)).scalar() or 0
    from ..care import today
    since = today() - timedelta(days=30)
    n_rec = db.execute(select(func.count()).select_from(CareRecord).where(CareRecord.academy_id == aid, CareRecord.day >= since)).scalar() or 0
    n_rep = db.execute(select(func.count()).select_from(CareReport).where(CareReport.academy_id == aid, CareReport.status == "public")).scalar() or 0
    lines.append(f"학원 이름: {ac.name if ac else ''}")
    lines.append(f"재원생 {n_st}명, 반 {n_cls}개, 다니는 학교 {n_sch}곳 / 분석한 학교 기출 {n_papers}회({n_psch}개 학교) / 낸 온라인 숙제 {n_hw}개 / "
                 f"최근 30일 매일 수업 기록 {n_rec}건 / 학부모에게 공개한 월간 리포트 {n_rep}개")
    lines.append("학원이 쓰는 관리 도구(이 사이트): 매일 수업 기록(출결·숙제·시험·진도) → 학부모 전용 앱(홈 화면 추가, 수업 기록·공지 푸시 알림), "
                 "월간 학습 리포트(점수 그래프·반 평균 비교·교재 진도·수업 사진·선생님 코멘트), 학생 숙제 앱(단어 암기·게임·퀴즈, 자동 채점·알림), "
                 "학교별 기출 분석·경향분석, 학교 시험과 같은 틀의 동형 모의고사, 지문 분석·변형문제·워크북 자체 제작.")
    tiles = [(n_st, "재원생", "명"), (n_cls, "반", "개"), (n_papers, "분석한 학교 기출", "회"), (n_psch, "분석한 학교", "곳"), (n_rep, "월간 리포트", "개")]
    tiles = [x for x in tiles if x[0]]
    if tiles:
        data["stats:academy"] = {"kind": "stats", "title": "숫자로 보는 학원", "items": [{"value": f"{v}{u}", "label": lab, "note": ""} for v, lab, u in tiles[:4]]}
        lines.append("데이터 키 stats:academy = 위 숫자 칸 (0인 것은 빠짐)")
    books = list(db.execute(select(CareBook).where(CareBook.academy_id == aid, CareBook.active.is_(True), CareBook.show.is_(True)).order_by(CareBook.title)).scalars())
    if books:
        uses: dict[int, list[str]] = {}
        for u, cname in db.execute(select(CareBookUse, ClassGroup.name).join(ClassGroup, ClassGroup.id == CareBookUse.class_id)
                                   .where(CareBookUse.academy_id == aid, CareBookUse.done.is_(False))):
            uses.setdefault(u.book_id, []).append(cname)
        data["books"] = {"kind": "table", "title": "수업 교재", "head": ["교재", "출판사", "반"],
                         "rows": [[b.title, b.publisher, ", ".join(uses.get(b.id, []))] for b in books[:10]]}
        lines.append("쓰는 교재: " + ", ".join(b.title for b in books[:12]) + " (데이터 키 books = 교재 표)")
    names = [s for (s,) in db.execute(select(School.name).where(School.academy_id == aid).order_by(School.name)).all()][:12]
    if names:
        lines.append("학생들이 다니는 학교: " + ", ".join(names))


def _trend_memos(db: Session, aid: int, school_ids: list, lines: list[str]):
    from ..models import School, SchoolTrend
    for sid in school_ids[:4]:
        if not str(sid).isdigit():
            continue
        for tr in db.execute(select(SchoolTrend).where(SchoolTrend.academy_id == aid, SchoolTrend.school_id == int(sid))).scalars():
            sc = db.get(School, tr.school_id)
            m = tr.memo or {}
            summ = m.get("summary") or []
            if summ:
                lines.append(f"- {sc.name if sc else ''} {tr.grade} 출제 경향: " + " / ".join(map(str, summ[:3])))


def facts(db: Session, aid: int, kind: str, inp: dict) -> tuple[str, dict]:
    lines: list[str] = []
    data: dict = {}
    if kind == "exam":
        _exam_facts(db, aid, inp, lines, data)
    else:
        _academy_facts(db, aid, lines, data)
        if kind == "season":
            for k, name in (("season", "설명회 주제"), ("period", "기간"), ("target", "대상")):
                if inp.get(k):
                    lines.append(f"{name}: {_t(inp[k], 120)}")
            if inp.get("school_ids"):
                lines.append("\n[다가오는 학교 시험 경향]")
                _trend_memos(db, aid, inp["school_ids"], lines)
    for k, name in (("audience", "청중"), ("when", "설명회 날짜·장소"), ("contact", "상담·연락처")):
        if inp.get(k):
            lines.append(f"{name}: {_t(inp[k], 120)}")
    if inp.get("notes"):
        lines.append("\n[원장님 메모 — 꼭 반영]\n" + str(inp["notes"])[:4000])
    return "\n".join(lines), data


# ── AI 프롬프트 ──────────────────────────────────────────────────────────────

SYSTEM = """당신은 영어 학원 원장님을 도와 '설명회 슬라이드'를 구성하는 기획자입니다. JSON 으로만 답합니다.
형식: {"title": "설명회 제목", "slides": [슬라이드, ...]}
슬라이드 모양(layout)과 칸:
- cover: title(설명회 제목), sub(부제), foot(날짜·장소)
- section: title(장 제목), sub(한 줄)
- bullets: title, sub(선택), bullets(3~6개, 한 줄 35자 이내)
- stats: title, items[{value, label, note}] 2~4개 — 숫자는 반드시 제공된 데이터에서만. 데이터 키가 있으면 "data": "키" 만 쓰고 items 는 비워도 됨
- chart: title, data(제공된 그래프 데이터 키), bullets(그래프 옆 해설 1~3개)
- two: title, left{head, bullets}, right{head, bullets} — 비교(예: 기존 학원 vs 우리 학원, 1학기 vs 2학기)
- table: title, data(표 데이터 키) 또는 head[..], rows[[..]] (5행 이내)
- timeline: title, steps[{when, what}] 3~6개 — 일정·커리큘럼 흐름
- closing: title(마무리 한 줄), sub, foot(상담·연락처)
모든 슬라이드에 notes(발표자가 말할 내용 2~4문장, 존댓말)를 넣습니다.
규칙:
- 첫 장은 cover, 끝 장은 closing. 중간에 section 으로 2~4개 장을 나눕니다.
- 숫자·통계는 제공된 데이터에 있는 것만 씁니다. 없는 성적 향상·합격 실적·수강료를 지어내지 않습니다(원장님 메모에 있으면 써도 됨).
- 그래프·표는 제공된 데이터 키(data)로만 넣습니다. 키가 없으면 chart 를 만들지 않습니다.
- 학부모님·학생이 듣는 자리이므로 쉬운 말, 짧은 문장. 영어 교육 전문 용어는 풀어 씁니다.
- 원장님 메모는 꼭 반영하고, 학원 이름은 제공된 이름 그대로 씁니다. 다른 학원·브랜드 이름은 쓰지 않습니다."""

KIND_GUIDE = {
    "exam": "학교별 시험대비 설명회: 1) 이 학교 시험은 어떤가(구성·서술형 비중·유형) 2) 최근 경향과 변화 3) 어디서 점수가 갈리나(어법·서술형 포인트) 4) 우리 학원 대비 전략·일정 5) 마무리.",
    "academy": "학원 입학·신규 설명회: 1) 학원 소개·철학 2) 수업 방식과 커리큘럼 3) 관리 방식(매일 기록→학부모 앱 알림, 월간 리포트, 숙제 앱) 4) 내신 대비(학교별 기출 분석) 5) 상담 안내.",
    "season": "학기·시즌 설명회: 1) 이번 시즌 목표 2) 반 편성·대상 3) 특강·커리큘럼 일정(timeline) 4) 관리·학부모 소통 5) 신청·상담 안내.",
}


def prompt(kind: str, fact_text: str, data: dict, length: str, title: str) -> str:
    n = LENGTHS.get(length, LENGTHS["normal"])[1]
    keys = "\n".join(f"- {k}: {v['title']} ({v['kind']})" for k, v in data.items()) or "(없음)"
    return (f"설명회 종류: {KINDS[kind][0]}\n구성 안내: {KIND_GUIDE[kind]}\n슬라이드 수: {n}장 안팎\n"
            + (f"원장님이 정한 제목: {title}\n" if title else "")
            + f"\n쓸 수 있는 그래프·표·숫자 데이터 키:\n{keys}\n\n[자료]\n{fact_text}")


# ── 다듬기 ───────────────────────────────────────────────────────────────────


def _bul(v, n=6, w=80) -> list[str]:
    if isinstance(v, str):
        v = [x for x in v.splitlines()]
    return [_t(x, w) for x in (v or []) if _t(x)][:n]


def clean_slide(s: dict, data: dict) -> dict | None:
    if not isinstance(s, dict):
        return None
    lay = s.get("layout") if s.get("layout") in LAYOUTS else "bullets"
    out = {"layout": lay, "title": _t(s.get("title"), 80), "sub": _t(s.get("sub"), 140), "notes": str(s.get("notes") or "").strip()[:1200]}
    if s.get("foot"):
        out["foot"] = _t(s.get("foot"), 120)
    key = _t(s.get("data"), 40)
    d = data.get(key) if key else None
    if lay in ("bullets", "chart"):
        out["bullets"] = _bul(s.get("bullets"), 6 if lay == "bullets" else 3)
    if lay == "stats":
        items = d["items"] if d and d["kind"] == "stats" else [
            {"value": _t(x.get("value"), 16), "label": _t(x.get("label"), 30), "note": _t(x.get("note"), 40)}
            for x in (s.get("items") or []) if isinstance(x, dict) and _t(x.get("value"))]
        out["items"] = items[:4]
        if not out["items"]:
            return None
    elif lay == "chart":
        ch = d["chart"] if d and d["kind"] == "chart" else s.get("chart") if isinstance(s.get("chart"), dict) else None
        if not ch:
            return None
        items = [{"label": _t(x.get("label"), 20), "value": _num(x.get("value"))} for x in ch.get("items") or [] if isinstance(x, dict)]
        items = [x for x in items if x["value"] is not None][:10]
        if not items:
            return None
        out["chart"] = {"type": "donut" if ch.get("type") == "donut" else "bar", "unit": _t(ch.get("unit"), 6), "items": items}
    elif lay == "two":
        for side in ("left", "right"):
            v = s.get(side) if isinstance(s.get(side), dict) else {}
            out[side] = {"head": _t(v.get("head"), 40), "bullets": _bul(v.get("bullets"), 5, 60)}
    elif lay == "table":
        if d and d["kind"] == "table":
            out["head"], out["rows"] = d["head"], d["rows"][:8]
        else:
            out["head"] = [_t(x, 30) for x in (s.get("head") or [])][:5]
            out["rows"] = [[_t(c, 60) for c in r][:5] for r in (s.get("rows") or []) if isinstance(r, list)][:8]
        if not out["rows"]:
            return None
    elif lay == "timeline":
        out["steps"] = [{"when": _t(x.get("when"), 24), "what": _t(x.get("what"), 70)} for x in (s.get("steps") or []) if isinstance(x, dict)][:6]
        if not out["steps"]:
            return None
    if key and d:
        out["data"] = key
    return out


def clean(raw, data: dict) -> tuple[str, list[dict]]:
    if not isinstance(raw, dict):
        return "", []
    slides = [x for x in (clean_slide(s, data) for s in raw.get("slides") or []) if x]
    return _t(raw.get("title"), 80), slides[:30]


def blank(layout: str = "bullets") -> dict:
    s = {"layout": layout, "title": "새 슬라이드", "sub": "", "notes": ""}
    if layout in ("bullets", "chart"):
        s["bullets"] = ["내용을 적어 주세요"]
    if layout == "two":
        s["left"], s["right"] = {"head": "왼쪽", "bullets": ["내용"]}, {"head": "오른쪽", "bullets": ["내용"]}
    if layout == "stats":
        s["items"] = [{"value": "0", "label": "항목", "note": ""}]
    if layout == "timeline":
        s["steps"] = [{"when": "1주", "what": "내용"}]
    if layout == "table":
        s["head"], s["rows"] = ["항목", "내용"], [["", ""]]
    if layout == "chart":
        s["chart"] = {"type": "bar", "unit": "", "items": [{"label": "항목", "value": 1}]}
    return s


def chart_geom(ch: dict) -> dict:
    """화면·PDF 용 SVG 좌표 (막대: 가로 막대, 도넛: 조각 경로)."""
    import math
    items = ch["items"]
    items = [{**x, "color": x.get("color") or PALETTE[i % len(PALETTE)], "txt": _f(x["value"])} for i, x in enumerate(items)]
    if ch["type"] == "donut":
        total = sum(max(0, x["value"]) for x in items) or 1
        a0, paths = -math.pi / 2, []
        for x in items:
            frac = max(0, x["value"]) / total
            a1 = a0 + 2 * math.pi * frac
            large = 1 if frac > 0.5 else 0
            r, ri = 150, 88
            if frac >= 0.999:
                paths.append({**x, "full": True, "pct": 100})
            else:
                p = (f"M {r * math.cos(a0):.1f} {r * math.sin(a0):.1f} A {r} {r} 0 {large} 1 {r * math.cos(a1):.1f} {r * math.sin(a1):.1f} "
                     f"L {ri * math.cos(a1):.1f} {ri * math.sin(a1):.1f} A {ri} {ri} 0 {large} 0 {ri * math.cos(a0):.1f} {ri * math.sin(a0):.1f} Z")
                paths.append({**x, "d": p, "pct": round(100 * frac)})
            a0 = a1
        return {"paths": paths}
    peak = max([x["value"] for x in items] + [0]) or 1
    return {"bars": [{**x, "w": round(100 * max(0, x["value"]) / peak, 1)} for x in items]}
