"""어법 문제 규칙.

문장형 (목표어법으로 출제)
  paren     괄호 선택      I enjoy (to read / reading) books.
  blank     빈칸 5지선다   빈칸에 들어갈 말로 알맞은 것은?
  pick      문장 고르기    어법상 틀린(옳은) 문장은?
  arrange   단어 배열 서술형 (단어는 코드가 정답에서 뽑아 섞음)
  transform 문장 전환 서술형
지문형 (지문을 넣었을 때, 지문마다)
  under5    밑줄 5지선다   밑줄 친 부분 중 어법상 틀린 것은?  (틀린 것 정확히 1개)
  count     어법 개수      밑줄 친 부분 중 어법상 틀린 것의 개수는?  (틀린 것 1~4개)

밑줄은 AI가 "밑줄 칠 구절 목록"만 주고, 코드가 지문에서 순서대로 위치를 찾아 붙입니다.
"""
import re

from ..comprehension.spec import answer_words, make_given, norm_words

TYPES = {
    "paren": "괄호 선택",
    "blank": "빈칸 5지선다",
    "pick": "문장 고르기",
    "arrange": "단어 배열 서술형",
    "transform": "문장 전환 서술형",
    "under5": "밑줄 5지선다",
    "count": "어법 개수",
}
SENTENCE_TYPES = ["paren", "blank", "pick", "arrange", "transform"]
PASSAGE_TYPES = ["under5", "count"]
DEFAULT_COUNTS = {"paren": 6, "blank": 4, "pick": 3, "arrange": 4, "transform": 3}
MAX_PER_TYPE = 15
MAX_PASSAGES = 3
CIRCLED = "①②③④⑤"
LETTERS = "ⓐⓑⓒⓓⓔ"

STEMS = {
    "paren": "괄호 안에서 어법상 알맞은 것을 고르시오.",
    "blank": "빈칸에 들어갈 말로 가장 알맞은 것은?",
    "pick_wrong": "다음 중 어법상 틀린 문장은?",
    "pick_right": "다음 중 어법상 옳은 문장은?",
    "arrange": "우리말과 같은 뜻이 되도록 괄호 안의 단어를 배열하시오.",
    "transform": "지시에 따라 문장을 바꿔 쓰시오.",
    "under5": "다음 글의 밑줄 친 부분 중, 어법상 틀린 것은?",
    "count": "다음 글의 밑줄 친 ⓐ~ⓔ 중, 어법상 틀린 것의 개수는?",
}


def _s(x) -> str:
    return re.sub(r"\s+", " ", str(x or "")).strip()


# ---------------------------------------------------------------- 밑줄 위치

def place_marks(passage: str, marks: list[dict]) -> list[dict] | None:
    """지문 + 밑줄 구절 목록 → [{"text", "mark": None | 번호(0부터)}] 조각. 하나라도 못 찾으면 None."""
    out, cur = [], 0
    for k, m in enumerate(marks):
        frag = m.get("text", "")
        if not frag:
            return None
        # 단어 중간(This 속 is 등)에 걸리지 않게 단어 경계로 찾기
        pat = re.compile(r"(?<![A-Za-z0-9'])" + re.escape(frag) + r"(?![A-Za-z0-9'])")
        found = pat.search(passage, cur)
        if found is None:
            return None
        i = found.start()
        if i > cur:
            out.append({"text": passage[cur:i], "mark": None})
        out.append({"text": frag, "mark": k})
        cur = i + len(frag)
    if cur < len(passage):
        out.append({"text": passage[cur:], "mark": None})
    return out


# ---------------------------------------------------------------- 정리 · 검증

def _choice_index(v, n=5) -> int:
    """3 / "3" / "③" / "3번" → 3. 알 수 없으면 0."""
    t = str(v or "").strip()
    if t[:1] in CIRCLED:
        return CIRCLED.index(t[0]) + 1
    m = re.search(r"\d+", t)
    i = int(m.group()) if m else 0
    return i if 1 <= i <= n else 0


