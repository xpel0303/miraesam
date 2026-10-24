"""시험 채점·통계 규칙 (AI 없음, 전부 코드 계산).

문항 items: [{n, kind: "mc"|"sa", answer, points, type, source}]
  - mc(객관식) answer: "3" / 복수 정답은 "2/4" (둘 중 하나면 정답)
  - sa(서술형) answer: 모범 답안 메모 (채점은 선생님이 점수를 넣음)
학생 답 ExamResponse.answers: {"1": "3", ...} (객관식, 빈칸 = 안 씀)
             sa_scores: {"21": 4.5, ...}  sa_text: {"21": "학생이 쓴 답"}

정답·배점을 나중에 고쳐도(정답 정정) 저장된 학생 답으로 언제든 다시 채점됩니다.
"""
import math
import re
import statistics

TYPES = ["주제", "제목", "요지", "주장", "목적", "심경", "내용일치", "지칭", "어법", "어휘", "빈칸", "순서", "삽입",
         "무관한 문장", "요약", "연결어", "장문", "영영풀이", "서술형(영작)", "서술형(어법)", "서술형(요약)", "서술형(해석)", "기타"]
CHOICES = ["1", "2", "3", "4", "5"]
CIRCLED = {"1": "①", "2": "②", "3": "③", "4": "④", "5": "⑤"}
MAX_ITEMS = 60
EASY_MISS = 70  # 반 정답률이 이 % 이상인데 틀리면 '아까운 문항'


def num(v, default=0.0) -> float:
    try:
        x = float(str(v).strip().replace(",", "."))
        return x if math.isfinite(x) and x >= 0 else default
    except (TypeError, ValueError):
        return default


def fmt(x: float) -> str:
    """3.0 → '3', 2.75 → '2.75', 2.8 → '2.8'."""
    if x is None:
        return ""
    s = f"{round(x, 2):.2f}".rstrip("0").rstrip(".")
    return s or "0"


def norm_mc(v) -> str:
    """'③', '3번', ' 3 ' → '3'. 복수 표기 '2,4' '2/4' '24' → '2/4'."""
    s = str(v or "").strip()
    for k, c in CIRCLED.items():
        s = s.replace(c, k)
    digits = sorted(set(re.findall(r"[1-5]", s)))
    return "/".join(digits)


_TOK = re.compile(r"[1-5](?:\s*/\s*[1-5])*|[0.\-xX?]")


def _tokens(text: str) -> list[str]:
    s = str(text or "")
    for k, c in CIRCLED.items():
        s = s.replace(c, k)
    return ["" if t in "0.-xX?" else norm_mc(t) for t in _TOK.findall(s)]


def parse_key(text: str) -> list[str]:
    """정답 붙여 넣기 → 문항별 정답 목록.
    '35214' / '3 5 2 1 4' / '③⑤②①④' 모두 됨. 복수 정답은 '2/4' ('3524/1' → 3, 5, 2, 4/1)."""
    return _tokens(text)


def parse_row(text: str, n: int) -> list[str]:
    """학생 한 명의 객관식 답 한 줄: '35214' 또는 '3 5 2 1 4'. 0 · - · x 는 안 쓴 문항."""
    out = _tokens(text)
    return (out + [""] * n)[:n]


def row_text(answers: dict, mc_items: list[dict]) -> str:
    """저장된 답 → 입력칸에 보여줄 한 줄 ('3521-4')."""
    vals = [answers.get(str(it["n"]), "") for it in mc_items]
    if all(len(v) <= 1 for v in vals):
        return "".join(v or "-" for v in vals).rstrip("-")
    return " ".join(v or "-" for v in vals)


