"""교과 연계: 영어 지문이 어느 과목·학년·단원(사회·과학, 2022 개정)과 이어지는지.

원장님이 정리한 data.md(교육과정 성취기준 기준 요약)를 단원 목록으로 읽고, Gemini에게 단원 목록 + 지문을 주어 고르게 함.
필요할 때만 쓰는 기능 — 지문 칸 위 '📚 교과 연계 찾기' 버튼.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).with_name("data.md")

SUBJ = {  # 장 제목 → (과목, 학년)
    "중학교 사회 ①": ("중학교 사회 ①", "중1~3"), "중학교 사회 ②": ("중학교 사회 ②", "중1~3"),
    "중학교 과학 1": ("중학교 과학 1", "중1"), "중학교 과학 2": ("중학교 과학 2", "중2"), "중학교 과학 3": ("중학교 과학 3", "중3"),
    "고등 통합사회 1": ("고등 통합사회 1", "고1"), "고등 통합사회 2": ("고등 통합사회 2", "고1"),
    "고등 통합과학 1": ("고등 통합과학 1", "고1"), "고등 통합과학 2": ("고등 통합과학 2", "고1"),
}


@lru_cache(maxsize=1)
def units() -> list[dict]:
    out, subj = [], None
    cur = None
    for line in DATA.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^## \d+\.\s*(.+)$", line)
        if m:
            subj = SUBJ.get(m.group(1).strip())
            cur = None
            continue
        m = re.match(r"^### (.+)$", line)
        if m and subj:
            cur = {"id": f"u{len(out) + 1}", "subject": subj[0], "grade": subj[1], "unit": m.group(1).strip(),
                   "area": "", "std": "", "concepts": [], "terms": ""}
            out.append(cur)
            continue
        if not cur:
            continue
        if (m := re.match(r"^- \*\*영역\*\*:\s*(.+)$", line)):
            cur["area"] = m.group(1).strip()
        elif (m := re.match(r"^- \*\*성취기준\*\*:\s*(.+)$", line)):
            cur["std"] = m.group(1).strip()
        elif (m := re.match(r"^\*\*핵심 용어\*\*:\s*(.+)$", line)):
            cur["terms"] = m.group(1).strip()
        elif line.startswith("- ") and not line.startswith("- **"):
            cur["concepts"].append(line[2:].strip())
    return out


SYSTEM = """당신은 한국 중·고등 사회·과학 교육과정(2022 개정)을 잘 아는 교과 연계 분석가입니다.
영어 지문을 읽고, 주어진 단원 목록에서 내용이 가장 가까운 단원을 고릅니다.
- 반드시 목록에 있는 id 만 씁니다. 억지로 이을 만한 단원이 없으면 빈 목록을 냅니다.
- 이유는 지문의 어떤 내용이 그 단원의 어떤 개념과 이어지는지 한국어 한 문장으로.
JSON 객체 하나로만 답합니다."""


def prompt(text: str) -> str:
    lines = [f"{u['id']} | {u['subject']} ({u['grade']}) | {u['unit']} | {u['area']} | {u['terms']}" for u in units()]
    return ("[단원 목록] id | 과목(학년) | 단원 | 영역 | 핵심 용어\n" + "\n".join(lines) +
            "\n\n[영어 지문]\n" + text.strip()[:6000] +
            '\n\n가장 가까운 단원을 1~3개, 가까운 순서로 고르세요.\n'
            '{"topic": "지문 주제 한국어 한 줄", "links": [{"id": "u12", "fit": "높음|보통", "reason": "...", "terms": ["지문과 겹치는 핵심 용어"]}]}')


def clean(raw) -> dict:
    raw = raw if isinstance(raw, dict) else (json.loads(raw) if isinstance(raw, str) else {})
    by = {u["id"]: u for u in units()}
    links = []
    for x in raw.get("links") or []:
        if not isinstance(x, dict) or x.get("id") not in by:
            continue
        u = by[x["id"]]
        links.append({"subject": u["subject"], "grade": u["grade"], "unit": u["unit"], "area": u["area"], "std": u["std"],
                      "fit": str(x.get("fit") or "보통")[:4], "reason": str(x.get("reason") or "")[:200],
                      "terms": [str(t)[:20] for t in (x.get("terms") or [])][:6],
                      "tag": f"{u['subject'].replace('고등 ', '').replace('중학교 ', '중 ')} {u['unit'].split('.')[0].strip()}"})
    return {"topic": str(raw.get("topic") or "")[:80], "links": links[:3]}
