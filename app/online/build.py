"""온라인 시험 문항 만들기: 여러 자료를 같은 모양의 '화면용 문제'로 바꿉니다.

문항 하나 = 학생·반 리포트 시험의 item + content
  {kind: mc|sa, answer, points, type, source,
   content: {label, stem, passage, box, choices[], conditions}}
  - passage·box 안의 [u]…[/u] 는 밑줄, ①~⑤ 같은 기호는 글자 그대로
  - 정답(answer)은 content 에 넣지 않음 → 학생 화면에는 content 만 보냄
"""
import re

from markupsafe import Markup, escape

from ..grammar import spec as gspec
from ..reports.spec import CIRCLED
from ..variant import spec as vspec

C = "①②③④⑤"


def _points(n_mc: int, n_sa: int, sa_pts: float = 0) -> tuple[float, float]:
    """100점을 고르게: 서술형이 있으면 서술형에 sa_pts(기본 문항당 5점)를 먼저 주고 남은 점수를 객관식에."""
    sa = (sa_pts or 5.0) if n_sa else 0.0
    left = 100 - sa * n_sa
    mc = round(left / n_mc, 2) if n_mc else 0.0
    return mc, sa


def _content(label, stem, passage="", box="", choices=None, conditions=""):
    return {"label": str(label), "stem": stem or "", "passage": passage or "", "box": box or "",
            "choices": list(choices or []), "conditions": conditions or ""}


# ---------------- 기출 분석 (학교 시험지에서 뽑은 문항) ----------------

def from_paper(qs: list[dict]) -> list[dict]:
    """지문을 같이 쓰는 문항(shared_with)은 그 지문을 복사해 넣어 문항마다 혼자 읽을 수 있게."""
    by_label = {q["label"]: q for q in qs}
    out = []
    for q in qs:
        passage = q.get("passage") or ""
        if not passage and q.get("shared_with") and q["shared_with"] in by_label:
            passage = by_label[q["shared_with"]].get("passage") or ""
        out.append({"kind": q["kind"], "answer": "", "points": q.get("points") or 0, "type": q.get("type", ""),
                    "source": q["label"] if not str(q["label"]).isdigit() else "",
                    "content": _content(q["label"], q.get("stem"), passage, q.get("box"), q.get("choices") if q["kind"] == "mc" else [],
                                        q.get("conditions"))})
    return out


# ---------------- 변형문제 ----------------

def from_variant(vs) -> list[dict]:
    qs = [q for q in (vs.questions or []) if not vspec.validate(q)]
    mc_pts, _ = _points(len(qs), 0)
    out = []
    for n, q in enumerate(qs, 1):
        t = q["type"]
        text = vs.passages[q.get("p", 0)]["text"] if vs.passages else ""
        stem = vspec.STEMS["match_" + q.get("ask", "wrong")] if t == "match" else vspec.STEMS[t]
        passage, box, choices = text, "", list(q.get("choices") or [])
        if t == "blank":
            passage = f"{q.get('before', '')} ________ {q.get('after', '')}".strip()
        elif t == "order":
            box = q.get("intro", "")
            passage = "\n".join(f"({k}) {q['parts'][k]}" for k in "ABC")
            choices = list(vspec.ORDER_CHOICES)
        elif t == "insert":
            box = q.get("given", "")
            parts = []
            for i, s in enumerate(q.get("sentences") or []):
                parts.append(s)
                if i in (q.get("gaps") or []):
                    parts.append(f"( {C[q['gaps'].index(i)]} )")
            passage = " ".join(parts)
            choices = list(C)
        elif t == "irrelevant":
            passage = " ".join([q.get("lead", "")] + [f"{C[i]} {s}" for i, s in enumerate(q.get("numbered") or [])] + [q.get("tail", "")]).strip()
            choices = list(C)
        elif t == "summary":
            box = q.get("summary", "")
            choices = [f"{a} …… {b}" for a, b in (q.get("choices") or [])]
        elif t == "vocab":
            pieces = gspec.place_marks(q.get("passage", ""), q.get("marks") or []) or []
            passage = "".join(pc["text"] if pc["mark"] is None else f"{C[pc['mark']]}[u]{pc['text']}[/u]" for pc in pieces)
            choices = list(C)
        ans = vspec.answer_text(q)
        out.append({"kind": "mc", "answer": str(C.index(ans) + 1) if ans in C else "", "points": mc_pts, "type": vspec.TYPES.get(t, t),
                    "source": (vs.passages[q.get("p", 0)].get("title") or f"지문 {q.get('p', 0) + 1}") if vs.passages else "",
                    "content": {**_content(n, stem, passage, box, choices), "box_first": t in ("order", "insert")}})
    return out


