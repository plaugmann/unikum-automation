"""Lokal webserver: RSS til mennesker, JSON til e-ink-skaermen.

Selve genereringen ligger i render.py, saa den lokale server og den udgave,
vi pusher til skyen, bygger paa noejagtig samme kode.

Adgang kraever en token i query-strengen. Det er bevidst simpelt - feedet
skal kunne hentes af en ESP32 paa hjemmenettet, og en ESP32 er daarlig til
baade TLS-certifikater og HTTP-basic-auth.
"""
from __future__ import annotations

import hmac

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from . import config, render

app = FastAPI(title="Unikum-feed", docs_url=None, redoc_url=None)


def _check_token(token: str) -> None:
    if not config.FEED_TOKEN:
        raise HTTPException(500, "FEED_TOKEN er ikke sat i .env")
    # compare_digest undgaar at svartiden roeber, hvor langt et gaet naaede.
    if not hmac.compare_digest(token, config.FEED_TOKEN):
        raise HTTPException(403, "Forkert token")


@app.get("/feed.xml")
def feed(token: str = Query(...), limit: int = Query(60, ge=1, le=300)) -> Response:
    _check_token(token)
    return Response(
        content=render.build_feed(limit=limit),
        media_type="application/rss+xml; charset=utf-8",
    )


@app.get("/display.json")
def display(
    token: str = Query(...),
    count: int = Query(5, ge=1, le=20),
    maxlen: int = Query(120, ge=20, le=500),
) -> JSONResponse:
    _check_token(token)
    return JSONResponse(render.build_display(count=count, maxlen=maxlen))


@app.get("/healthz")
def healthz() -> dict:
    from . import auth

    return {"ok": True, "session": auth.status(), "kode": render.code_version()}
