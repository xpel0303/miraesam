"""문장 시험지 규칙.

지문 → 문장(코드가 나눔) → 문장마다 AI는 '우리말 해석'과 '빈칸으로 만들 핵심 단어'만 줍니다.
유형
  translate  해석 쓰기   영어 → 우리말
  compose    영작        우리말 → 영어 (힌트: 단어 수 / 첫 단어)
  arrange    단어 배열   우리말 + 섞은 단어 → 영어  (단어 섞기는 코드)
  blank      빈칸 채우기 핵심 단어를 빈칸으로, 첫 글자 힌트 (빈칸 만들기는 코드)
"""
import random
import re

from ..analysis.spec import split_sentences
from ..comprehension.spec import answer_words, make_given

TYPES = {"translate": "해석 쓰기", "compose": "영작", "arrange": "단어 배열", "blank": "빈칸 채우기"}
STEMS = {
    "translate": "다음 문장을 우리말로 해석하시오.",
    "compose": "다음 우리말을 영어로 쓰시오.",
    "arrange": "우리말과 같은 뜻이 되도록 주어진 단어를 배열하시오.",
    "blank": "우리말을 참고하여 빈칸에 알맞은 말을 쓰시오. (첫 글자 힌트)",
}
MAX_PASSAGES = 30
MAX_SENTENCES = 40
ARRANGE_MIN, ARRANGE_MAX = 4, 22  # 단어 배열은 너무 짧거나 긴 문장 제외


def sentences_of(text: str) -> list[str]:
    return split_sentences(text)


def _find_word(sentence: str, word: str) -> re.Match | None:
    return re.search(r"(?<![A-Za-z0-9'])" + re.escape(word) + r"(?![A-Za-z0-9'])", sentence)


def blank_pieces(sentence: str, keys: list[str]) -> list[dict]:
    """문장을 [{"text"}, {"blank": 정답, "hint": 첫 글자}] 조각으로. 못 찾은 키는 건너뜀."""
    spans = []
    for k in keys:
        m = _find_word(sentence, k)
        if m and not any(a < m.end() and m.start() < b for a, b, _ in spans):
            spans.append((m.start(), m.end(), m.group()))
    spans.sort()
    out, cur = [], 0
    for a, b, w in spans:
        if a > cur:
            out.append({"text": sentence[cur:a]})
        out.append({"blank": w, "hint": w[0]})
        cur = b
    if cur < len(sentence):
        out.append({"text": sentence[cur:]})
    return out


def clean(raw: dict, sentences: list[str]) -> tuple[list[dict], list[str]]:
    by_n = {}
    for x in raw.get("sentences") or []:
        try:
            by_n[int(x.get("n"))] = x
        except (TypeError, ValueError, AttributeError):
            continue
    rows, warns = [], []
    for n, en in enumerate(sentences, 1):
        x = by_n.get(n, {})
        keys = [str(k).strip() for k in (x.get("keys") or []) if str(k).strip()]
        found = [k for k in keys if _find_word(en, k)]
        if keys and len(found) < len(keys):
            warns.append(f"{n}번 문장: 빈칸 단어 일부를 문장에서 찾지 못해 뺐어요.")
        rows.append({"n": n, "en": en, "ko": re.sub(r"\s+", " ", str(x.get("ko") or "")).strip(), "keys": found[:3]})
    return rows, warns


def validate(rows: list[dict], sentences: list[str]) -> list[str]:
    errs = []
    if len(rows) != len(sentences):
        errs.append("문장 수가 맞지 않습니다.")
    missing = [r["n"] for r in rows if not r["ko"]]
    if missing:
        errs.append(f"해석이 빠진 문장: {', '.join(map(str, missing[:10]))}번")
    nokey = [r["n"] for r in rows if not r["keys"] and len(answer_words(r["en"])) >= 4]
    if len(nokey) > len(rows) // 2:
        errs.append(f"빈칸 단어가 없는 문장이 많습니다: {', '.join(map(str, nokey[:10]))}번")
    return errs


def build_sections(passage: dict, types: list[str], shuffle: bool, hint: str, seed: int) -> list[dict]:
    """시험지에 넣을 유형별 문항 묶음 (지문 하나)."""
    rows = passage.get("rows") or []
    rng = random.Random(seed)
    sections = []
    for t in types:
        items = [r for r in rows if r["ko"]]
        if t == "arrange":
            items = [r for r in items if ARRANGE_MIN <= len(answer_words(r["en"])) <= ARRANGE_MAX]
        if t == "blank":
            items = [r for r in items if r["keys"]]
        if shuffle and t != "translate":
            items = items[:]
            rng.shuffle(items)
        qs = []
        for r in items:
            q = {"n": r["n"], "en": r["en"], "ko": r["ko"]}
            if t == "arrange":
                q["given"] = make_given(r["en"], seed=f"{seed}-{r['n']}")
            elif t == "compose":
                words = answer_words(r["en"])
                q["hint"] = (f"{len(words)}단어" if hint == "count" else f"{words[0]} …" if hint == "first" else "")
            elif t == "blank":
                q["pieces"] = blank_pieces(r["en"], r["keys"])
            qs.append(q)
        if qs:
            sections.append({"type": t, "items": qs})
    return sections


SYSTEM_PROMPT = """당신은 한국 고등학생용 영어 문장 시험지를 만드는 교사입니다. JSON 객체 하나로만 답합니다.
번호가 붙은 문장마다:
- ko: 자연스럽고 정확한 문어체 우리말 해석. 학생이 영작할 때 영어 원문이 떠오르도록 원문의 구조를 크게 벗어나지 않게.
- keys: 빈칸으로 만들 핵심 단어 1~2개. 문장에 실제로 있는 글자 그대로의 한 단어(대소문자 포함).
  시험에 나올 만한 동사·형용사·명사·연결어를 고르고, a/the/is 같은 쉬운 단어나 고유명사는 피합니다.
모든 번호를 빠짐없이, 받은 번호 그대로.
출력: {"sentences": [{"n": 1, "ko": "...", "keys": ["..."]}]}"""


def prompt(sentences: list[str], level: str, notes: list[str] | None = None) -> str:
    lines = [f"대상: {level or '고등학생'}", f"문장 {len(sentences)}개:"] + [f"[{i}] {s}" for i, s in enumerate(sentences, 1)]
    if notes:
        lines.append("\n직전 답의 문제점 (규칙에 맞게 전체 다시):\n- " + "\n- ".join(notes))
    return "\n".join(lines)