# ---------------- 어법 문제 ----------------

def from_grammar(gs) -> list[dict]:
    qs = [q for q in (gs.questions or []) if not gspec.validate(q)]
    n_sa = sum(1 for q in qs if q["type"] in ("arrange", "transform"))
    mc_pts, sa_pts = _points(len(qs) - n_sa, n_sa)
    out = []
    for n, q in enumerate(qs, 1):
        t = q["type"]
        stems = gspec.STEMS
        item = {"kind": "mc", "points": mc_pts, "type": "어법", "source": gs.target or ""}
        if t == "paren":
            a, b = q["options"]
            item.update(answer=str(q["options"].index(q["answer"]) + 1) if q["answer"] in q["options"] else "",
                        content=_content(n, stems["paren"], f"{q['before']}( {a} / {b} ){q['after']}", "", [a, b]))
        elif t == "blank":
            item.update(answer=str(q["answer"]), content=_content(n, stems["blank"], q["sentence"], "", q["choices"]))
        elif t == "pick":
            item.update(answer=str(q["answer"]), content=_content(n, stems["pick_right" if q.get("ask") == "right" else "pick_wrong"], "", "", q["choices"]))
        elif t in ("under5", "count"):
            pieces = gspec.place_marks(q["passage"], q["marks"]) or []
            sym = C if t == "under5" else gspec.LETTERS
            passage = "".join(pc["text"] if pc["mark"] is None else f"{sym[pc['mark']]}[u]{pc['text']}[/u]" for pc in pieces)
            wrong = [k for k, m in enumerate(q["marks"]) if m.get("wrong")]
            ans = (wrong[0] + 1) if t == "under5" and wrong else len(wrong)
            item.update(answer=str(ans) if ans else "", content=_content(n, stems[t], passage, "", list(C) if t == "under5" else [f"{k}개" for k in range(1, 6)]))
        elif t == "arrange":
            item.update(kind="sa", points=sa_pts, answer=q["answer"], type="서술형(영작)",
                        content=_content(n, stems["arrange"], "", f"{q['ko']}\n( {' / '.join(q['given'])} )", [], q.get("condition", "")))
        elif t == "transform":
            item.update(kind="sa", points=sa_pts, answer=q["answer"], type="서술형(어법)",
                        content=_content(n, stems["transform"], q["original"], f"→ {q['instruction']}", []))
        else:
            continue
        out.append(item)
    return out


# ---------------- 학생 화면에 보낼 모양 ----------------

def to_html(text: str) -> Markup:
    """글자는 모두 이스케이프한 뒤 [u]…[/u] 만 밑줄로, 줄바꿈은 <br>."""
    s = str(escape(text or ""))
    s = re.sub(r"\[u\](.+?)\[/u\]", r"<u>\1</u>", s, flags=re.S).replace("[u]", "").replace("[/u]", "")
    return Markup(s.replace("\n", "<br>"))


def is_online(exam) -> bool:
    return bool((exam.options or {}).get("online")) and has_content(exam.items)


def has_content(items) -> bool:
    return any(isinstance(it.get("content"), dict) for it in (items or []))


def public_items(items: list[dict]) -> list[dict]:
    """정답 없이 화면용으로. 바로 앞 문항과 같은 지문이면 지문을 접어 둠(same)."""
    out, prev = [], None
    for it in items:
        c = it.get("content") or {}
        passage = c.get("passage", "")
        same = bool(passage) and prev is not None and passage == prev["passage_raw"]
        row = {"n": it["n"], "kind": it["kind"], "points": it["points"], "label": str(it["n"]) if str(c.get("label") or "").isdigit() or not c.get("label") else c["label"],
               "stem": c.get("stem", ""), "passage": to_html(passage), "passage_raw": passage, "same": prev["label"] if same else "",
               "box": to_html(c.get("box", "")), "box_first": bool(c.get("box_first")), "conditions": to_html(c.get("conditions", "")),
               "choices": [to_html(x) for x in (c.get("choices") or [])][:5]}
        if it["kind"] == "mc" and not row["choices"]:
            row["choices"] = list(C)  # 보기 글이 없으면 번호만
        out.append(row)
        prev = row
    for r in out:
        r.pop("passage_raw")
    return out


__all__ = ["from_paper", "from_variant", "from_grammar", "public_items", "is_online", "has_content", "to_html", "CIRCLED"]
