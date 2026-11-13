"""문법뱅크 분류 재배치 작업."""
from ..bank import service
from ..db import session_scope
from ..jobs import JobContext, job_handler


@job_handler("bank_remap")
def remap(ctx: JobContext) -> dict:
    mapping = ctx.payload.get("mapping") or []
    step = lambda k, n: ctx.step(f"문항 옮기는 중 ({min(k, n):,}/{n:,})", 10 + int(85 * min(k, n) / max(1, n)))  # noqa: E731
    with session_scope() as db:
        if ctx.payload.get("rules"):
            ctx.step("교재 학년 단계와 기존 분류로 새 자리 찾는 중", 5)
            r = service.remap_rules(db, step=step)
        else:
            ctx.step(f"{len(mapping):,}개 묶음으로 문항 분류 바꾸는 중", 5)
            r = service.remap(db, mapping, step=step)
    return {"reply": f"{r['mapped']:,}문항을 새 분류로 옮겼어요." + (f" 매핑에 없는 {r['unmapped']:,}문항은 '미분류'로 남겼어요." if r["unmapped"] else ""), "model": "분류 재배치"}


CONCEPT_SYSTEM = """당신은 한국 중고등학생용 영어 문법 교재 저자입니다. 문법 개념 설명 카드를 JSON 하나로만 답합니다.
- 쉬운 우리말, 짧은 개조식. 예문은 자연스럽고 짧은 영어 문장(학생 수준)과 우리말 해석.
- 틀린 문법 설명을 쓰지 않습니다. 확실하지 않은 예외는 넣지 않습니다."""


def concept_prompt(s: dict) -> str:
    return "\n".join([
        f"학년 단계: {s['tier_label']}", f"단원: {s['cat']}", f"문법: {s['label']}", "",
        "이 문법의 개념 설명 카드를 만들어 주세요. 출력 형식:",
        '{"title": "카드 제목 (문법 이름, 짧게)",',
        ' "points": ["핵심 규칙 3~5개. 형태·쓰임·주의점. 한 줄씩, 40자 안팎"],',
        ' "forms": "형태 공식 한 줄 (예: have/has + p.p.) — 없으면 빈 문자열",',
        ' "examples": [{"en": "예문", "ko": "해석", "hl": "예문 속 핵심 부분(그대로 일치)"}, ... 3개],',
        ' "tip": "시험에 자주 나오는 함정 한 줄"}',
    ])


