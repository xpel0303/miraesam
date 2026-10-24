"""지문 분석 규칙 (기존 analysis · passage-analysis-worksheet 두 스킬을 합친 것).

지문 하나 → 문장마다
  - 색 표시: 문법(grammar) · 어휘(vocab) · 문법+어휘(both) · 연결어(connective) · 형광펜(highlight)
  - 구문 칩: "관계대명사(주격)" 같은 문법 용어, 어휘는 "ruthless 무자비한"
  - 한글 해석
  - 포인트 박스 (출제 가능성 높은 문장만, 전체의 20~40%)
지문 끝에: 주제 · 흐름(단계) · 배경지식 · 중요 어휘표(단어|의미|유의어|반의어)

안정장치
- 문장 나누기는 코드가 합니다 → AI가 문장을 빠뜨리거나 합칠 수 없음.
- AI는 HTML을 쓰지 않고 "어느 단어에 어떤 색"만 줍니다 → 코드가 위치를 찾아 칠함.
"""
import re

MAX_PASSAGES = 6

TAG_TYPES = ("grammar", "vocab", "both", "connective", "parallel", "highlight")
POINT_TYPES = {
    "reading": "독해 포인트",
    "grammar": "어법 포인트",
    "topic": "주제",
    "blank": "빈칸",
    "insertion": "문장삽입",
    "implication": "함의추론",
}
DEFAULT_OPTIONS = {"layout": "split", "show_background": True, "show_vocab": True, "show_visual": False}
MAX_POINTS = 3  # 문장 하나에 붙는 포인트 카드 최대 수

# 정리 그림(4컷)에 쓸 수 있는 아이콘 — templates/pdf/_icons.html 에 그림이 있음
ICONS = ["brain", "people", "person", "idea", "warning", "shield", "scale", "book", "chart_up", "chart_down", "globe", "heart",
         "clock", "money", "leaf", "gear", "chat", "question", "check", "cycle", "phone", "home", "target", "eye", "lock",
         "school", "science", "medical", "robot", "handshake"]

# ---------------------------------------------------------------- 문장 나누기

_ABBREV = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "e.g", "i.e", "u.s", "u.k",
    "fig", "approx", "inc", "ltd", "mt", "jan", "feb", "apr",
    "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
}
_BOUNDARY = re.compile(r"[.!?]+[\"'”’)\]]*(?=\s+[\"'“‘(\[]?[A-Z0-9])")


def split_sentences(text: str) -> list[str]:
    """영어 지문 → 문장 목록. 문단 줄바꿈은 항상 문장 경계로 봅니다."""
    out: list[str] = []
    for para in re.split(r"\n\s*\n|\n", text.replace("\r", "")):
        para = re.sub(r"\s+", " ", para).strip()
        if not para:
            continue
        start = 0
        for m in _BOUNDARY.finditer(para):
            end = m.end()
            before = para[start:m.start() + 1]
            last_word = re.findall(r"([A-Za-z.]+)\.$", before.rstrip("\"'”’)]"))
            token = last_word[0].lower().rstrip(".") if last_word else ""
            if token in _ABBREV or (len(token) == 1 and token.isalpha()):
                continue  # Mr. / e.g. / J. 같은 약어 뒤에서는 자르지 않음
            piece = para[start:end].strip()
            if piece:
                out.append(piece)
            start = end
        tail = para[start:].strip()
        if tail:
            out.append(tail)
    return out


# ---------------------------------------------------------------- 색 표시 위치 계산

def segments(sentence: str, tags: list[dict]) -> list[dict]:
    """문장 + 태그 목록 → [{"text", "type"|None, "label"}] 조각들 (겹치지 않게, 문장 순서대로).

    태그 text가 문장에 없으면 버립니다. 같은 구절이 여러 번 나오면 아직 칠하지 않은 첫 위치를 씁니다.
    """
    spans: list[tuple[int, int, str, str]] = []
    taken = [False] * len(sentence)
    for t in tags or []:
        frag = (t.get("text") or "").strip()
        typ = t.get("type")
        if not frag or typ not in TAG_TYPES:
            continue
        pos = 0
        while True:
            i = sentence.find(frag, pos)
            if i < 0:
                low = sentence.lower().find(frag.lower(), pos)
                i = low
            if i < 0:
                break
            j = i + len(frag)
            if not any(taken[i:j]):
                spans.append((i, j, typ, (t.get("label") or "").strip(), (t.get("par") or "").strip()[:8]))
                for k in range(i, j):
                    taken[k] = True
                break
            pos = i + 1
    spans.sort()
    out, cur = [], 0
    for i, j, typ, label, par in spans:
        if i > cur:
            out.append({"text": sentence[cur:i], "type": None, "label": ""})
        out.append({"text": sentence[i:j], "type": typ, "label": label, "par": par})
        cur = j
    if cur < len(sentence):
        out.append({"text": sentence[cur:], "type": None, "label": ""})
    return out


