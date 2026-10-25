from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import handlers  # noqa: F401  (작업 처리기 등록)
from .config import BASE_DIR, SECRET_KEY
from .db import init_db
from .jobs import start_workers, stop_workers
from .routes import analysis, auth, comprehension, exam, grammar, home, homework, leveltest, paraphrase, reports, roster, sentence, settings, student, variant, vocab
from .web import LoginRequired, render


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from .leveltest.pack import seed_builtin_background
    seed_builtin_background()  # 사이트에 들어 있는 레벨테스트 시험지를 학원마다 넣기
    start_workers()
    yield
    stop_workers()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, session_cookie="hw_session", max_age=60 * 60 * 24 * 30, same_site="lax", https_only=False)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

for r in (auth, home, settings, roster, student, comprehension, analysis, vocab, grammar, variant, sentence, paraphrase, reports, homework, exam, leveltest):
    app.include_router(r.router)


@app.exception_handler(LoginRequired)
async def to_login(request: Request, exc: LoginRequired):
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/api/"):
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    message = exc.detail if isinstance(exc.detail, str) and exc.status_code != 404 else "페이지를 찾을 수 없습니다."
    return render(request, "error.html", {"message": message, "code": exc.status_code}, exc.status_code)


@app.get("/healthz")
def healthz():
    return {"ok": True}
