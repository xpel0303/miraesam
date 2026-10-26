"""숙제 규칙 (AI 없음).

할 일(task) 종류
  vocab  단어 퀴즈      {list_id, mode, count, pass}           — 단어장 퀴즈를 그대로 씀
  ox     OX 퀴즈        {source, items[{q, a, why, p}], passages[{title, text}], pass}  — 내용이해 워크북 OX를 복사
  omr    답 입력        {source, items[{n, a}], pass}           — 종이로 푼 변형문제·어법 문제(또는 직접 넣은 정답)의 답만 입력
  leveltest 레벨테스트 {test_id}                                — 레벨테스트를 풀고 제출하면 완료 (점수 = 최고 점수)
  check  할 일 체크     {text}                                  — 본문 암기처럼 학생이 '했어요'를 누름

pass = 통과 점수(%). 0 이면 한 번 내면 완료.
"""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from ..comprehension.spec import SECTIONS as WB_SECTIONS
from ..reports.spec import CIRCLED, parse_key
from ..variant import spec as vspec
from ..vocab.spec import QUIZ_MODES

KINDS = {"vocab": "단어 퀴즈", "ox": "OX 퀴즈", "omr": "답 입력", "leveltest": "레벨테스트", "check": "할 일 체크"}
MAX_TASKS = 8
OX_LEVELS = {"short": "기본만", "deep": "심화만", "all": "기본+심화"}
_C2N = {c: i + 1 for i, c in enumerate("①②③④⑤")}


def today() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")


def is_late(due: str) -> bool:
    return bool(due) and today() > due


def _pct(v, default: int) -> int:
    try:
        return max(0, min(100, int(str(v).strip())))
    except (TypeError, ValueError):
        return default


# ---------------- 할 일 만들기 (원본 자료 → 숙제용 복사본) ----------------

def task_vocab(wl, mode: str, count, pass_) -> dict:
    n = len([w for w in wl.words if w.get("word") and w.get("meaning")])
    try:
        count = min(n, max(1, int(count or 20)))
    except ValueError:
        count = min(n, 20)
    return {"kind": "vocab", "title": f"단어 퀴즈 · {wl.title}", "list_id": wl.id, "mode": mode if mode in QUIZ_MODES else "mc_en",
            "count": count, "pass": _pct(pass_, 80)}


def task_ox(wb, level: str, pass_) -> dict:
    keys = [k for k, _, _ in WB_SECTIONS if k.startswith("ox_") and (level == "all" or k.endswith(level))]
    items, passages = [], []
    for p in wb.passages or []:
        if p.get("status") != "done":
            continue
        pi = len(passages)
        passages.append({"title": p.get("title") or f"지문 {pi + 1}", "text": p.get("passage", "")})
        for k in keys:
            for x in p.get(k) or []:
                if x.get("q") and x.get("a") in ("O", "X"):
                    items.append({"q": x["q"], "a": x["a"], "why": x.get("why", ""), "p": pi})
    return {"kind": "ox", "title": f"OX 퀴즈 · {wb.title}", "source": f"workbook:{wb.id}", "items": items, "passages": passages,
            "level": level if level in OX_LEVELS else "all", "pass": _pct(pass_, 0)}


def variant_key(vs) -> list[dict]:
    out = []
    for i, q in enumerate(vs.questions or []):
        a = _C2N.get(vspec.answer_text(q)[:1], 0)
        if a:
            out.append({"n": i + 1, "a": a})
    return out


def grammar_key(gs) -> list[dict]:
    """객관식으로 답할 수 있는 문항만 (빈칸·고르기·밑줄 5지선다·어법 개수). 괄호·배열·바꿔 쓰기는 빠짐."""
    out = []
    for i, q in enumerate(gs.questions or []):
        t = q.get("type")
        if t in ("blank", "pick") and q.get("answer"):
            out.append({"n": i + 1, "a": int(q["answer"])})
        elif t in ("under5", "count"):
            wrong = [k for k, m in enumerate(q.get("marks") or []) if m.get("wrong")]
            if t == "under5" and len(wrong) == 1:
                out.append({"n": i + 1, "a": wrong[0] + 1})
            elif t == "count" and 1 <= len(wrong) <= 5:
                out.append({"n": i + 1, "a": len(wrong)})
    return out