def clean_sentence_items(raw: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {t: [] for t in SENTENCE_TYPES}
    for x in raw.get("paren") or []:
        sent = _s(x.get("sentence"))
        m = re.search(r"[{(\[]([^{}()\[\]]+?)\s*/\s*([^{}()\[\]]+?)[})\]]", sent)
        if not m:
            continue
        a, b = m.group(1).strip(), m.group(2).strip()
        ans = _s(x.get("answer"))
        out["paren"].append({"type": "paren", "before": sent[: m.start()], "options": [a, b], "after": sent[m.end():], "answer": ans, "explain": _s(x.get("explain"))})
    for x in raw.get("blank") or []:
        out["blank"].append({"type": "blank", "sentence": _s(x.get("sentence")), "choices": [_s(c) for c in (x.get("choices") or [])][:5],
                             "answer": _choice_index(x.get("answer")), "explain": _s(x.get("explain"))})
    for x in raw.get("pick") or []:
        ask = "right" if str(x.get("ask", "")).lower().startswith("r") else "wrong"
        out["pick"].append({"type": "pick", "ask": ask, "choices": [_s(c) for c in (x.get("choices") or [])][:5],
                            "answer": _choice_index(x.get("answer")), "explain": _s(x.get("explain"))})
    for x in raw.get("arrange") or []:
        ans = _s(x.get("answer"))
        out["arrange"].append({"type": "arrange", "ko": _s(x.get("ko")), "answer": ans, "given": make_given(ans), "condition": _s(x.get("condition")), "explain": _s(x.get("explain"))})
    for x in raw.get("transform") or []:
        out["transform"].append({"type": "transform", "original": _s(x.get("original")), "instruction": _s(x.get("instruction")),
                                 "answer": _s(x.get("answer")), "explain": _s(x.get("explain"))})
    return out


def clean_passage_item(raw: dict, qtype: str) -> dict | None:
    if not isinstance(raw, dict):
        return None
    passage = (raw.get("passage") or "").strip()
    marks = [{"text": str(m.get("text", "")).strip(), "wrong": bool(m.get("wrong")), "fix": _s(m.get("fix")), "why": _s(m.get("why"))}
             for m in (raw.get("marks") or []) if isinstance(m, dict)]
    return {"type": qtype, "passage": passage, "marks": marks, "explain": _s(raw.get("explain"))}


def validate(q: dict) -> list[str]:
    t, errs = q.get("type"), []
    if t == "paren":
        if q["answer"] not in q["options"]:
            errs.append("괄호 선택: 정답이 두 보기 중 하나가 아닙니다.")
    elif t in ("blank", "pick"):
        if len(q["choices"]) != 5 or any(not c for c in q["choices"]):
            errs.append(f"{TYPES[t]}: 보기가 5개여야 합니다.")
        if not q["answer"]:
            errs.append(f"{TYPES[t]}: 정답 번호(1~5)가 없습니다.")
        if t == "blank" and "___" not in q["sentence"]:
            errs.append("빈칸 5지선다: 문장에 빈칸(____)이 없습니다.")
    elif t == "arrange":
        if not q["ko"] or len(answer_words(q["answer"])) < 3:
            errs.append("단어 배열: 우리말 또는 정답 문장이 비었습니다.")
        elif norm_words(" ".join(q["given"])) != norm_words(q["answer"]):
            errs.append("단어 배열: 괄호 단어가 정답과 맞지 않습니다.")
    elif t == "transform":
        if not (q["original"] and q["instruction"] and q["answer"]):
            errs.append("문장 전환: 원래 문장·지시·정답 중 빈 것이 있습니다.")
    elif t in PASSAGE_TYPES:
        marks = q["marks"]
        if len(marks) != 5:
            errs.append(f"{TYPES[t]}: 밑줄이 5개여야 하는데 {len(marks)}개입니다.")
        elif place_marks(q["passage"], marks) is None:
            errs.append(f"{TYPES[t]}: 밑줄 구절을 지문에서 순서대로 찾지 못했습니다 (구절은 지문 글자 그대로, 앞에서부터 순서대로).")
        wrong = sum(m["wrong"] for m in marks)
        if t == "under5" and wrong != 1:
            errs.append(f"밑줄 5지선다: 틀린 밑줄이 정확히 1개여야 하는데 {wrong}개입니다.")
        if t == "count" and not 1 <= wrong <= 4:
            errs.append(f"어법 개수: 틀린 밑줄은 1~4개여야 하는데 {wrong}개입니다.")
        if any(m["wrong"] and not m["fix"] for m in marks):
            errs.append(f"{TYPES[t]}: 틀린 밑줄의 고친 형태(fix)가 없습니다.")
    return errs


def answer_of(q: dict) -> str:
    t = q["type"]
    if t == "paren":
        return q["answer"]
    if t in ("blank", "pick"):
        return CIRCLED[q["answer"] - 1] if q["answer"] else "?"
    if t in ("arrange", "transform"):
        return q["answer"]
    wrong = [i for i, m in enumerate(q["marks"]) if m["wrong"]]
    if t == "under5":
        return CIRCLED[wrong[0]] if wrong else "?"
    return f"{CIRCLED[len(wrong) - 1]} {len(wrong)}개 ({', '.join(LETTERS[i] for i in wrong)})" if wrong else "?"


# ---------------------------------------------------------------- 프롬프트

SYSTEM_PROMPT = """당신은 한국 고등학교 영어 내신 어법 문제를 출제하는 교사입니다. JSON 객체 하나로만 답합니다.
- 정답은 반드시 하나로만 떨어지게 만듭니다. 애매한 문항, 문맥상 둘 다 가능한 보기는 만들지 않습니다.
- 목표어법이 문제의 핵심이 되게 하되, 오답 보기는 학생이 실제로 헷갈리는 형태로 만듭니다.
- explain: 한국어로 1~2문장, 왜 그것이 정답인지 근거를 짚습니다.
- 영어 문장은 고등학생 수준의 자연스러운 문장으로, 지문이 주어지면 지문의 주제·어휘를 활용합니다."""

SENTENCE_FORMAT = """출력 형식 (요청한 개수만큼, 없으면 빈 배열):
{
  "paren": [{"sentence": "I enjoy {to read/reading} novels.", "answer": "reading", "explain": "..."}],
  "blank": [{"sentence": "She made her son ____ his room.", "choices": ["clean", "to clean", "cleaning", "cleaned", "cleans"], "answer": 1, "explain": "..."}],
  "pick": [{"ask": "wrong", "choices": ["문장1", "문장2", "문장3", "문장4", "문장5"], "answer": 3, "explain": "..."}],
  "arrange": [{"ko": "우리말 문장", "answer": "Complete English sentence.", "condition": "", "explain": "..."}],
  "transform": [{"original": "원래 문장", "instruction": "분사구문으로 바꾸시오", "answer": "바꾼 문장", "explain": "..."}]
}
규칙:
- paren: 문장 안에 {A/B} 괄호 하나. answer는 A 또는 B 글자 그대로.
- blank: 문장에 빈칸 ____ 하나, choices 정확히 5개, answer는 정답 번호 1~5.
- pick: ask는 "wrong"(틀린 문장 고르기) 또는 "right"(옳은 문장 고르기), choices 5문장, answer는 번호 1~5. wrong이면 틀린 문장이 정확히 1개, right이면 옳은 문장이 정확히 1개.
- arrange: answer의 단어를 섞어 학생에게 주므로(프로그램이 섞음) 다른 어순으로도 맞는 문장이 되지 않게 우리말에 단서를 넣습니다. condition은 "필요시 형태를 바꿀 것" 같은 조건이 있을 때만.
- transform: 목표어법을 쓰도록 바꾸는 문제. answer는 모범 답안 한 문장."""

PASSAGE_FORMAT = """출력 형식:
{
  "passage": "어법 오류를 넣은 지문 전체",
  "marks": [
    {"text": "지문 속 밑줄 칠 구절 (지문 글자 그대로)", "wrong": false, "fix": "", "why": "왜 맞는지 한 줄"},
    {"text": "...", "wrong": true, "fix": "고친 형태", "why": "왜 틀렸는지 한 줄"}
  ],
  "explain": "정답 해설 1~2문장"
}
규칙:
- 원래 지문을 거의 그대로 쓰되, 틀린 밑줄 자리만 어법상 틀린 형태로 바꿔 passage에 넣습니다.
- marks는 정확히 5개, 지문 앞에서부터 나오는 순서대로. text는 passage에 있는 글자 그대로(1~4단어), 서로 겹치지 않게.
- 밑줄은 시험 단골 어법 자리(수 일치, 동사/준동사, 관계사, 분사, 대명사, 병렬, 형용사/부사 등)에, 목표어법이 있으면 1~2개는 목표어법 자리에."""


def sentence_prompt(target: str, counts: dict[str, int], level: str, passages: list[str]) -> str:
    lines = [f"대상: {level or '고등학생'}", f"목표어법: {target or '고등 내신 빈출 어법 전반'}", "만들 개수:"]
    lines += [f"- {k} ({TYPES[k]}): {counts.get(k, 0)}개" for k in SENTENCE_TYPES]
    if passages:
        lines.append("\n참고 지문 (주제·어휘 활용, 지문 속 문장을 변형해도 좋음):")
        lines += [p.strip()[:2500] for p in passages]
    return "\n".join(lines) + "\n\n" + SENTENCE_FORMAT


def passage_prompt(passage: str, qtype: str, target: str, level: str) -> str:
    rule = ("틀린 밑줄은 정확히 1개, 나머지 4개는 맞는 형태." if qtype == "under5"
            else "틀린 밑줄은 2~3개(1~4개 범위), 나머지는 맞는 형태.")
    return (f"대상: {level or '고등학생'}\n목표어법: {target or '없음'}\n문제 유형: {TYPES[qtype]} — {rule}\n\n"
            f"원래 지문:\n{passage.strip()}\n\n{PASSAGE_FORMAT}")


def repair_prompt(errors: list[str]) -> str:
    return "직전 답에 아래 문제가 있었습니다. 규칙에 맞게 전체를 다시 JSON으로 주세요.\n- " + "\n- ".join(errors[:10])
