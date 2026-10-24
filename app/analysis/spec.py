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

TAG_TYPES = ("grammar", "vocab", "both", "connective", "highlight")
POINT_TYPES = {
    "grammar": "어법 포인트",
    "reading": "독해 포인트",
    "topic": "주제/요지",
    "blank": "빈칸·서술형",
    "insertion": "문장삽입",
    "implication": "함의추론",
}

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
                spans.append((i, j, typ, (t.get("label") or "").strip()))
                for k in range(i, j):
                    taken[k] = True
                break
            pos = i + 1
    spans.sort()
    out, cur = [], 0
    for i, j, typ, label in spans:
        if i > cur:
            out.append({"text": sentence[cur:i], "type": None, "label": ""})
        out.append({"text": sentence[i:j], "type": typ, "label": label})
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
        point = s.get("point") if isinstance(s.get("point"), dict) else None
        if point:
            ptype = point.get("type") if point.get("type") in POINT_TYPES else "grammar"
            ptext = (point.get("text") or "").strip()
            point = {"type": ptype, "text": ptext} if ptext else None
        rows.append(
            {
                "n": n,
                "text": text,
                "segments": segs,
                "chips": [str(c).strip() for c in (s.get("chips") or []) if str(c).strip()][:8],
                "ko": (s.get("ko") or "").strip(),
                "point": point,
                "insertion": bool(s.get("insertion")),
            }
        )

    summary = raw.get("summary") if isinstance(raw.get("summary"), dict) else {}
    flow = []
    for step in summary.get("flow") or []:
        if isinstance(step, dict) and (step.get("ko") or "").strip():
            flow.append({"ko": step["ko"].strip(), "en": (step.get("en") or "").strip()})
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
    }
    return result, warns


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
    points = sum(1 for r in rows if r.get("point"))
    if rows and points == 0:
        warns.append("포인트 박스가 하나도 없습니다.")
    elif rows and points > max(2, len(rows) * 0.6):
        warns.append("포인트 박스가 너무 많습니다 (출제 가능성 높은 문장만 고르는 게 좋아요).")
    if sum(1 for r in rows if not any(s["type"] for s in r["segments"])) > len(rows) * 0.5:
        warns.append("색 표시가 없는 문장이 절반이 넘습니다.")
    return errs, warns


# ---------------------------------------------------------------- 프롬프트

SYSTEM_PROMPT = """당신은 대한민국 고등학교 내신·수능 영어 교재 집필 전문가이자 스타 강사입니다.
번호가 붙은 영어 문장들(한 지문)을 받아, 학생용 '지문 분석본'의 내용을 JSON으로 만듭니다.

## 문장마다 (모든 번호를 하나도 빠짐없이, 받은 번호 그대로)
- tags: 색으로 표시할 단어·구. text는 그 문장에 실제로 있는 글자 그대로(대소문자 포함) 적습니다.
  type: "grammar"(순수 문법 요소: 관계사, 분사구문, 가정법, 수동태 등) / "vocab"(뜻을 알아야 하는 어휘) /
        "both"(숙어이면서 문법 구조도 걸리는 표현) / "connective"(however, therefore 같은 연결어) /
        "highlight"(지각동사 구문처럼 긴 구조를 통째로 강조).
  label: grammar·both는 문법 용어(예: "관계대명사(주격)"), vocab은 뜻(예: "무자비하게 (= ruthlessly)"), connective는 "역접" 같은 역할.
  한 문장에 1~4개. 너무 쉬운 단어는 표시하지 않습니다.
- chips: 그 문장의 문법 개념·구조·시제를 짧은 칩으로 1~4개 (예: "가정법 과거완료", "5형식(사역)").
- ko: 자연스럽고 정확한 문어체 한국어 해석.
- point: 시험에 나올 만한 문장에만 (전체 문장의 20~40%). 아니면 null.
  type: "grammar"(어법) / "reading"(문장의 논리적 역할, 지시어가 가리키는 것) / "topic"(주제·요지 문장) /
        "blank"(빈칸·서술형으로 낼 만한 핵심 문장) / "insertion"(흐름 전환점, 문장삽입 후보) / "implication"(함의추론).
  text: 다정하지만 핵심을 날카롭게 짚는 강사 말투(~해, ~야, ~거든, ~단골 출제 포인트야) 2~4문장.
        어법이면 주어 수 일치, 사역·지각동사 목적격 보어, 관계대명사 vs 접속사, 관계부사·동격 that 구분, 도치 같은 단골 유형을 근거와 함께.
- insertion: 역접 연결어로 시작하는 등 문장삽입 후보면 true.

## 지문 끝
- title_en, title_ko: 지문 내용을 나타내는 영문 제목과 한글 제목 (요청받았을 때만).
- summary.topic: 글의 요지 한 문장.
- summary.flow: 3~5단계, 각 단계 {"ko": "문제 제기", "en": "Problem"}.
- summary.background: 지문과 관련된 배경지식(심리학, 과학, 사회학 이론, 작품 배경 등)을 심화 서술형 대비가 되도록 4~6문장 한 단락. 출처(작품·저자)를 알면 포함.
- vocab: 중요 어휘 8~12개, 각 {"word", "meaning"(문맥상 뜻), "syn"(유의어 1~3개, 쉼표), "ant"(반의어, 없으면 빈 문자열)}.

맞춤법과 표준어를 지킵니다.

## 출력 (JSON 객체 하나만)
{
  "title_en": "...", "title_ko": "...",
  "sentences": [
    {"n": 1, "tags": [{"text": "which", "type": "grammar", "label": "관계대명사(주격)"}],
     "chips": ["관계대명사(주격)"], "ko": "...",
     "point": {"type": "grammar", "text": "..."}, "insertion": false}
  ],
  "summary": {"topic": "...", "flow": [{"ko": "...", "en": "..."}], "background": "..."},
  "vocab": [{"word": "...", "meaning": "...", "syn": "...", "ant": "..."}]
}"""


def user_prompt(sentences: list[str], *, need_title: bool, level: str = "") -> str:
    lines = []
    if level:
        lines.append(f"대상: {level}")
    lines.append("제목: " + ('title_en, title_ko 도 만들어 주세요.' if need_title else "이미 있음 (title_en, title_ko는 빈 문자열로)"))
    lines.append(f"문장 수: {len(sentences)}개 (1~{len(sentences)}번 모두 분석)")
    lines.append("")
    lines += [f"[{i}] {s}" for i, s in enumerate(sentences, 1)]
    return "\n".join(lines)


def repair_prompt(errors: list[str]) -> str:
    return "직전 답에 아래 문제가 있었습니다. 규칙에 맞게 전체를 다시 JSON으로 주세요.\n- " + "\n- ".join(errors)
