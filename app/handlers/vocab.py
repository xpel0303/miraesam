"""단어장 작업.

vocab_build : 지문에서 단어 뽑기 / 붙여 넣은 목록의 빈칸 채우기 (25개씩 나눠 요청 → 답이 잘리지 않게)
vocab_pdf   : 단어장 PDF 또는 단어 시험지 PDF (답지는 맨 뒤 페이지)
"""
import random
from datetime import datetime
from zoneinfo import ZoneInfo

from .. import llm
from ..db import session_scope
from ..jobs import JobContext, job_handler
from ..models import Academy, WordList
from ..pdf import render_pdf
from ..vocab import spec


def _set(list_id: int, **fields):
    with session_scope() as db:
        wl = db.get(WordList, list_id)
        for k, v in fields.items():
            setattr(wl, k, v)


def _ask(key, model, prompt, n_words) -> list[dict]:
    raw = llm.generate(key, prompt, system=spec.SYSTEM_PROMPT, model=model, json_mode=True, temperature=0.3, max_tokens=2000 + 320 * n_words)
    items = raw.get("words") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise llm.LLMError("단어 목록을 JSON으로 받지 못했습니다.")
    return [spec.clean_entry(x) for x in items if isinstance(x, dict) and str(x.get("word", "")).strip()]


@job_handler("vocab_build")
def build(ctx: JobContext) -> dict:
    list_id = ctx.payload["list_id"]
    key, model = ctx.gemini()
    with session_scope() as db:
        wl = db.get(WordList, list_id)
        kind, text, level, words = wl.source_kind, wl.source_text, wl.level, [dict(w) for w in (wl.words or [])]
    _set(list_id, status="making", error=None)

    try:
        ps = spec.split_passages(text) if kind == "passage" else []
        names = spec.passage_names(ps)
        have = {w.get("src") or "" for w in words if w.get("word")}
        # 아직 단어가 없는 지문만 뽑기 — 중간에 오류·재시작으로 다시 돌 때도 앞 지문만 남고 끝나지 않게
        failed: list[str] = []
        todo_ps = [(n, p) for n, p in enumerate(ps) if names[n] not in have] if (not words or ctx.payload.get("count") or ctx.payload.get("missing")) else []
        if todo_ps:
            count = int(ctx.payload.get("count") or 0) or spec.per_passage_count(words) or 10
            seen = {w["word"].lower() for w in words if w.get("word")}
            for i, (n, p) in enumerate(todo_ps, 1):  # 지문마다 따로 뽑고, 앞 지문에 나온 단어는 빼기
                ctx.step(f"{names[n]}에서 단어 {count}개 고르는 중 ({i}/{len(todo_ps)})", int(10 + 80 * (i - 1) / max(1, len(todo_ps))))
                got, err = [], None
                for _try in range(3):  # 한 지문이 실패해도(빈 답·끊김) 다시 묻고, 그래도 안 되면 다음 지문으로
                    try:
                        got = _ask(key, model, spec.extract_prompt(p["text"], count + 3, level), count + 3)
                    except llm.LLMError as e:
                        err = e
                        if "API 키" in str(e) or "모델을 찾을 수" in str(e):
                            raise
                        continue
                    if [w for w in got if w["word"].lower() not in seen]:
                        break
                new = [{**w, "src": names[n]} for w in got if w["word"].lower() not in seen][:count]
                if not new:
                    failed.append(names[n])
                    if err:
                        ctx.step(f"{names[n]} 단어를 못 뽑아서 다음 지문으로 넘어가요 ({err})", int(10 + 80 * i / max(1, len(todo_ps))))
                    continue
                seen |= {w["word"].lower() for w in new}
                words = spec.order_by_passage(words + new, names)
                _set(list_id, words=[dict(w) for w in words])  # 지문마다 저장
            words = words[: spec.MAX_WORDS]
        todo = [i for i, w in enumerate(words) if spec.blank_needed(w)]
        if todo:
            chunks = [todo[i:i + spec.CHUNK] for i in range(0, len(todo), spec.CHUNK)]
            for n, chunk in enumerate(chunks, 1):
                ctx.step(f"빈칸 채우는 중 ({n}/{len(chunks)})", int(10 + 80 * (n - 1) / max(1, len(chunks))))
                filled = _ask(key, model, spec.fill_prompt([words[i] for i in chunk], " ".join(p["text"] for p in ps)[:12000] if kind == "passage" else "", level), len(chunk))
                by_word = {f["word"].lower(): f for f in filled}
                for pos, i in enumerate(chunk):
                    f = by_word.get(words[i]["word"].lower()) or (filled[pos] if pos < len(filled) else None)
                    if not f:
                        continue
                    teacher_meaning = words[i].get("meaning")
                    merged = {**spec.clean_entry(f), "word": words[i]["word"]}
                    if teacher_meaning:
                        merged["meaning"] = teacher_meaning  # 선생님이 적은 뜻은 그대로
                    words[i] = {**spec.clean_entry(words[i]), **{k: v for k, v in merged.items() if v}}
                _set(list_id, words=[dict(w) for w in words])  # 덩어리마다 저장
        if not words:
            raise llm.LLMError("단어를 하나도 받지 못했습니다.")
        missing = [w["word"] for w in words if not w.get("meaning")]
        msgs = []
        if failed:
            msgs.append(f"단어를 못 뽑은 지문: {', '.join(failed)} — '빠진 지문 단어 뽑기'를 눌러 주세요")
        if missing:
            msgs.append(f"뜻을 채우지 못한 단어: {', '.join(missing[:8])}")
        _set(list_id, words=words, status="done", error=(" / ".join(msgs) or None))
        return {"list_id": list_id, "count": len(words)}
    except llm.LLMError as e:
        _set(list_id, status="failed", error=str(e), words=words)
        raise


