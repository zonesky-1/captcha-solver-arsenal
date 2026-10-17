#!/usr/bin/env python3
"""
eni_ts_api.py — API server murni buatan sendiri utk sitekey finder + Turnstile solver.

Rute:
  GET  /sitekey?url=...                cari sitekey (HTML+bundle+render+validasi CF)
  GET  /solve?url=...&sitekey=...      solve Turnstile, balas token
  GET  /health                         status + statistik
  GET  /stats                          detail statistik
  GET  /proxy-check                    status egress

Bind 127.0.0.1 default (ENI_TS_HOST/ENI_TS_PORT). JANGAN 0.0.0.0.
Tidak memakai layanan pihak ketiga / npm solver / API key.
"""
from __future__ import annotations

import os
import threading
import time
import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from sk_finder import deep_scan
from ts_engine import TurnstileEngine

START = time.time()
app = FastAPI(title="ENI Turnstile API", version="1.0", docs_url="/docs")

_eng = TurnstileEngine()
_eng_lock = threading.Lock()

# --------------------------------------------------------------- helpers
def _eng_solve(url: str, sitekey: str, **kw) -> dict:
    with _eng_lock:
        return _eng.solve(url, sitekey, **kw)


# --------------------------------------------------------------- routes
@app.get("/health")
async def health():
    return {"ok": True, "service": "eni-ts-api", "uptime_s": round(time.time() - START, 1),
            "engine": {"solves": _eng.stats["solves"], "ok": _eng.stats["ok"],
                       "fail": _eng.stats["fail"],
                       "avg_ms": round(_eng.stats["total_ms"] / max(1, _eng.stats["solves"]), 1)}}


@app.get("/stats")
async def stats():
    return _eng.stats


@app.get("/sitekey")
async def sitekey(url: str = Query(...), render: bool = Query(True),
                  validate: bool = Query(True)):
    return deep_scan(url, render=render, validate=validate)


@app.get("/solve")
async def solve(url: str = Query(...), sitekey: str = Query(...),
                action: str = Query(None), deliver: str = Query("api"),
                timeout: float = Query(45.0), click_submit: bool = Query(False),
                submit_selector: str = Query(None), settle: float = Query(2.0),
                keep_open: float = Query(0.0)):
    return _eng_solve(url, sitekey, action=action or None, deliver=deliver,
                      timeout=timeout, click_submit=click_submit,
                      submit_selector=submit_selector or None, settle=settle,
                      keep_open=keep_open)


@app.get("/proxy-check")
async def proxy_check():
    from curl_cffi import requests as cr  # noqa: PLC0415
    probes = {"direct": None, "warp": "socks5://127.0.0.1:1080"}
    out = {}
    for name, px in probes.items():
        try:
            kw = {"impersonate": "chrome131", "timeout": 8}
            if px:
                kw["proxies"] = {"http": px, "https": px}
            r = cr.get("https://challenges.cloudflare.com/cdn-cgi/trace", **kw)
            out[name] = {"status": r.status_code, "via": name}
        except Exception as exc:  # noqa: BLE001
            out[name] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


if __name__ == "__main__":
    uvicorn.run(app, host=os.getenv("ENI_TS_HOST", "127.0.0.1"),
                port=int(os.getenv("ENI_TS_PORT", "8123")),
                log_level="warning", access_log=False)