def clean_concept(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    s = lambda v, n: " ".join(str(v or "").split())[:n]  # noqa: E731
    pts = [s(x, 120) for x in raw.get("points") or [] if s(x, 120)][:5]
    exs = []
    for e in raw.get("examples") or []:
        if isinstance(e, dict) and s(e.get("en"), 200):
            en, hl = s(e.get("en"), 200), s(e.get("hl"), 80)
            exs.append({"en": en, "ko": s(e.get("ko"), 200), "hl": hl if hl and hl in en else ""})
    if len(pts) < 2 or not exs:
        return None
    return {"title": s(raw.get("title"), 60), "points": pts, "forms": s(raw.get("forms"), 120), "examples": exs[:3], "tip": s(raw.get("tip"), 160)}


@job_handler("bank_concepts")
def concepts(ctx: JobContext) -> dict:
    from .. import llm
    from ..bank import taxonomy
    from ..models import BankConcept

    subs = taxonomy.load()["subs"]
    todo = [x for x in ctx.payload.get("subs") or [] if x in subs]
    key, model = ctx.gemini()
    made, failed = 0, []
    for n, sid in enumerate(todo, 1):
        s = subs[sid]
        ctx.step(f"'{s['label']}' 개념 설명 만드는 중 ({n}/{len(todo)})", int(5 + 90 * (n - 1) / max(1, len(todo))))
        body = None
        for _ in range(2):
            try:
                body = clean_concept(llm.generate(key, concept_prompt(s), system=CONCEPT_SYSTEM, model=model, json_mode=True, temperature=0.4, max_tokens=1500))
            except llm.LLMError as e:
                if "API 키" in str(e):
                    raise
            if body:
                break
        if not body:
            failed.append(s["label"])
            continue
        if not body["title"]:
            body["title"] = s["label"]
        with session_scope() as db:
            row = db.get(BankConcept, sid) or BankConcept(sub_id=sid)
            row.body = body
            db.add(row)
        made += 1
    return {"reply": f"개념 설명 {made}개를 만들었어요." + (f" 못 만든 것: {', '.join(failed[:5])}" if failed else "") + " 시험지 PDF를 받으면 맨 앞에 들어가요.",
            "model": "개념 설명"}


# ---------------------------------------------------------------- 빈 중분류 채우기 (AI가 문항을 새로 만듦)

GEN_SYSTEM = """당신은 한국 중고등학교 영어 내신 문법 문제 출제 전문가입니다. 요청한 문법 하나만 묻는 문제를 JSON 하나로만 답합니다.
- 문제마다 정답이 딱 하나여야 합니다. 애매한 보기, 두 개가 맞는 보기, 원어민이 어색하다고 느낄 문장은 쓰지 않습니다.
- 영어 문장은 짧고 자연스럽게, 학년 단계에 맞는 쉬운 어휘로. 교과서·문제집 문장을 그대로 베끼지 말고 새로 만듭니다.
- 발문은 우리말 한 문장(예: "다음 빈칸에 들어갈 말로 알맞은 것은?"). 해설은 우리말 1~2문장."""

CHECK_SYSTEM = """당신은 영어 문법 문제 검토자입니다. 각 문제를 먼저 스스로 풀고, 적힌 정답과 비교해 JSON 하나로만 답합니다.
정답이 하나로 정해지지 않거나, 문법적으로 틀린 문장이 정답이거나, 오답 보기 중에도 맞는 것이 있으면 agree 를 false 로 합니다."""

GEN_MIX = [("mc", "객관식", 5), ("short", "주관식", 3), ("essay", "서술형", 2)]
GEN_QTYPES = {"mc": ["객관식"], "short": ["빈칸 쓰기", "오류 수정", "단답"], "essay": ["영작", "배열", "문장 전환"]}
GEN_BOOK = "AI 생성 문항"


def gen_prompt(s: dict, siblings: list[str], n: int, seen: list[str]) -> str:
    young = s["tier"] in ("g1", "g2", "g3")
    mix = ", ".join(f"{lab} {k}개" for _, lab, k in GEN_MIX) if n == 10 else f"객관식 위주로 {n}개"
    return "\n".join([
        f"학년 단계: {s['tier_label']}" + (" (초등학생 — 아주 쉬운 단어, 4~8단어 문장)" if young else ""),
        f"단원(중분류): {s['cat']}", f"이번 문법(소분류): {s['label']}",
        f"같은 단원의 다른 소분류(이번엔 묻지 않음): {', '.join(x for x in siblings if x != s['label'])[:400]}", "",
        f"이 소분류만 묻는 문제 {n}개를 만들어 주세요. 구성: {mix}. 난이도는 하·중·상을 고르게.",
        "- 객관식: 보기 5개 '① … ② … ③ … ④ … ⑤ …' 한 줄, 정답은 '③'처럼 번호만. 문제 문장의 빈칸은 ____ 로.",
        "- 주관식: 빈칸 쓰기 / 오류 수정(틀린 부분 → 고친 말) / 단답. choices 는 빈 문자열. 정답은 짧게.",
        "- 서술형: 영작(우리말 주고 영어로, 조건 괄호로 제시) / 배열(단어 묶음을 / 로 나눠 제시) / 문장 전환. 정답은 완성 문장.",
        "- 이미 만든 문제와 겹치지 않게: " + (" | ".join(seen[-25:]) if seen else "(없음)"), "",
        '출력: {"questions": [{"kind": "mc|short|essay", "qtype": "객관식|빈칸 쓰기|오류 수정|단답|영작|배열|문장 전환",',
        ' "level": "하|중|상", "instr": "우리말 발문", "stem": "영어 문제 문장(지문)", "choices": "① a ② b ③ c ④ d ⑤ e 또는 빈 문자열",',
        ' "answer": "정답", "expl": "우리말 해설"}, ...]}',
    ])


def check_prompt(s: dict, qs: list[dict]) -> str:
    body = "\n\n".join(f"[{i}] {q['instr']}\n{q['stem']}\n{q['choices']}\n적힌 정답: {q['answer']}" for i, q in enumerate(qs))
    return (f"문법: {s['tier_label']} · {s['cat']} · {s['label']}\n\n{body}\n\n"
            '출력: {"results": [{"i": 0, "mine": "내가 푼 답", "agree": true}, ...]} — 모든 문제에 대해')


def clean_gen(raw) -> list[dict]:
    import re as _re
    out = []
    for q in (raw or {}).get("questions") or [] if isinstance(raw, dict) else []:
        if not isinstance(q, dict):
            continue
        g = lambda k, n=2000: str(q.get(k) or "").strip()[:n]  # noqa: E731
        kind = g("kind") if g("kind") in GEN_QTYPES else "short"
        d = {"kind": kind, "qtype": g("qtype", 30) if g("qtype") in GEN_QTYPES[kind] else GEN_QTYPES[kind][0],
             "level": g("level") if g("level") in ("하", "중", "상") else "중", "instr": g("instr", 300), "stem": g("stem"),
             "choices": " ".join(g("choices").split()), "answer": g("answer", 400), "expl": g("expl", 600)}
        if not d["stem"] or not d["answer"] or not d["instr"]:
            continue
        if kind == "mc":
            if not all(c in d["choices"] for c in "①②③④⑤") or not _re.fullmatch(r"[①②③④⑤]", d["answer"]):
                continue
        else:
            d["choices"] = ""
        out.append(d)
    return out


@job_handler("bank_fill")
def fill(ctx: JobContext) -> dict:
    """문항이 하나도 없는 중분류마다 per_cat 문항을 AI로 만들어 넣음 (소분류에 고르게 나눔).
    서버가 다시 시작돼도 이미 넣은 문항은 그대로 두고 모자란 만큼만 이어서 만듦."""
    import random
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import func, select

    from .. import llm
    from ..bank import taxonomy
    from ..models import BankQuestion

    per_cat = int(ctx.payload.get("per_cat") or 200)
    targets: dict[str, int] = ctx.payload.get("targets") or {}
    tx = taxonomy.load()
    subs = tx["subs"]
    if not targets:  # 처음 실행: 지금 비어 있는 중분류를 찾아 목표를 정해 두고 저장 (다시 시작해도 같은 목표)
        with session_scope() as db:
            counts = dict(db.execute(select(BankQuestion.sub_id, func.count()).where(BankQuestion.review != "del", BankQuestion.sub_id != "")
                                     .group_by(BankQuestion.sub_id)).all())
        cats: dict[tuple, list[str]] = {}
        for sid, s in subs.items():
            cats.setdefault((s["tier"], s["cat"]), []).append(sid)
        for key, sids in cats.items():
            if sum(counts.get(x, 0) for x in sids) == 0:
                base, extra = divmod(per_cat, len(sids))
                for i, sid in enumerate(sids):
                    targets[sid] = base + (1 if i < extra else 0)
        from ..models import Job
        with session_scope() as db:
            job = db.get(Job, ctx.job_id)
            job.payload = {**(job.payload or {}), "targets": targets}
    if not targets:
        return {"reply": "비어 있는 중분류가 없어요.", "model": "문항 채우기"}

    with session_scope() as db:  # 새 문항 정렬 자리: 앞 소분류의 마지막 문항 바로 뒤
        rows = db.execute(select(BankQuestion.sub_id, func.max(BankQuestion.ord)).where(BankQuestion.sub_id != "").group_by(BankQuestion.sub_id)).all()
        have = dict(db.execute(select(BankQuestion.sub_id, func.count()).where(BankQuestion.book == GEN_BOOK).group_by(BankQuestion.sub_id)).all())
        seen0 = {}
        for sid, stem in db.execute(select(BankQuestion.sub_id, BankQuestion.stem).where(BankQuestion.book == GEN_BOOK)).all():
            seen0.setdefault(sid, []).append(stem[:60])
    by_idx = sorted((subs[sid]["idx"], o or 0) for sid, o in rows if sid in subs)

    def ord_for(sid):
        i, best = subs[sid]["idx"], 0
        for j, o in by_idx:
            if j > i:
                break
            best = o
        return best

    siblings = {}
    for sid, s in subs.items():
        siblings.setdefault((s["tier"], s["cat"]), []).append(s["label"])
    key, model = ctx.gemini()
    total = sum(targets.values())
    lock = threading.Lock()
    state = {"done": sum(min(have.get(s, 0), n) for s, n in targets.items()), "dropped": 0, "failed": []}

    def work(sid: str):
        s, need = subs[sid], targets[sid]
        n_have, seen, tries = have.get(sid, 0), list(seen0.get(sid, [])), 0
        while n_have < need and tries < max(6, need // 4):
            tries += 1
            n = min(10, need - n_have)
            try:
                qs = clean_gen(llm.generate(key, gen_prompt(s, siblings[(s["tier"], s["cat"])], 10, seen), system=GEN_SYSTEM, model=model,
                                            json_mode=True, temperature=0.8, max_tokens=8000))
                if not qs:
                    continue
                res = llm.generate(key, check_prompt(s, qs), system=CHECK_SYSTEM, model=model, json_mode=True, temperature=0.1, max_tokens=4000)
                ok = {int(r.get("i", -1)) for r in (res or {}).get("results") or [] if isinstance(r, dict) and r.get("agree") is True}
            except llm.LLMError as e:
                if "API 키" in str(e):
                    raise
                continue
            good = [q for i, q in enumerate(qs) if i in ok and q["stem"][:60] not in seen]
            random.shuffle(good)
            good = good[:n]
            if not good:
                continue
            batch = []
            for q in good:
                n_have += 1
                seen.append(q["stem"][:60])
                batch.append({"id": f"ai-{sid}-{n_have:03d}", "ord": ord_for(sid), "cat": taxonomy.cat_name(sid)[:60], "subcat": s["label"][:120],
                              "level": q["level"], "book": GEN_BOOK, "chapter": s["cat"][:120], "unit": s["label"][:160], "no": str(n_have),
                              "page": None, "qtype": q["qtype"], "instr": q["instr"], "passage": "", "stem": q["stem"], "choices": q["choices"],
                              "answer": q["answer"], "expl": q["expl"], "src": "AI 생성", "review": "", "tier": s["tier"], "sub_id": sid,
                              "old_cat": "", "old_sub": ""})
            with session_scope() as db:
                db.execute(BankQuestion.__table__.insert(), batch)
            with lock:
                state["done"] += len(batch)
                state["dropped"] += len(qs) - len(good)
                ctx.step(f"'{s['label']}' 문항 만드는 중 — 전체 {state['done']:,}/{total:,}", 2 + int(96 * state["done"] / max(1, total)))
        if n_have < need:
            with lock:
                state["failed"].append(f"{s['label']}({n_have}/{need})")

    ctx.step(f"빈 중분류 {len({(subs[s]['tier'], subs[s]['cat']) for s in targets})}개, 소분류 {len(targets)}개에 {total:,}문항 만드는 중", 2)
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(work, [s for s in targets if have.get(s, 0) < targets[s]]))
    return {"reply": f"AI로 {state['done']:,}문항을 만들어 넣었어요 (검토에서 정답이 애매한 {state['dropped']:,}문항은 버림). 정답 출처는 'AI 생성'으로 표시돼요."
            + (f" 덜 채운 소분류: {', '.join(state['failed'][:8])}" if state["failed"] else ""), "model": "문항 채우기"}
