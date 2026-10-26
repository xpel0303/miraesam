"""학교 경향분석: 같은 학교·학년의 기출 분석(ExamPaper)을 모아 숫자로 비교하고, Gemini가 출제 스타일 메모를 씁니다.

숫자(문항 수·배점·유형 분포·지문 길이)는 코드가 세고, 메모(어휘·어법·독해·서술형 경향, 대비 전략)만 AI가 씁니다.
메모는 동형 모의고사를 만들 때 출제 지시문에 같이 들어갑니다.
"""
import json
import re

from ..exam import spec as exam_spec

MEMO_KEYS = ["summary", "vocab", "grammar", "reading", "writing", "strategy"]
MEMO_NAMES = {"summary": "한눈에 보는 출제 경향", "vocab": "어휘", "grammar": "어법", "reading": "독해·지문 변형",
              "writing": "서술형", "strategy": "다음 시험 대비 전략"}


def paper_name(p) -> str:
    bits = []
    if p.year:
        bits.append(f"{p.year}")
    if p.semester:
        bits.append(f"{p.semester}학기")
    if p.term:
        bits.append(f"{p.term}차")
    return " ".join(bits) or p.title


def compare(papers: list) -> dict:
    """여러 회 비교 표: 회차별 숫자 + 유형 × 회차 행렬 + 평균."""
    rows, types = [], {}
    for p in papers:
        qs = p.questions or []
        st = exam_spec.stats(qs)
        rows.append({"id": p.id, "name": paper_name(p), "title": p.title, "range": (p.meta or {}).get("range", ""), **st})
        for t in st["types"]:
            types.setdefault(t["type"], {})[p.id] = t["count"]
    order = [t for t in exam_spec.TYPE_ORDER if t in types] + sorted(t for t in types if t not in exam_spec.TYPE_ORDER)
    matrix = [{"type": t, "cells": [types[t].get(r["id"], 0) for r in rows], "avg": round(sum(types[t].values()) / len(rows), 1)} for t in order]
    avg = lambda k: round(sum(r[k] for r in rows) / len(rows), 1) if rows else 0
    return {"rows": rows, "matrix": matrix,
            "avg": {k: avg(k) for k in ("n", "mc", "sa", "sa_share", "passages", "avg_words")}}


def _compact(p) -> dict:
    """메모를 쓸 때 AI에게 보여 줄 한 회 요약 (지문 전체는 빼고 앞부분만)."""
    out = []
    for q in p.questions or []:
        d = {"no": q["label"], "kind": q.get("kind"), "pts": q.get("points"), "type": q.get("type"), "stem": q.get("stem", "")[:90]}
        if q.get("point"):
            d["point"] = q["point"][:80]
        if q.get("box"):
            d["box"] = q["box"][:160]
        if q.get("conditions"):
            d["cond"] = q["conditions"][:200]
        if q.get("choices") and exam_spec.words(q.get("passage", "")) < 40:
            d["choices"] = q["choices"][:5]
        pw = exam_spec.words(q.get("passage", ""))
        if pw:
            d["passage_words"] = pw
            d["passage_start"] = " ".join(re.sub(r"\[/?u\]", "", q["passage"]).split()[:18])
        out.append(d)
    return {"exam": paper_name(p), "range": (p.meta or {}).get("range", ""), "questions": out}


SYSTEM = """당신은 한국 고등학교 영어 내신 기출을 분석하는 입시 컨설턴트입니다. 한 학교의 여러 회 시험을 보고 출제 경향을 학원 선생님과 학생이 바로 쓸 수 있게 정리합니다.
- 숫자로 확인되는 사실만 말하고, 추측은 '~로 보임'으로 표시합니다.
- 일반론(열심히 공부하자 등)은 쓰지 않고, 이 학교 문항에서 실제로 보이는 패턴을 구체적으로 씁니다.
JSON 객체 하나로만 답합니다."""


def memo_prompt(school: str, grade: str, papers: list) -> str:
    data = [_compact(p) for p in papers]
    return "\n".join([
        f"학교: {school} {grade}", f"시험 {len(papers)}회 문항 목록:", json.dumps(data, ensure_ascii=False), "",
        "출력 형식 (모두 한국어, 각 항목 1~4문장 또는 3~6개 목록):",
        '{"summary": ["가장 중요한 출제 경향 3~5개"],',
        ' "vocab": "어휘 문항 출제 방식 (외부 어휘 여부, 오답 구성: 같은 어원·철자 비슷·다의어 등, 대표 예)",',
        ' "grammar": ["반복해서 나온 어법 포인트"],',
        ' "reading": "독해 유형 구성과 지문 변형 방식 (교과서/모의고사 비중, 원래 유형을 다른 유형으로 바꿔 내는지, 반의어 치환 등)",',
        ' "writing": "서술형 유형·조건·채점 방식의 특징 (예: 주어진 단어 그대로/어형 변화, 시작·끝 단어 지정, 부분 점수)",',
        ' "strategy": ["다음 시험 대비 전략 3~5개, 이 학교 패턴에 맞춘 구체적인 공부법"]}',
    ])


def clean_memo(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k in MEMO_KEYS:
        v = raw.get(k)
        if isinstance(v, list):
            v = [re.sub(r"\s+", " ", str(x)).strip() for x in v if str(x).strip()][:8]
        else:
            v = re.sub(r"\s+", " ", str(v or "")).strip()
        if v:
            out[k] = v
    return out


def memo_text(memo: dict) -> str:
    """동형 모의고사 출제 지시문에 넣을 짧은 글."""
    lines = []
    for k in MEMO_KEYS:
        if k == "strategy" or k not in memo:
            continue
        v = memo[k]
        lines.append(f"- {MEMO_NAMES[k]}: " + (" / ".join(v) if isinstance(v, list) else v))
    return "\n".join(lines)