# ---------------------------------------------------------------- 정리 · 검증

def clean_result(raw: dict, sentences: list[str]) -> tuple[dict, list[str]]:
    """AI 답 → 저장할 모양. (결과, 경고)"""
    warns: list[str] = []
    by_n: dict[int, dict] = {}
    for s in raw.get("sentences") or []:
        if not isinstance(s, dict):
            continue
        try:
            n = int(s.get("n"))
        except (TypeError, ValueError):
            continue
        by_n[n] = s

    rows = []
    for n, text in enumerate(sentences, 1):
        s = by_n.get(n, {})
        tags = [t for t in (s.get("tags") or []) if isinstance(t, dict)]
        segs = segments(text, tags)
        lost = sum(1 for t in tags if (t.get("text") or "").strip() and not any(
            seg["type"] and seg["text"].lower() == t["text"].strip().lower() for seg in segs))
        if lost:
            warns.append(f"{n}번 문장: 색 표시 {lost}개는 문장에서 찾지 못해 뺐습니다.")
        raw_points = s.get("points") if isinstance(s.get("points"), list) else ([s["point"]] if isinstance(s.get("point"), dict) else [])
        points = []
        for pt in raw_points:
            if isinstance(pt, dict) and (pt.get("text") or "").strip():
                points.append({"type": pt.get("type") if pt.get("type") in POINT_TYPES else "reading", "text": pt["text"].strip()})
        rows.append(
            {
                "n": n,
                "text": text,
                "segments": segs,
                "chips": [str(c).strip() for c in (s.get("chips") or []) if str(c).strip()][:8],
                "ko": (s.get("ko") or "").strip(),
                "points": points[:MAX_POINTS],
                "topic": bool(s.get("topic")),
                "insertion": bool(s.get("insertion")),
            }
        )

    summary = raw.get("summary") if isinstance(raw.get("summary"), dict) else {}
    flow = []
    for step in summary.get("flow") or []:
        if isinstance(step, dict) and (step.get("ko") or "").strip():
            flow.append({"ko": step["ko"].strip(), "desc": (step.get("desc") or "").strip(), "en": (step.get("en") or "").strip()})
        elif isinstance(step, str) and step.strip():
            flow.append({"ko": step.strip(), "en": ""})
    vocab = []
    for v in raw.get("vocab") or []:
        if isinstance(v, dict) and (v.get("word") or "").strip():
            vocab.append({k: (v.get(k) or "").strip() if isinstance(v.get(k), str) else ", ".join(v.get(k) or [])
                          for k in ("word", "meaning", "syn", "ant")})
    result = {
        "title_en": (raw.get("title_en") or "").strip()[:120],
        "title_ko": (raw.get("title_ko") or "").strip()[:80],
        "sentences": rows,
        "summary": {
            "topic": (summary.get("topic") or "").strip(),
            "flow": flow[:6],
            "background": (summary.get("background") or "").strip(),
        },
        "vocab": vocab[:20],
        "visual": clean_visual(raw.get("visual")),
    }
    return result, warns


def clean_visual(v) -> dict | None:
    """정리 그림(4컷): {title, panels[{icon, head, text}], message}. 아이콘은 정해진 목록에서만."""
    if not isinstance(v, dict):
        return None
    panels = []
    for x in v.get("panels") or []:
        if isinstance(x, dict) and (x.get("head") or "").strip():
            icon = str(x.get("icon") or "").strip()
            panels.append({"icon": icon if icon in ICONS else "idea", "head": x["head"].strip()[:30], "text": (x.get("text") or "").strip()[:160]})
    if len(panels) < 2:
        return None
    return {"title": (v.get("title") or "").strip()[:60], "panels": panels[:4], "message": (v.get("message") or "").strip()[:120]}


