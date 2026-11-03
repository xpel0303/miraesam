"""흐름분석 규칙.

지문마다 A4 가로 한 쪽:
  왼쪽  우리말 정리 — 제목, 핵심 내용, '분류와 구조화'(중심 화제 → 갈래 2~3칸 → 결론 칸 + □ 항목)
  오른쪽 네 컷 만화 — 지문의 중요 영어 단어만 써서 흐름을 4칸으로 (그림 모델), 칸마다 단어 + 뜻
"""
import re

MAX_PASSAGES = 30

SYSTEM_PROMPT = """당신은 한국 고등학생용 영어 지문 '흐름분석' 자료를 만드는 교재 저자입니다. JSON 객체 하나로만 답합니다.
- 지문 내용을 우리말로 짧고 또렷하게 구조화합니다. 지문에 없는 내용을 지어내지 않습니다.
- 개조식(명사형·~함/~임 끝맺음)으로 짧게 씁니다."""


def prompt(text: str, level: str = "", fix: list[str] | None = None) -> str:
    lines = [f"대상: {level or '고등학생'}", "아래 영어 지문의 흐름분석 자료를 만들어 주세요.", "",
             "출력 형식:",
             "{",
             '  "title": "우리말 제목 — 글의 요지를 담은 쉬운 명사구, 띄어쓰기 포함 14자 이내, 한 줄 (예: 돌고래와 물고기의 차이)",',
             '  "gist": "핵심 내용 — 지문의 주요 내용을 개조식으로 1~2줄 (60자 안팎, 쉼표로 이어도 됨)",',
             '  "top": "중심 화제 (짧게, 예: 세종의 업적)",',
             '  "branches": [{"head": "갈래 이름 (예: 정치 안정)", "body": "그 갈래의 내용 (40자 안팎. 두 대상을 비교하면 \"A: … / B: …\"처럼 / 로 나눔)"}, ... 2개 또는 3개],',
             '  "result": {"head": "결론·결과 칸 이름 (예: 문화 발달과 유교 질서 확립)", "items": ["항목", ... 2~4개, 짧게]},',
             '  "comic": [{"words": ["지문에 나온 중요 영어 단어", ... 1~2개], "ko": "이 칸 내용 우리말 한 줄 (25자 안팎)",',
             '             "scene": "English description of what to draw in this panel (characters, action, setting), 1~2 sentences"}, ... 정확히 4개]',
             "}",
             "",
             "규칙:",
             "- 분류와 구조화는 지문의 논리 흐름이 한눈에 보이게: 중심 화제 → 나란한 갈래(원인·측면·대조·단계 등) → 결론/결과.",
             "- 하이라이트: gist, top, branches의 body, result의 items에서 가장 중요한 낱말·구를 **두 별표**로 감쌉니다 (예: 돌고래는 **포유류**).",
             "  칸마다 1~2곳, 전체 6~10곳. 문장 전체를 감싸지 않습니다. 별표를 빠뜨리지 마세요.",
             "- 네 컷 만화는 지문 흐름 순서대로 기-승-전-결. words는 지문에 실제로 나온 내용어(명사·동사·형용사)를 지문 속 형태 그대로.",
             "- 네 칸에 같은 단어를 두 번 쓰지 않습니다. scene은 그림으로 그릴 수 있는 구체적 장면(같은 주인공이 이어서 나오게).",
             "", "지문:", text.strip()]
    if fix:
        lines += ["", "직전 답의 문제점 (고쳐서 다시):", *[f"- {x}" for x in fix[:10]]]
    return "\n".join(lines)


def _s(v, n=200) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def plain(t: str) -> str:
    """하이라이트 표시(**)를 뺀 글."""
    return (t or "").replace("**", "")