def clean_items(items: list[dict]) -> list[dict]:
    out = []
    for i, it in enumerate(items[:MAX_ITEMS]):
        kind = "sa" if it.get("kind") == "sa" else "mc"
        ans = norm_mc(it.get("answer")) if kind == "mc" else str(it.get("answer") or "").strip()[:300]
        row = {"n": i + 1, "kind": kind, "answer": ans, "points": num(it.get("points"), 0.0),
               "type": str(it.get("type") or "").strip()[:20], "source": str(it.get("source") or "").strip()[:60]}
        if isinstance(it.get("content"), dict):  # 온라인 시험: 화면에 띄울 문제 (발문·지문·보기·선지)
            row["content"] = it["content"]
        out.append(row)
    return out


def make_items(mc: int, sa: int, key: list[str], mc_points: float | None, sa_points: float | None) -> list[dict]:
    mc, sa = max(0, min(MAX_ITEMS, mc)), max(0, min(MAX_ITEMS - mc, sa))
    if mc_points is None:  # 배점을 안 넣으면 100점을 객관식에 고르게
        mc_points = round(100 / mc, 2) if mc and not sa else (round(70 / mc, 2) if mc else 0)
    if sa_points is None:
        sa_points = round((100 - mc_points * mc) / sa, 2) if sa else 0
    items = [{"kind": "mc", "answer": key[i] if i < len(key) else "", "points": mc_points} for i in range(mc)]
    items += [{"kind": "sa", "answer": "", "points": sa_points} for _ in range(sa)]
    return clean_items(items)


def check_items(items: list[dict]) -> list[str]:
    warns = []
    total = sum(it["points"] for it in items)
    if items and abs(total - 100) > 0.05:
        warns.append(f"배점 합계가 {fmt(total)}점이에요. (100점이 아니어도 괜찮지만 확인해 주세요)")
    no_key = [str(it["n"]) for it in items if it["kind"] == "mc" and not it["answer"]]
    if no_key:
        warns.append(f"정답이 없는 객관식: {', '.join(no_key[:12])}번")
    no_type = [it for it in items if not it["type"]]
    if items and len(no_type) == len(items):
        warns.append("유형을 넣으면 학생 리포트에 유형별 정답률이 나와요.")
    return warns


# ---------------- 채점 ----------------

def grade(items: list[dict], answers: dict, sa_scores: dict) -> dict:
    """학생 한 명 채점 → {score, max, mc_correct, mc_total, sa_score, sa_max, pending(서술형 미채점 수), rows[]}"""
    rows, score, mx, mc_ok, mc_n, sa_s, sa_m, pending = [], 0.0, 0.0, 0, 0, 0.0, 0.0, 0
    for it in items:
        k = str(it["n"])
        mx += it["points"]
        if it["kind"] == "mc":
            mc_n += 1
            got = norm_mc(answers.get(k, ""))
            ok = bool(got) and bool(it["answer"]) and len(got) == 1 and got in it["answer"].split("/")
            earned = it["points"] if ok else 0.0
            mc_ok += ok
            rows.append({**it, "got": got, "ok": ok, "earned": earned, "graded": True})
        else:
            sa_m += it["points"]
            raw = sa_scores.get(k)
            graded = raw not in (None, "")
            earned = min(it["points"], num(raw)) if graded else 0.0
            pending += not graded
            sa_s += earned
            ok = graded and earned >= it["points"] - 1e-9
            rows.append({**it, "got": "", "ok": ok, "partial": graded and 0 < earned < it["points"], "earned": earned, "graded": graded})
        score += rows[-1]["earned"]
    return {"score": round(score, 2), "max": round(mx, 2), "mc_correct": mc_ok, "mc_total": mc_n,
            "sa_score": round(sa_s, 2), "sa_max": round(sa_m, 2), "pending": pending, "rows": rows}


def _rank(scores: list[float]) -> list[int]:
    """동점 같은 등수 (1, 2, 2, 4)."""
    order = sorted(scores, reverse=True)
    return [order.index(s) + 1 for s in scores]