@job_handler("vocab_pdf")
def make_pdf(ctx: JobContext) -> dict:
    p = ctx.payload
    with session_scope() as db:
        wl = db.get(WordList, p["list_id"])
        academy = db.get(Academy, wl.academy_id)
        db.expunge(academy)
        title, words = wl.title, [w for w in wl.words if w.get("word")]
    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")

    if p.get("kind") == "test":
        mode = p.get("mode", "en_ko")
        key = p.get("key", "end")
        note = ""
        if mode == "exam":
            groups = spec.exam_items(words, int(p.get("mc", 10)), int(p.get("sa", 5)), p.get("shuffle", True), p.get("seed") or 1,
                                     p.get("by_src", True), p.get("srcs"), p.get("per_src", False))
            if not any(g["mc"] or g["sa"] for g in groups):
                raise llm.LLMError("낼 문제가 없어요. 객관식·주관식 문항 수를 확인해 주세요.")
            got_mc, got_sa = sum(len(g["mc"]) for g in groups), sum(len(g["sa"]) for g in groups)
            if not p.get("per_src") and (got_mc < int(p.get("mc", 10)) or got_sa < int(p.get("sa", 5))):
                note = (f"단어가 모자라 객관식 {got_mc} · 주관식 {got_sa}문제로 줄였어요 "
                        f"(정한 수: 객관식 {p.get('mc', 10)} · 주관식 {p.get('sa', 5)}, 주관식은 예문이 있는 단어만).")
            tpl = "pdf/vocab_exam.html"
            ctxv = {"academy": academy, "title": title, "groups": groups, "doc_title": f"{title} · 단어 시험지",
                    "date": datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y. %m. %d."), "circled": spec.CIRCLED, "pages": p.get("layout") == "pages"}
        else:
            groups = spec.test_items(words, mode, int(p.get("count") or len(words)), p.get("shuffle", True), p.get("seed") or 1, p.get("by_src", True),
                                     p.get("srcs"), p.get("per_src", False))
            tpl = "pdf/vocab_test.html"
            ctxv = {"academy": academy, "title": title, "groups": groups, "n": sum(len(g["qs"]) for g in groups),
                    "doc_title": f"{title} · 단어 시험", "today": today, "mode": mode, "dirs": spec.TEST_DIRS,
                    "circled": spec.CIRCLED, "pages": p.get("layout") == "pages"}
        ctx.step("시험지 만드는 중", 60)
        if key == "separate":  # 시험지 · 정답지 따로 (같은 문제로)
            test_pdf = render_pdf(tpl, {**ctxv, "part": "test"})
            ctx.step("정답지 만드는 중", 80)
            key_pdf = render_pdf(tpl, {**ctxv, "part": "key", "doc_title": f"{title} · 정답지"})
            files = [{"file_id": ctx.save_file(f"{title}_단어시험.pdf", "application/pdf", test_pdf), "filename": f"{title}_단어시험.pdf"},
                     {"file_id": ctx.save_file(f"{title}_단어시험_정답지.pdf", "application/pdf", key_pdf), "filename": f"{title}_단어시험_정답지.pdf"}]
            return {**files[0], "files": files, "note": note}
        pdf = render_pdf(tpl, {**ctxv, "part": "test" if key == "none" else "all"})
        name = f"{title}_단어시험.pdf"
        fid = ctx.save_file(name, "application/pdf", pdf)
        return {"file_id": fid, "filename": name, "note": note}
    else:
        ctx.step("단어장 만드는 중", 60)
        pdf = render_pdf("pdf/vocab_list.html", {"academy": academy, "title": title, "words": words, "doc_title": f"{title} · 단어장", "today": today,
                                                 "show_example": p.get("show_example", True), "hide": p.get("hide", ""),
                                                 "groups": spec.by_source(words) if p.get("page_src") and len(spec.sources(words)) > 1 else None})
        name = f"{title}_단어장.pdf"
    fid = ctx.save_file(name, "application/pdf", pdf)
    return {"file_id": fid, "filename": name}
