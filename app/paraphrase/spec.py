"""지문 변형 규칙.

AI는 변형한 지문(과 바꾼 이유 몇 줄)만 줍니다. 변형도(%)와 원문 대조는 코드가 계산하므로
선생님이 변형 지문을 직접 고쳐도 대조·변형도가 그대로 다시 맞춰집니다.
"""
import difflib
import re

from ..analysis.spec import split_sentences

STRENGTHS = {
    "light": {"name": "약", "band": (8, 25), "rule": "어휘만 바꿉니다. 핵심 단어 일부를 문맥에 맞는 동의어·유사 표현으로 교체하고, 문장 구조와 순서는 그대로 둡니다."},
    "medium": {"name": "중", "band": (22, 45), "rule": "어휘 교체에 더해 문장 구조를 바꿉니다 (능동↔수동, 분사구문, 관계사절, 명사구↔절, 연결어 교체 등). 문장 순서와 문장 수는 유지합니다."},
    "strong": {"name": "강", "band": (40, 75), "rule": "내용과 논리 흐름은 같게 두되, 대부분의 문장을 다른 표현과 구조로 다시 씁니다. 두 문장을 합치거나 나눠도 됩니다."},
}
MAX_PASSAGES = 30
_TOKEN = re.compile(r"[A-Za-z0-9']+|[^\sA-Za-z0-9']")


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text)


def diff(original: str, changed: str) -> tuple[list[dict], int]:
    """단어 단위 대조 → ([{"op": equal|delete|insert, "text"}], 변형도%)."""
    a, b = tokens(original), tokens(changed)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    ops = []

    def join(ws):
        s = ""
        for w in ws:
            s += w if (not s or re.match(r"[^\sA-Za-z0-9'(\[\"“]", w) or s.endswith(("(", "[", "“", '"'))) else " " + w
        return s

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            ops.append({"op": "equal", "text": join(a[i1:i2])})
        else:
            if i2 > i1:
                ops.append({"op": "delete", "text": join(a[i1:i2])})
            if j2 > j1:
                ops.append({"op": "insert", "text": join(b[j1:j2])})
    for k, o in enumerate(ops):  # 문장부호 앞에는 띄우지 않음
        o["sp"] = k > 0 and not re.match(r"[.,;:!?)\]'’”]", o["text"])
    words_a = [w for w in a if re.match(r"[A-Za-z0-9]", w)]
    words_b = [w for w in b if re.match(r"[A-Za-z0-9]", w)]
    sm_w = difflib.SequenceMatcher(a=[w.lower() for w in words_a], b=[w.lower() for w in words_b], autojunk=False)
    ratio = round(100 * (1 - sm_w.ratio()))
    return ops, ratio


def change_list(ops: list[dict]) -> list[dict]:
    """대조 결과에서 '원문 → 변형' 쌍 목록 (교사용 표)."""
    out, i = [], 0
    while i < len(ops):
        o = ops[i]
        if o["op"] == "delete" and i + 1 < len(ops) and ops[i + 1]["op"] == "insert":
            out.append({"from": o["text"], "to": ops[i + 1]["text"]})
            i += 2
            continue
        if o["op"] == "delete":
            out.append({"from": o["text"], "to": "(삭제)"})
        elif o["op"] == "insert":
            out.append({"from": "(추가)", "to": o["text"]})
        i += 1
    return [c for c in out if re.search(r"[A-Za-z]", c["from"] + c["to"])]


def check(original: str, changed: str, strength: str) -> tuple[list[str], list[str], int]:
    """(다시 받을 사유, 경고, 변형도)."""
    errs, warns = [], []
    if not changed.strip():
        return ["변형 지문이 비어 있습니다."], [], 0
    _, ratio = diff(original, changed)
    lo, hi = STRENGTHS[strength]["band"]
    if ratio < lo:
        errs.append(f"변형도가 {ratio}%로 너무 낮습니다. {lo}~{hi}%가 되도록 더 바꿔 주세요.")
    elif ratio > hi:
        errs.append(f"변형도가 {ratio}%로 너무 높습니다. {lo}~{hi}%가 되도록 원문 표현을 더 살려 주세요.")
    n_a, n_b = len(split_sentences(original)), len(split_sentences(changed))
    if strength != "strong" and abs(n_a - n_b) > 1:
        errs.append(f"문장 수가 {n_a}개에서 {n_b}개로 바뀌었습니다. 문장 수를 유지해 주세요.")
    if len(changed) > len(original) * 1.6 or len(changed) < len(original) * 0.5:
        warns.append("변형 지문 길이가 원문과 많이 다릅니다.")
    return errs, warns, ratio


SYSTEM_PROMPT = """당신은 한국 고등학교 영어 내신 시험 출제 교사입니다. 학교 시험처럼 교과서·모의고사 원문을 변형합니다.
- 원문의 내용, 논리 흐름, 핵심 주장은 반드시 그대로 유지합니다. 사실을 바꾸거나 새 정보를 넣지 않습니다.
- 고등학생 수준의 자연스럽고 문법적으로 정확한 영어로 씁니다.
- 학생이 원문을 외워서는 풀 수 없도록, 하지만 원문을 이해했다면 알아볼 수 있도록 바꿉니다.
JSON 객체 하나로만 답합니다: {"text": "변형한 지문 전체", "notes": ["주요 변형 포인트 한 줄씩, 한국어, 3~6개"]}"""


def prompt(original: str, strength: str, level: str, focus: str, fix: list[str] | None = None) -> str:
    st = STRENGTHS[strength]
    lines = [f"대상: {level or '고등학생'}", f"변형 강도: {st['name']} — {st['rule']}"]
    if focus:
        lines.append(f"특히 신경 쓸 것: {focus}")
    if fix:
        lines.append("직전 답의 문제점 (고쳐서 다시):\n- " + "\n- ".join(fix))
    lines.append("\n원문:\n" + original.strip())
    return "\n".join(lines)