def sentence_points(row: dict) -> list[dict]:
    """예전 저장본(point 하나) 호환."""
    if row.get("points") is not None:
        return row["points"]
    return [row["point"]] if row.get("point") else []


def validate(result: dict, sentences: list[str]) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    rows = result.get("sentences") or []
    missing_ko = [r["n"] for r in rows if not r.get("ko")]
    if len(rows) != len(sentences):
        errs.append(f"문장 {len(sentences)}개 중 {len(rows)}개만 분석했습니다.")
    if missing_ko:
        errs.append(f"해석이 빠진 문장: {', '.join(map(str, missing_ko[:8]))}번")
    if not result["summary"]["topic"]:
        errs.append("주제가 비어 있습니다.")
    if len(result["summary"]["flow"]) < 2:
        warns.append("흐름 단계가 너무 적습니다.")
    if len(result["vocab"]) < 5:
        warns.append(f"중요 어휘가 {len(result['vocab'])}개뿐입니다.")
    without = sum(1 for r in rows if not sentence_points(r))
    if rows and without > len(rows) * 0.4:
        errs.append(f"포인트 카드가 없는 문장이 {without}개입니다. 모든 문장에 독해 포인트를 1개 이상 달아 주세요.")
    if rows and not any(r.get("topic") for r in rows):
        warns.append("주제문 표시가 없습니다.")
    if sum(1 for r in rows if not any(s["type"] for s in r["segments"])) > len(rows) * 0.5:
        warns.append("색 표시가 없는 문장이 절반이 넘습니다.")
    return errs, warns


# ---------------------------------------------------------------- 프롬프트

SYSTEM_PROMPT = """당신은 대한민국 고등학교 내신·수능 영어 교재 집필 전문가이자 스타 강사입니다.
번호가 붙은 영어 문장들(한 지문)을 받아, 학생용 '지문 분석본'의 내용을 JSON으로 만듭니다.

## 문장마다 (모든 번호를 하나도 빠짐없이, 받은 번호 그대로)
- tags: 색으로 표시할 단어·구. text는 그 문장에 실제로 있는 글자 그대로(대소문자 포함) 적습니다. 한 문장에 2~6개.
  type: "grammar"(문법 요소: 접속사·관계사·분사·가정법·수동태·간접의문문 등, 빨강) / "vocab"(뜻을 알아야 하는 어휘·숙어, 파랑) /
        "both"(문법 구조도 걸리는 어휘·숙어, 보라) / "connective"(however, therefore 같은 연결어, 회색 밑줄) /
        "parallel"(and·or로 묶인 병렬 요소 중 문법·어휘 표시가 없는 것, 청록) / "highlight"(목표 어법 구조를 통째로 형광펜).
  label: 단어 바로 아래에 작게 쓰는 설명. grammar는 문법 용어(예: "명사절 접속사", "수동태(수행되다)"), vocab·both는 뜻(예: "극복하다"),
         connective는 역할(예: "역접"), parallel은 "병렬1-①"처럼. 어휘가 병렬 요소이기도 하면 label을 "강조하다 · 병렬1-①"처럼 함께 씁니다.
  par: 병렬 요소면 "1-①", "1-②"(병렬 묶음 번호-순서), 아니면 빈 문자열. 병렬은 그 문장에서 의미 있는 것만.
- ko: 자연스럽고 정확한 문어체 한국어 해석.
- topic: 글의 주제문이면 true (지문 전체에서 1개, 많아야 2개).
- points: 이 문장 옆에 붙는 설명 카드 1~2개 (모든 문장에 최소 1개).
  type: "reading"(독해 포인트: 문장의 역할, 지시어가 가리키는 것, 글쓴이의 의도) / "grammar"(어법 포인트: 시험에 나오는 구조와 근거) /
        "topic"(주제문일 때 그 문장이 왜 핵심인지) / "blank"(빈칸·서술형으로 낼 만한 숙어·핵심 표현) /
        "insertion"(흐름 전환점, 문장삽입 후보) / "implication"(함의추론).
  text: 다정하지만 핵심을 짚는 강사 말투(~해, ~야, ~거든, ~꼭 외워두자!)로 1~3문장. 어법은 근거와 함께.
- insertion: 역접 연결어로 시작하는 등 문장삽입 후보면 true.

## 지문 끝
- title_en, title_ko: 지문 내용을 나타내는 영문 제목과 한글 제목 (요청받았을 때만).
- summary.topic: 글의 주제 한 문장 (명사구로 끝나도 됨).
- summary.flow: 3~4단계, 각 {"ko": "도입", "desc": "그 단계 내용을 괄호 안에 넣을 짧은 구"} (예: 도입(신경윤리학을 통한 문제 해결 필요성 제시)).
- summary.background: 지문과 관련된 배경지식을 3~5문장 한 단락. 출처(작품·저자)를 알면 포함.
- vocab: 핵심 어휘 8~12개, 각 {"word", "meaning"(문맥상 뜻, 1~2개), "syn"(유의어 1개), "ant"(반의어, 없으면 "-")}.

맞춤법과 표준어를 지킵니다.

## 출력 (JSON 객체 하나만)
{
  "title_en": "...", "title_ko": "...",
  "sentences": [
    {"n": 1, "tags": [{"text": "that", "type": "grammar", "label": "명사절 접속사", "par": ""}, {"text": "overcome", "type": "vocab", "label": "극복하다", "par": ""}],
     "ko": "...", "topic": false,
     "points": [{"type": "reading", "text": "..."}, {"type": "grammar", "text": "..."}], "insertion": false}
  ],
  "summary": {"topic": "...", "flow": [{"ko": "도입", "desc": "..."}], "background": "..."},
  "vocab": [{"word": "...", "meaning": "...", "syn": "...", "ant": "..."}]
}"""

