"""내용이해 워크북 출제 규칙 (기존 comprehension-workbook 스킬과 같은 구성).

지문당 30문항:
  ox_ko_short 5 · ox_en_short 5 · ox_ko_deep 5 · ox_en_deep 5 · compose 5 · vocab 5

스킬과 다른 점
- 작문(compose)의 괄호 속 단어(given)는 AI가 쓰지 않고, 정답 문장에서 코드가 직접 뽑아 섞습니다.
  → '단어가 정답과 안 맞는' 오류가 생길 수 없음.
- AI 답에 규칙 위반(문항 수 등)이 있으면 위반 내용을 알려 주고 한 번 더 받습니다 (handlers 쪽).
"""
import random
import re

MAX_PASSAGES = 30

SECTIONS = [  # (key, 문항 수, 문제지 지시문)
    ("ox_ko_short", 5, "글의 내용과 일치하면 O, 일치하지 않으면 X를 고르시오."),
    ("ox_en_short", 5, "글의 내용과 일치하면 O, 일치하지 않으면 X를 고르시오."),
    ("ox_ko_deep", 5, "글의 내용과 일치하면 O, 일치하지 않으면 X를 고르시오."),
    ("ox_en_deep", 5, "글의 내용과 일치하면 O, 일치하지 않으면 X를 고르시오."),
    ("compose", 5, "다음 우리말과 같은 뜻이 되도록 괄호 안의 단어를 배열하여 영어 문장을 쓰시오."),
    ("vocab", 5, "주어진 단어의 뜻을 적으시오."),
]
SECTION_KEYS = [k for k, _, _ in SECTIONS]
PER_PASSAGE = sum(n for _, n, _ in SECTIONS)  # 30

LABELS = {
    "ox_ko_short": "OX 한글 (기본)",
    "ox_en_short": "OX 영어 (기본)",
    "ox_ko_deep": "OX 한글 (심화)",
    "ox_en_deep": "OX 영어 (심화)",
    "compose": "목표어법 작문",
    "vocab": "단어 뜻",
}


# ---------------------------------------------------------------- 작문 단어

_EDGE_PUNCT = re.compile(r"^[\"'“”‘(\[]+|[\"'“”’)\].,!?;:]+$")


def answer_words(answer: str) -> list[str]:
    """정답 문장 → 괄호에 줄 단어들 (문장부호 떼고, 대소문자·축약형은 그대로)."""
    words = []
    for raw in answer.replace("’", "'").split():
        w = _EDGE_PUNCT.sub("", raw)
        w = _EDGE_PUNCT.sub("", w)  # 따옴표+마침표가 겹친 경우
        if w:
            words.append(w)
    return words


def make_given(answer: str, seed=None) -> list[str]:
    """정답 단어를 섞되, 정답 순서와 같아지지 않게. seed가 없으면 같은 문장은 항상 같은 순서로 섞입니다."""
    words = answer_words(answer)
    if len(words) < 2:
        return words
    rng = random.Random(answer if seed is None else seed)
    shuffled = words[:]
    for _ in range(20):
        rng.shuffle(shuffled)
        if shuffled != words:
            break
    return shuffled


def norm_words(s: str) -> list[str]:
    return sorted(re.findall(r"[a-z0-9']+", s.lower().replace("’", "'")))


# ---------------------------------------------------------------- 정리 · 검증