def summarize(items: list[dict], people: list[dict]) -> dict:
    """people: [{id, name, class_name, answers, sa_scores}] → 반 전체 통계.

    반환: {n, avg, median, max, min, sd, full, people[{...grade, rank, pct}], items[{...rate, dist, disc}],
           types[{type, rate, n_items}], bins[{label, count}]}
    """
    graded = []
    for p in people:
        g = grade(items, p.get("answers") or {}, p.get("sa_scores") or {})
        graded.append({**p, **g, "rate": (100 * g["score"] / g["max"]) if g["max"] else 0})
    n = len(graded)
    scores = [g["score"] for g in graded]
    ranks = _rank(scores) if n else []
    for g, r in zip(graded, ranks):
        g["rank"] = r
        g["top_pct"] = round(100 * r / n) if n else 0  # 상위 %
    full = round(sum(it["points"] for it in items), 2)

    # 문항 분석: 정답률, 선지 분포, 변별도(상위 27% 정답률 - 하위 27% 정답률)
    k = max(1, round(n * 0.27)) if n >= 4 else 0
    by_score = sorted(range(n), key=lambda i: -scores[i])
    top, low = set(by_score[:k]), set(by_score[-k:]) if k else set()
    item_stats = []
    for idx, it in enumerate(items):
        oks = [g["rows"][idx]["ok"] for g in graded if g["rows"][idx]["graded"]]
        earned = [g["rows"][idx]["earned"] for g in graded if g["rows"][idx]["graded"]]
        rate = round(100 * sum(oks) / len(oks)) if oks else None
        st = {**it, "rate": rate, "graded_n": len(oks), "avg": round(sum(earned) / len(earned), 2) if earned else None}
        if it["kind"] == "mc":
            dist = {c: 0 for c in CHOICES}
            dist[""] = 0
            for g in graded:
                got = g["rows"][idx]["got"]
                dist[got if got in dist else ""] += 1
            st["dist"] = dist
            wrong_pick = max(((c, v) for c, v in dist.items() if c and c not in it["answer"].split("/")), key=lambda cv: cv[1], default=None)
            st["lure"] = wrong_pick[0] if wrong_pick and wrong_pick[1] > 0 and n and wrong_pick[1] / n >= 0.2 else None
        if k:
            t = [graded[i]["rows"][idx]["ok"] for i in top if graded[i]["rows"][idx]["graded"]]
            l_ = [graded[i]["rows"][idx]["ok"] for i in low if graded[i]["rows"][idx]["graded"]]
            st["disc"] = round((sum(t) / len(t) - sum(l_) / len(l_)), 2) if t and l_ else None
        else:
            st["disc"] = None
        item_stats.append(st)

    types = type_rates(items, graded)
    bins = []
    if full:
        edges = list(range(0, 100, 10))
        counts = [0] * 10
        for g in graded:
            b = min(9, int(g["rate"] // 10))
            counts[b] += 1
        bins = [{"label": f"{e}~{e + 9}" if e < 90 else "90~100", "count": c} for e, c in zip(edges, counts)]
    return {
        "n": n, "full": full,
        "avg": round(statistics.mean(scores), 1) if n else None,
        "median": round(statistics.median(scores), 1) if n else None,
        "max": max(scores) if n else None, "min": min(scores) if n else None,
        "sd": round(statistics.pstdev(scores), 1) if n > 1 else None,
        "pending": sum(g["pending"] for g in graded),
        "people": sorted(graded, key=lambda g: (g["rank"], g["name"])),
        "items": item_stats, "types": types, "bins": bins,
    }


def type_rates(items: list[dict], graded: list[dict]) -> list[dict]:
    """유형별 득점률(%) — 배점 가중. graded: grade() 결과 목록."""
    out = {}
    for idx, it in enumerate(items):
        t = it["type"] or "유형 미정"
        o = out.setdefault(t, {"type": t, "earned": 0.0, "max": 0.0, "items": []})
        o["items"].append(it["n"])
        for g in graded:
            r = g["rows"][idx]
            if r["graded"]:
                o["earned"] += r["earned"]
                o["max"] += it["points"]
    res = []
    for o in out.values():
        res.append({"type": o["type"], "items": o["items"], "rate": round(100 * o["earned"] / o["max"]) if o["max"] else None})
    return res


def student_view(summary: dict, sid: int) -> dict | None:
    """학생 한 명 리포트에 필요한 것: 내 점수·등수, 유형별(나 vs 반), 틀린 문항, 자동 요약 문장."""
    me = next((p for p in summary["people"] if p["id"] == sid), None)
    if me is None:
        return None
    class_types = {t["type"]: t["rate"] for t in summary["types"]}
    mine = type_rates([{**r} for r in me["rows"]], [me])
    types = []
    for t in mine:
        types.append({**t, "class": class_types.get(t["type"]), "n_items": len(t["items"])})
    item_rate = {it["n"]: it["rate"] for it in summary["items"]}
    wrong = []
    for r in me["rows"]:
        if not r["graded"] or r["ok"]:
            continue
        wrong.append({**r, "class_rate": item_rate.get(r["n"]), "easy": (item_rate.get(r["n"]) or 0) >= EASY_MISS})
    lost = round(sum(r["points"] - r["earned"] for r in me["rows"] if r["graded"]), 2)
    easy_lost = round(sum(w["points"] - w["earned"] for w in wrong if w["easy"]), 2)
    return {"me": me, "types": types, "wrong": wrong, "lost": lost, "easy_lost": easy_lost, "auto": auto_comment(me, types, wrong, summary)}


def auto_comment(me: dict, types: list[dict], wrong: list[dict], summary: dict) -> list[str]:
    """숫자로만 쓰는 요약 문장 (선생님 코멘트와 별개)."""
    lines = []
    avg = summary["avg"]
    if avg is not None and summary["n"] > 1:
        diff = round(me["score"] - avg, 1)
        if diff > 0:
            lines.append(f"반 평균({fmt(avg)}점)보다 {fmt(diff)}점 높습니다.")
        elif diff < 0:
            lines.append(f"반 평균({fmt(avg)}점)보다 {fmt(-diff)}점 낮습니다.")
        else:
            lines.append(f"반 평균({fmt(avg)}점)과 같습니다.")
    scored = [t for t in types if t["rate"] is not None and t["class"] is not None and t["type"] != "유형 미정"]
    strong = [t["type"] for t in scored if t["rate"] == 100 and t["n_items"] >= 2]
    weak = sorted([t for t in scored if t["rate"] < t["class"] - 10], key=lambda t: t["rate"] - t["class"])
    if strong:
        lines.append(f"{', '.join(strong[:3])} 유형은 모두 맞혔습니다.")
    if weak:
        lines.append(f"{', '.join(t['type'] for t in weak[:3])} 유형이 반 평균보다 낮아 보충이 필요합니다.")
    easy = [w for w in wrong if w["easy"]]
    if easy:
        lines.append(f"반 학생 대부분이 맞힌 문항 {len(easy)}개({', '.join(str(w['n']) + '번' for w in easy[:5])})에서 {fmt(sum(w['points'] - w['earned'] for w in easy))}점을 잃었습니다. 실수를 줄이면 바로 올릴 수 있는 점수입니다.")
    if me["pending"]:
        lines.append(f"서술형 {me['pending']}문항은 아직 채점 전입니다.")
    if not wrong and not me["pending"]:
        lines.append("모든 문항을 맞혔습니다.")
    return lines


def mask_name(name: str) -> str:
    """게시용 이름 가리기: 김하늘 → 김*늘, 이솔 → 이*, 남궁민수 → 남**수."""
    name = name.strip()
    if len(name) <= 1:
        return name
    if len(name) == 2:
        return name[0] + "*"
    return name[0] + "*" * (len(name) - 2) + name[-1]