def task_omr(title: str, source: str, items: list[dict], pass_) -> dict:
    return {"kind": "omr", "title": title, "source": source, "items": items, "pass": _pct(pass_, 0)}


def key_items(text: str) -> list[dict]:
    """직접 넣은 정답 한 줄 → [{n, a}] (복수 정답은 첫 번째 대신 '2/4' 문자열로 둠)."""
    out = []
    for i, k in enumerate(parse_key(text)):
        if k:
            out.append({"n": i + 1, "a": int(k) if len(k) == 1 else k})
    return out


def task_leveltest(t) -> dict:
    return {"kind": "leveltest", "title": f"레벨테스트 · {t.title}", "test_id": t.id, "pass": 0}


def task_check(text: str) -> dict:
    t = re.sub(r"\s+", " ", text or "").strip()[:200]
    return {"kind": "check", "title": t or "할 일", "text": t}


# ---------------- 채점 ----------------

def _ok(a, given: str) -> bool:
    return given != "" and given in str(a).split("/")


def grade_ox(task: dict, form) -> tuple[int, list[dict]]:
    correct, wrong = 0, []
    for i, it in enumerate(task["items"]):
        g = str(form.get(f"a{i}", "")).strip().upper()
        if g == it["a"]:
            correct += 1
        else:
            wrong.append({"i": i + 1, "q": it["q"], "a": it["a"], "given": g, "why": it.get("why", "")})
    return correct, wrong


def grade_omr(task: dict, form) -> tuple[int, list[dict]]:
    correct, wrong = 0, []
    for it in task["items"]:
        g = str(form.get(f"a{it['n']}", "")).strip()
        if _ok(it["a"], g):
            correct += 1
        else:
            wrong.append({"n": it["n"], "a": str(it["a"]), "given": g})
    return correct, wrong


def answer_label(a) -> str:
    return "/".join(CIRCLED.get(x, x) for x in str(a).split("/"))


def status(task: dict, r) -> str:
    """화면 표시용: todo | retry | done"""
    if r is None or not r.attempts:
        return "todo"
    return "done" if r.done else "retry"


def summary_text(task: dict) -> str:
    k = task["kind"]
    if k == "vocab":
        return f"{QUIZ_MODES.get(task['mode'], '')} {task['count']}문제" + (f" · {task['pass']}점 이상 통과" if task["pass"] else "")
    if k == "ox":
        return f"{len(task['items'])}문항 ({OX_LEVELS.get(task.get('level'), '')})" + (f" · {task['pass']}점 이상 통과" if task["pass"] else "")
    if k == "omr":
        nums = [it["n"] for it in task["items"]]
        rng = f"{nums[0]}~{nums[-1]}번 중 " if nums else ""
        return f"{rng}{len(nums)}문항 답 입력" + (f" · {task['pass']}점 이상 통과" if task["pass"] else "")
    if k == "leveltest":
        return "레벨테스트 풀고 제출하기"
    return "했으면 체크"


def pass_done(task: dict, correct: int, total: int) -> tuple[int, bool]:
    pct = round(100 * correct / total) if total else 0
    return pct, pct >= (task.get("pass") or 0)


def remind_text(academy_name: str, hw, student_name: str, link: str, todo_titles: list[str]) -> str:
    due = f" (마감 {hw.due[5:].replace('-', '/')})" if hw.due else ""
    lines = [f"[{academy_name}] {student_name} 학생 숙제 안내", f"■ {hw.title}{due}"]
    lines += [f" - {t}" for t in todo_titles]
    lines.append(f"여기서 바로 풀 수 있어요: {link}")
    return "\n".join(lines)

