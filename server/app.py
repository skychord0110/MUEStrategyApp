"""MUEStrategy をスマホから見るための中継サーバ（閲覧専用）。

構成:
  スマホ ─(Tailscale・HTTPS)→ tailscale serve ─→ 127.0.0.1:8765 このサーバ
                                                  └ 本番 MUEStrategy のファイルを読むだけ

守っていること:
  - kabuステーションAPIを呼ばない（トークンを発行するとランナーの発注が止まる。
    詳細は CLAUDE.md）。ランナーが書き出すファイルを読むだけ。
  - 閲覧専用。GET/HEAD 以外のメソッドはすべて拒否する（書き込みAPIは作らない）。
  - 待ち受けはループバックのみ。tailscale serve が付ける Tailscale-User-Login を
    許可リストと照合し、一致しなければ何も返さない。
"""
import logging
import os
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from datetime import date
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import db, ingest, queries
from . import settings as settings_mod

log = logging.getLogger("mueapp")

LOGIN_HEADER = "Tailscale-User-Login"
_DATE = r"^\d{4}-\d{2}-\d{2}$"


# 口座の写しを取る間隔（秒）。ランナーの書き出し間隔（15秒）に合わせる
ACCOUNT_SAVE_INTERVAL = 15


def _ingest_loop(settings, stop: threading.Event):
    conn = db.connect(settings.db_path)
    next_ingest = 0.0
    try:
        while True:
            try:
                queries.save_last_account(settings)
            except Exception:
                log.exception("口座の写しを保存できませんでした")
            if time.monotonic() >= next_ingest:
                next_ingest = time.monotonic() + settings.ingest_interval_seconds
                try:
                    counts = ingest.run(conn, settings)
                    if any(counts.values()):
                        log.info("取り込み: %s", counts)
                except Exception:
                    log.exception("取り込みに失敗しました（次の周期でやり直します）")
            if stop.wait(ACCOUNT_SAVE_INTERVAL):
                return
    finally:
        conn.close()


def create_app(settings=None, run_ingest: bool = True) -> FastAPI:
    settings = settings or settings_mod.load()
    db.connect(settings.db_path).close()     # スキーマを用意しておく

    @asynccontextmanager
    async def lifespan(app):
        stop = threading.Event()
        t = None
        if run_ingest:
            t = threading.Thread(target=_ingest_loop, args=(settings, stop),
                                 name="ingest", daemon=True)
            t.start()
        yield
        stop.set()
        if t:
            t.join(timeout=5)

    app = FastAPI(title="MUEStrategy Mobile", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.method not in ("GET", "HEAD"):
            return JSONResponse({"detail": "閲覧専用です"}, status_code=405)
        login = request.headers.get(LOGIN_HEADER, "").strip().lower()
        if not login or login not in settings.allowed_logins:
            return PlainTextResponse("Forbidden", status_code=403)
        response = await call_next(request)
        h = response.headers
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "no-referrer"
        h["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        if request.url.path.startswith("/api/"):
            h["Cache-Control"] = "no-store"
        else:
            # 画面の部品は毎回更新を確かめさせる（更新したのに古いJSが動き続けないように）
            h["Cache-Control"] = "no-cache"
        return response

    def conn():
        # リクエストごとに読み取り専用で開く（書き込むのは取り込みスレッドだけ）
        c = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        return c

    def kind_param(kind: str) -> str:
        if kind not in ("paper", "real"):
            raise HTTPException(400, "kind は paper か real")
        return kind

    @app.get("/api/me")
    def me(request: Request):
        return {"login": request.headers.get(LOGIN_HEADER)}

    @app.get("/api/status")
    def status():
        with conn() as c:
            return queries.status(c, settings)

    @app.get("/api/account")
    def account():
        with conn() as c:
            return queries.account(settings, c)

    @app.get("/api/trades")
    def trades(kind: str = "paper",
               date_from: Optional[str] = Query(None, alias="from", pattern=_DATE),
               date_to: Optional[str] = Query(None, alias="to", pattern=_DATE),
               strategy: Optional[str] = Query(None, max_length=64),
               estimated: bool = False,
               limit: int = Query(500, ge=1, le=5000)):
        with conn() as c:
            return queries.trades(c, kind_param(kind), date_from, date_to, strategy,
                                  estimated, limit)

    @app.get("/api/performance")
    def performance(kind: str = "paper",
                    date_from: Optional[str] = Query(None, alias="from", pattern=_DATE),
                    date_to: Optional[str] = Query(None, alias="to", pattern=_DATE),
                    estimated: bool = False):
        with conn() as c:
            return queries.performance(c, kind_param(kind), date_from, date_to, estimated)

    @app.get("/api/orders")
    def orders(date_from: Optional[str] = Query(None, alias="from", pattern=_DATE),
               date_to: Optional[str] = Query(None, alias="to", pattern=_DATE)):
        acc = queries.account(settings)
        snap = acc["snapshot"] or {}
        # 停止中でも、最後の値が今日のものなら今日の注文として出す（source で区別できる）
        today = (snap.get("orders") if snap.get("updated_at", "")[:10] == date.today().isoformat()
                 else None)
        with conn() as c:
            return {"today": today, "today_source": acc["source"] if today is not None else None,
                    "today_at": snap.get("updated_at") if today is not None else None,
                    "history": queries.order_history(c, date_from, date_to)}

    @app.get("/api/reports")
    def reports():
        return queries.reports(settings)

    @app.get("/api/reports/{dirname}/{filename}")
    def report(dirname: str, filename: str):
        text = queries.report_text(settings, dirname, filename)
        if text is None:
            raise HTTPException(404)
        return PlainTextResponse(text)

    app.mount("/", StaticFiles(directory=os.path.join(settings_mod.APP_ROOT, "web"), html=True),
              name="web")
    return app