def clean_passage_result(raw: dict) -> dict:
    """AI 답을 표준 모양으로 다듬습니다 (O/X 대문자, 빈칸 제거, 작문 단어는 코드로 생성)."""
    out: dict = {}
    for key in ("ox_ko_short", "ox_en_short", "ox_ko_deep", "ox_en_deep"):
        items = []
        for x in raw.get(key) or []:
            if not isinstance(x, dict):
                continue
            a = str(x.get("a", "")).strip().upper().replace("○", "O").replace("×", "X")
            items.append({"q": str(x.get("q", "")).strip(), "a": a, "why": str(x.get("why", "")).strip()})
        out[key] = items
    comp = []
    for x in raw.get("compose") or []:
        if not isinstance(x, dict):
            continue
        answer = re.sub(r"\s+", " ", str(x.get("answer", "")).strip())
        src = str(x.get("source", "")).strip()
        comp.append(
            {
                "ko": str(x.get("ko", "")).strip(),
                "answer": answer,
                "given": make_given(answer),
                "point": str(x.get("point", "")).strip(),
                "source": src if src in ("지문", "보충") else ("보충" if src else ""),
            }
        )
    out["compose"] = comp
    out["vocab"] = [
        {"word": str(x.get("word", "")).strip(), "meaning": str(x.get("meaning", "")).strip()}
        for x in (raw.get("vocab") or [])
        if isinstance(x, dict)
    ]
    if raw.get("passage_ko"):
        out["passage_ko"] = str(raw["passage_ko"]).strip()
    if raw.get("title"):
        out["title"] = str(raw["title"]).strip()[:80]
    return out


def validate_passage(p: dict, label: str = "") -> tuple[list[str], list[str]]:
    """(오류, 경고). 오류가 있으면 PDF를 만들지 않습니다."""
    errs, warns = [], []
    t = f"[{label}] " if label else ""
    if not (p.get("passage") or "").strip():
        errs.append(f"{t}지문 본문이 없습니다.")
    for key, n, _ in SECTIONS:
        items = p.get(key) or []
        name = LABELS[key]
        if len(items) != n:
            errs.append(f"{t}{name}: {n}문항이어야 하는데 {len(items)}문항입니다.")
        if key.startswith("ox_"):
            answers = [x.get("a") for x in items]
            if any(a not in ("O", "X") for a in answers):
                errs.append(f"{t}{name}: 정답은 O 또는 X여야 합니다.")
            elif len(items) > 1 and len(set(answers)) == 1:
                warns.append(f"{t}{name}: 정답이 모두 {answers[0]}입니다.")
            if any(not x.get("q") for x in items):
                errs.append(f"{t}{name}: 비어 있는 문항이 있습니다.")
            if any(not x.get("why") for x in items):
                warns.append(f"{t}{name}: 해설이 빠진 문항이 있습니다.")
        elif key == "compose":
            for j, x in enumerate(items, 1):
                if not x.get("ko") or not x.get("answer"):
                    errs.append(f"{t}작문 {j}번: 우리말 또는 정답 문장이 비어 있습니다.")
                    continue
                if norm_words(" ".join(x.get("given") or [])) != norm_words(x["answer"]):
                    errs.append(f"{t}작문 {j}번: 괄호 단어가 정답 문장과 맞지 않습니다.")
                if len(answer_words(x["answer"])) < 3:
                    warns.append(f"{t}작문 {j}번: 정답 문장이 너무 짧습니다.")
                if not x.get("point"):
                    warns.append(f"{t}작문 {j}번: 어법 설명이 비어 있습니다.")
        elif key == "vocab":
            if any(not x.get("word") or not x.get("meaning") for x in items):
                errs.append(f"{t}단어 뜻: 단어나 뜻이 빠진 문항이 있습니다.")
    return errs, warns


# ---------------------------------------------------------------- 프롬프트

