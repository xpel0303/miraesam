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
        if kind == "passage" and not words:
            count = int(ctx.payload.get("count") or 20)
            ctx.step(f"지문에서 단어 {count}개 고르는 중", 20)
            words = _ask(key, model, spec.extract_prompt(text, count, level), count)[: spec.MAX_WORDS]
        else:
            todo = [i for i, w in enumerate(words) if spec.blank_needed(w)]
            chunks = [todo[i:i + spec.CHUNK] for i in range(0, len(todo), spec.CHUNK)]
            for n, chunk in enumerate(chunks, 1):
                ctx.step(f"빈칸 채우는 중 ({n}/{len(chunks)})", int(10 + 80 * (n - 1) / max(1, len(chunks))))
                filled = _ask(key, model, spec.fill_prompt([words[i] for i in chunk], text if kind == "passage" else "", level), len(chunk))
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
        _set(list_id, words=words, status="done", error=(f"뜻을 채우지 못한 단어: {', '.join(missing[:8])}" if missing else None))
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
        rng = random.Random(p.get("seed") or 1)
        pool = [w for w in words if w.get("meaning")]
        picked = rng.sample(pool, min(int(p.get("count") or len(pool)), len(pool))) if p.get("shuffle", True) else pool[: int(p.get("count") or len(pool))]
        mode = p.get("mode", "en_ko")
        qs = []
        for w in picked:
            m = mode if mode != "mix" else rng.choice(["en_ko", "ko_en"])
            qs.append({"show": w["word"] if m == "en_ko" else w["meaning"], "answer": w["meaning"] if m == "en_ko" else w["word"], "dir": m})
        ctx.step("시험지 만드는 중", 60)
        pdf = render_pdf("pdf/vocab_test.html", {"academy": academy, "title": title, "qs": qs, "doc_title": f"{title} · 단어 시험", "today": today, "mode": mode})
        name = f"{title}_단어시험.pdf"
    else:
        ctx.step("단어장 만드는 중", 60)
        pdf = render_pdf("pdf/vocab_list.html", {"academy": academy, "title": title, "words": words, "doc_title": f"{title} · 단어장", "today": today,
                                                 "show_example": p.get("show_example", True), "hide": p.get("hide", "")})
        name = f"{title}_단어장.pdf"
    fid = ctx.save_file(name, "application/pdf", pdf)
    return {"file_id": fid, "filename": name}
