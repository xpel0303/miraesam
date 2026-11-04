"""흐름분석 작업: 지문마다 우리말 구조화(AI) → 네 컷 만화(그림 모델) → A4 가로 PDF."""
import base64
import copy
import io

from PIL import Image

from sqlalchemy.orm.attributes import flag_modified

from .. import llm
from ..db import session_scope
from ..flow import spec
from ..jobs import JobContext, job_handler
from ..models import Academy, FlowSet, StoredFile
from ..pdf import first_fit, render_pdf
from .analysis import _shrink

def _stand_in(w: int, h: int) -> str:
    """글씨 크기 고를 때 진짜 그림 대신 넣는 같은 크기(가로×세로)의 빈 그림 (SVG, 몇 바이트).
    진짜 그림(장당 수백 KB)을 지문 수 × 크기 단계만큼 복사해 그리면 무료 서버 메모리(512MB)가 넘침.
    크기가 다르면 맞춘 글씨 크기가 틀려 한 쪽을 넘김 → 반드시 진짜 그림과 같은 가로·세로로."""
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}"><rect width="{w}" height="{h}" fill="#ddd"/></svg>'
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


LEVELS = 10  # pdf/flow.html 의 SCALES 개수 (0 = 가장 크게)


def _save(set_id, idx, data):
    with session_scope() as db:
        s = db.get(FlowSet, set_id)
        ps = copy.deepcopy(s.passages)
        ps[idx].update(data)
        s.passages = ps
        flag_modified(s, "passages")


def _load(set_id) -> FlowSet:
    with session_scope() as db:
        s = db.get(FlowSet, set_id)
        db.expunge(s)
    return s


def _one(key, model, p: dict, level: str) -> dict:
    fix, best = None, None
    for _ in range(2):
        raw = llm.generate(key, spec.prompt(p["text"], level, fix), system=spec.SYSTEM_PROMPT, model=model, json_mode=True,
                           temperature=0.5, max_tokens=6000)
        out, errs = spec.clean(raw, p["text"])
        if best is None or len(errs) < len(best[1]):
            best = (out, errs)
        if not errs:
            break
        fix = errs
    out, errs = best
    if not out["top"] or len(out["comic"]) < 4:
        raise llm.LLMError("흐름분석 내용을 받지 못했습니다: " + " / ".join(errs[:3]))
    return {"out": out, "warnings": errs[:4], "status": "done", "error": None, "img": None, "img_error": None}


def _draw(ctx: JobContext, set_id: int, only: list[int] | None = None):
    s = _load(set_id)
    todo = [i for i, p in enumerate(s.passages) if p.get("status") == "done" and not p.get("img") and (only is None or i in only)]
    if not todo:
        return
    key, _ = ctx.gemini()
    for n, idx in enumerate(todo, 1):
        ctx.step(f"네 컷 만화 그리는 중 ({n}/{len(todo)})", 55 + int(30 * (n - 1) / len(todo)))
        try:
            img = llm.generate_image(key, spec.image_prompt(s.passages[idx]["out"]), aspect="1:1")
        except llm.LLMError as e:
            _save(set_id, idx, {"img_error": str(e)})
            if "API 키" in str(e) or "쓸 수 없습니다" in str(e):
                break  # 이 키로는 그림을 못 그림 → 나머지는 글 칸으로
            continue
        fid = ctx.save_file(f"네컷만화_{idx + 1}.jpg", "image/jpeg", _shrink(img, 1100))
        _save(set_id, idx, {"img": fid, "img_error": None})


def _with_images(passages: list[dict]) -> list[dict]:
    ids = [p.get("img") for p in passages if p.get("img")]
    files = {}
    if ids:
        with session_scope() as db:
            files = {f.id: (f.mime, f.data) for f in db.query(StoredFile).filter(StoredFile.id.in_(ids))}
    out = []
    for p in passages:
        f = files.get(p.get("img"))
        wh = None
        if f:
            try:
                wh = Image.open(io.BytesIO(f[1])).size
            except Exception:  # noqa: BLE001
                wh = (1024, 1024)
        out.append({**p, "img_src": f"data:{f[0]};base64,{base64.b64encode(f[1]).decode()}" if f else None, "img_wh": wh})
    return out


def render_flow_pdf(ctx: JobContext, set_id: int) -> dict:
    with session_scope() as db:
        s = db.get(FlowSet, set_id)
        academy = db.get(Academy, s.academy_id)
        db.expunge(s)
        db.expunge(academy)
    ps = _with_images([p for p in s.passages if p.get("status") == "done"])
    if not ps:
        raise llm.LLMError("PDF에 넣을 지문이 없습니다.")
    ctx.step("한 쪽에 맞게 글자 크기 고르는 중", 88)
    base = {"academy": academy, "s": s, "doc_title": s.title}
    light = [{**p, "img_src": _stand_in(*p["img_wh"]) if p.get("img_src") else None} for p in ps]
    for start in range(0, len(ps), 3):  # 몇 개씩 나눠서 (한 번에 다 그리면 메모리가 쌓임)
        groups = [[{**base, "passages": [{**p, "z": z}]} for z in range(LEVELS)] for p in light[start:start + 3]]
        for p, z in zip(ps[start:start + 3], first_fit("pdf/flow.html", groups)):
            p["z"] = z
    ctx.step("PDF 만드는 중", 94)
    pdf = render_pdf("pdf/flow.html", {**base, "passages": ps}, chunk_key="passages", chunk=2)
    name = f"{s.title}_흐름분석.pdf"
    fid = ctx.save_file(name, "application/pdf", pdf)
    with session_scope() as db:
        row = db.get(FlowSet, set_id)
        old, row.file_id = row.file_id, fid
        row.options = {k: v for k, v in (row.options or {}).items() if k != "dirty"}
        db.flush()
        if old and (f := db.get(StoredFile, old)) is not None:
            db.delete(f)
    return {"file_id": fid, "filename": name, "flow_id": set_id}


@job_handler("flow_build")
def build(ctx: JobContext) -> dict:
    set_id = ctx.payload["flow_id"]
    only = ctx.payload.get("only")
    key, model = ctx.gemini()
    s = _load(set_id)
    level = (s.options or {}).get("level", "")
    targets = [i for i in range(len(s.passages)) if (only is None and s.passages[i].get("status") != "done") or (only and i in only)]
    failed = []
    for k, i in enumerate(targets, 1):
        p = s.passages[i]
        ctx.step(f"{p.get('title') or f'지문 {i + 1}'} 흐름 정리하는 중 ({k}/{len(targets)})", int(5 + 50 * (k - 1) / max(1, len(targets))))
        _save(set_id, i, {"status": "making", "error": None})
        try:
            _save(set_id, i, _one(key, model, p, level))
        except llm.LLMError as e:
            _save(set_id, i, {"status": "failed", "error": str(e)})
            failed.append(f"지문 {i + 1}")
            if "API 키" in str(e) or "모델을 찾을 수" in str(e):
                raise
    _draw(ctx, set_id, only)
    if failed:
        raise llm.LLMError(f"{', '.join(failed)}을(를) 정리하지 못했습니다. 그 지문만 다시 만들 수 있어요.")
    return render_flow_pdf(ctx, set_id)


@job_handler("flow_draw")
def redraw(ctx: JobContext) -> dict:
    _draw(ctx, ctx.payload["flow_id"], ctx.payload.get("only"))
    return render_flow_pdf(ctx, ctx.payload["flow_id"])


@job_handler("flow_render")
def rerender(ctx: JobContext) -> dict:
    return render_flow_pdf(ctx, ctx.payload["flow_id"])