SYSTEM_PROMPT = """당신은 한국 고등학교 영어 내신 문제를 만드는 출제 교사입니다.
주어진 영어 지문 하나로 '내용이해 워크북' 30문항을 JSON으로 만듭니다.

## 공통
- 지문 내용만 근거로 출제합니다. 지문에 없는 사실로 O/X를 만들지 않습니다.
- 학생용 문장은 쉬운 말로, 해설(why)은 한 줄로 짧게. 근거가 되는 지문 구절을 우선 적습니다.
- 지문이 대화(M/W)여도 문제에는 화자 기호 대신 "남자/여자"로 풀어 씁니다.

## OX 4세트 (각 정확히 5문항)
- ox_ko_short: 한글, 한 문장, 지문 핵심 사실 확인
- ox_en_short: 영어, 한 문장, 쉬운 어휘
- ox_ko_deep: 한글, 길고 추상적인 표현, 해석·추론 포함
- ox_en_deep: 영어, 길고 추상적인 표현, 지문 단어를 바꿔 쓴 패러프레이즈
- 세트마다 O 2~3개, X 2~3개로 섞습니다.
- X 문장은 정반대만 쓰지 말고 섞습니다: 정반대 / 숫자·시점 바꾸기 / 일부만 맞고 일부 틀림 / 지문에 없는 내용 추가 / 한정어(only, all, never) 과장.
- 심화 세트의 O 문장은 지문 단어를 그대로 베끼지 말고 바꿔 씁니다.
- 심화 세트의 X 문장은 그럴듯하지만 한 군데가 명확히 틀리게 만듭니다. 애매한 해석 문제는 피합니다.

## 목표어법 작문 compose (정확히 5문항)
목표어법이 있을 때:
1. 지문에서 목표어법이 실제로 쓰인 문장을 찾아 우선 출제합니다 (source: "지문").
2. 5개에 못 미치면 지문 주제·어휘에 맞는 보충 문장으로 채웁니다 (source: "보충").
3. 목표어법을 모르면 정답을 만들 수 없는 문장을 고릅니다.
목표어법이 없을 때: 지문의 핵심 문장 5개를 일반 영작으로 냅니다.
각 문항 필드:
- ko: 우리말 문장. 영어 정답이 하나로 떨어지도록 구체적으로.
- answer: 완성 영어 문장 하나 (마침표 포함). 학생은 answer의 단어를 섞어서 받으므로, 다른 어순으로도 맞는 문장이 되지 않게 우리말에 단서를 넣어 한 가지로 고정합니다. 축약형을 쓸지 풀어 쓸지도 answer 그대로 고정됩니다.
- point: 답지에 실릴 어법 설명 한 줄.
- source: "지문" 또는 "보충".
- 단어를 섞은 괄호(given)는 만들지 마세요. 프로그램이 answer로 만듭니다.

## 단어 뜻 vocab (정확히 5문항)
- 지문에 나온 단어 중 학습 가치가 높은 5개. 고유명사, 너무 쉬운 단어는 피합니다.
- meaning: 지문 문맥에 맞는 대표 뜻 1~2개.

## 출력 (JSON 객체 하나만)
{
  "title": "지문 제목 (요청받았을 때만)",
  "passage_ko": "지문 전체 한글 해석 (요청받았을 때만)",
  "ox_ko_short": [{"q": "...", "a": "O", "why": "..."}],
  "ox_en_short": [...],
  "ox_ko_deep": [...],
  "ox_en_deep": [...],
  "compose": [{"ko": "...", "answer": "...", "point": "...", "source": "지문"}],
  "vocab": [{"word": "...", "meaning": "..."}]
}"""


def user_prompt(passage: str, *, target_grammar: str = "", need_title: bool = False, need_translation: bool = True) -> str:
    lines = []
    lines.append(f"목표어법: {target_grammar.strip()}" if target_grammar.strip() else "목표어법: 없음 (작문은 일반 영작)")
    asks = []
    if need_title:
        asks.append('"title" (지문 내용을 나타내는 짧은 한국어 제목, 20자 이내)')
    if need_translation:
        asks.append('"passage_ko" (지문 전체를 자연스러운 한국어로 번역, 문단 구분은 줄바꿈으로)')
    lines.append("추가로 만들 것: " + (", ".join(asks) if asks else "없음 (title, passage_ko는 넣지 마세요)"))
    lines.append("")
    lines.append("지문:")
    lines.append(passage.strip())
    return "\n".join(lines)


def repair_prompt(previous_errors: list[str]) -> str:
    return (
        "직전 답에 아래 문제가 있었습니다. 규칙에 맞게 30문항 전체를 다시 JSON으로 주세요.\n- "
        + "\n- ".join(previous_errors)
    )