VISUAL_RULE = """
## 정리 그림 (추가로 요청됨)
- visual: 지문 내용을 한눈에 정리하는 4컷 그림의 내용.
  {"title": "그림 제목(한국어, 짧게)", "panels": [{"icon": "아이콘 이름", "head": "핵심어 2~8글자", "text": "한두 문장 설명(한국어)"} 4개, 글의 흐름 순서대로],
   "message": "그림 아래 한 줄 결론"}
  icon은 다음 중에서만 고릅니다: """ + ", ".join(ICONS) + "\n"


def user_prompt(sentences: list[str], *, need_title: bool, level: str = "", target: str = "", visual: bool = False) -> str:
    lines = []
    if level:
        lines.append(f"대상: {level}")
    if target:
        lines.append(f"목표 어법: {target} — 이 구조가 나오는 곳은 highlight 태그와 어법 포인트로 꼭 짚어 주세요.")
    if visual:
        lines.append("정리 그림(visual)도 만들어 주세요." + VISUAL_RULE)
    lines.append("제목: " + ('title_en, title_ko 도 만들어 주세요.' if need_title else "이미 있음 (title_en, title_ko는 빈 문자열로)"))
    lines.append(f"문장 수: {len(sentences)}개 (1~{len(sentences)}번 모두 분석)")
    lines.append("")
    lines += [f"[{i}] {s}" for i, s in enumerate(sentences, 1)]
    return "\n".join(lines)


def repair_prompt(errors: list[str]) -> str:
    return "직전 답에 아래 문제가 있었습니다. 규칙에 맞게 전체를 다시 JSON으로 주세요.\n- " + "\n- ".join(errors)


VISUAL_SYSTEM = """당신은 고등학생용 영어 지문 정리 자료를 만드는 교재 디자이너입니다.
지문을 한눈에 보여 주는 4컷 정리 그림의 내용을 JSON으로만 만듭니다.""" + VISUAL_RULE + """
출력: {"visual": {"title": "...", "panels": [{"icon": "...", "head": "...", "text": "..."}], "message": "..."}}"""


def visual_prompt(sentences: list[str], topic: str = "") -> str:
    """이미 분석이 끝난 지문에 정리 그림만 따로 만들 때."""
    lines = [f"글의 주제: {topic}" if topic else "", "지문:"] + sentences
    return "\n".join(x for x in lines if x)