def clean(raw, text: str = "") -> tuple[dict, list[str]]:
    raw = raw if isinstance(raw, dict) else {}
    errs = []
    branches = [{"head": _s(b.get("head"), 30), "body": _s(b.get("body"), 90)}
                for b in raw.get("branches") or [] if isinstance(b, dict) and _s(b.get("head"))][:3]
    res = raw.get("result") if isinstance(raw.get("result"), dict) else {}
    result = {"head": _s(res.get("head"), 40), "items": [_s(x, 40) for x in res.get("items") or [] if _s(x)][:4]}
    low = (text or "").lower()
    comic = []
    for c in raw.get("comic") or []:
        if not isinstance(c, dict):
            continue
        words = [_s(w, 30) for w in (c.get("words") or []) if _s(w)][:2]
        comic.append({"words": words, "ko": _s(c.get("ko"), 60), "scene": _s(c.get("scene"), 300)})
    comic = comic[:4]
    out = {"title": plain(_s(raw.get("title"), 40)), "gist": _s(raw.get("gist"), 160), "top": _s(raw.get("top"), 40),
           "branches": branches, "result": result, "comic": comic}
    marks = sum(t.count("**") // 2 for t in [out["gist"], out["top"], *[b["body"] for b in branches], *result["items"]])
    if marks < 3:
        errs.append(f"하이라이트(**두 별표**)가 {marks}곳뿐입니다. 중요한 낱말·구 6~10곳을 **로 감싸세요.")
    if len(out["title"].replace(" ", "")) > 14:
        errs.append(f"제목 '{out['title']}'이 깁니다. 띄어쓰기 포함 14자 이내 한 줄로.")
    for k, name in (("title", "제목"), ("gist", "핵심 내용"), ("top", "중심 화제")):
        if not out[k]:
            errs.append(f"{name}이(가) 비었습니다.")
    if len(branches) < 2:
        errs.append("갈래(branches)는 2~3개여야 합니다.")
    if not result["head"] or len(result["items"]) < 2:
        errs.append("결론 칸(result)에 이름과 항목 2~4개가 필요합니다.")
    if len(comic) != 4:
        errs.append(f"네 컷 만화가 {len(comic)}칸입니다. 정확히 4칸.")
    for i, c in enumerate(comic, 1):
        if not c["words"]:
            errs.append(f"{i}번 칸에 영어 단어가 없습니다.")
        elif low and any(w.lower() not in low for w in c["words"]):
            errs.append(f"{i}번 칸 단어 {c['words']} 중 지문에 없는 것이 있습니다. 지문 속 형태 그대로.")
        if not c["scene"]:
            errs.append(f"{i}번 칸 장면(scene)이 비었습니다.")
    return out, errs


def image_prompt(out: dict) -> str:
    """네 컷 만화 → 그림 모델에게 줄 설명. 칸마다 중요 영어 단어만 글자로."""
    lines = [
        "Create ONE square four-panel comic (2 x 2 grid of four EQUAL square panels, reading order: top-left 1, top-right 2, "
        "bottom-left 3, bottom-right 4), separated by clean white gutters, with a thin dark rounded border around each panel.",
        "Purpose: help Korean high school students remember the flow of an English reading passage.",
        "Art style: cute, warm, clean webtoon / storybook illustration, soft pastel colors, clear ink outlines, "
        "the SAME main character(s) in every panel, simple backgrounds, easy to read when printed.",
    ]
    for i, c in enumerate(out.get("comic", [])[:4], 1):
        words = " / ".join(c["words"])
        lines.append(f"Panel {i}: {c['scene']} In this panel, show ONLY the English word(s) \"{words}\" "
                     "in a bold speech bubble or a small sign, spelled exactly.")
    lines.append("Text rule: the only text anywhere in the image is those English words, spelled exactly as given. "
                 "No Korean, no panel numbers, no captions, no titles, no logos, signatures or watermarks.")
    return "\n".join(lines)


# ---------------------------------------------------------------- 선생님이 고친 글 → 내용

def branches_text(bs: list[dict]) -> str:
    return "\n".join(f"{b['head']} | {b['body']}" for b in bs)


def parse_branches(text: str) -> list[dict]:
    out = []
    for line in (text or "").splitlines():
        head, _, body = line.partition("|")
        if head.strip():
            out.append({"head": _s(head, 30), "body": _s(body, 90)})
    return out[:3]


def comic_text(cs: list[dict]) -> str:
    return "\n".join(f"{', '.join(c['words'])} | {c['ko']} | {c['scene']}" for c in cs)


def parse_comic(text: str) -> list[dict]:
    out = []
    for line in (text or "").splitlines():
        cells = [x.strip() for x in line.split("|")]
        if cells and cells[0]:
            cells += [""] * (3 - len(cells))
            out.append({"words": [_s(w, 30) for w in cells[0].split(",") if w.strip()][:2], "ko": _s(cells[1], 60), "scene": _s(cells[2], 300)})
    return out[:4]
