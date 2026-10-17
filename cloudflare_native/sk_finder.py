#!/usr/bin/env python3
"""
sk_finder.py — PENCARI SITEKEY (murni buatan sendiri, tanpa layanan pihak ketiga)

Cara kerja:
  1. Unduh HTML target (curl_cffi impersonate Chrome, fallback urllib).
  2. Cari sitekey dengan 8 pola regex resmi (data-sitekey, turnstile.render,
     api.js?sitekey=, sitekey:, dll) + ambil konteks di sekitarnya.
  3. IKUTI semua <script src> same-origin lalu scan juga (banyak SPA menaruh
     sitekey di bundle JS, bukan di HTML).
  4. Validasi tiap kandidat ke endpoint CF resmi: kalau CF balas 200 = sitekey
     hidup, 400/404 = sampah. Ini murni HTTP, cepat (~200ms/kandidat).
  5. Mode render (Playwright): buka halaman, tunggu elemen [data-sitekey] /
     iframe challenges.cloudflare.com, baca sitekey dari DOM + nilai hidden
     input cf-turnstile-response.

Hasil: daftar kandidat unik + status validasi + lokasi asal (html / bundle / dom).
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Iterable, Optional
from urllib.parse import urljoin, urlparse

try:
    from curl_cffi import requests as cr
except Exception:  # pragma: no cover
    cr = None

import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# --- pola resmi Turnstile -------------------------------------------------
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("data-sitekey", re.compile(r'data-sitekey\s*=\s*["\']([A-Za-z0-9_-]{8,})["\']')),
    ("sitekey-prop", re.compile(r'["\']?sitekey["\']?\s*[:=]\s*["\']([A-Za-z0-9_-]{8,})["\']')),
    ("render-arg", re.compile(r'turnstile\.render\s*\(\s*[^,)]{0,120}?["\']([0-9A-Za-z_-]{16,})["\']')),
    ("render-object", re.compile(r'turnstile\.render\s*\([^)]*?\{[^}]{0,200}?sitekey\s*:\s*["\']([A-Za-z0-9_-]{8,})["\']', re.S)),
    ("apijs-query", re.compile(r'challenges\.cloudflare\.com/turnstile/v0/api\.js\?[^"\'\s>]*?sitekey=([A-Za-z0-9_-]{8,})')),
    ("script-src-q", re.compile(r'turnstile/v0/api\.js[^"\']{0,200}?render=([0-9A-Za-z_-]{20,})')),
    ("generic-0x4", re.compile(r'(0x4[A-Za-z0-9_-]{10,})')),
    ("tmp-key", re.compile(r'["\'](0x[0-9A-Fa-f]{8,}[A-Za-z0-9_-]{4,})["\']')),
]

SCRIPT_SRC = re.compile(r'<script[^>]+src\s*=\s*["\']([^"\']+)["\']', re.I)
# sitekey yang jelas bukan sitekey
BLACKLIST = re.compile(r'^(0x0+|test|dummy|none|null|undefined)$', re.I)


# ------------------------------------------------------------------ fetch
def fetch(url: str, timeout: float = 25.0, proxy: Optional[str] = None) -> dict:
    """Ambil HTML. Balas {ok, status, text, err}."""
    hdr = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
           "Accept-Language": "en-US,en;q=0.9"}
    if cr is not None:
        try:
            kw: dict[str, Any] = {"impersonate": "chrome131", "timeout": timeout, "headers": hdr}
            if proxy:
                kw["proxies"] = {"http": proxy, "https": proxy}
            r = cr.get(url, **kw)
            return {"ok": True, "status": r.status_code, "text": r.text, "via": "curl_cffi"}
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
    else:
        last = "curl_cffi tidak ada"
    try:
        req = urllib.request.Request(url, headers=hdr)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"ok": True, "status": resp.status, "text": resp.read().decode("utf-8", "ignore"),
                    "via": "urllib", "note": last}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": 0, "text": "", "err": f"{last} | urllib: {exc}"}


# ------------------------------------------------------------------ scan
def scan_text(text: str, origin: str) -> list[dict]:
    """Cari semua kandidat sitekey dalam sebuah teks."""
    out: list[dict] = []
    seen: set[str] = set()
    for name, rx in PATTERNS:
        for m in rx.finditer(text):
            key = m.group(1)
            if key in seen or BLACKLIST.match(key):
                continue
            # sitekey Turnstile asli selalu 0x4... ; pola generik disaring
            if name in ("generic-0x4", "tmp-key") and not key.startswith("0x4"):
                continue
            if len(key) < 20 and not key.startswith("0x4"):
                continue
            seen.add(key)
            ctx = text[max(0, m.start() - 60):m.end() + 30].replace("\n", " ")
            out.append({"sitekey": key, "pattern": name, "origin": origin,
                        "ctx": " ".join(ctx.split())[:180]})
    return out


def follow_bundles(html: str, base_url: str, limit: int = 8, timeout: float = 20.0,
                   proxy: Optional[str] = None) -> list[dict]:
    """Ikuti <script src> same-origin, scan isinya (SPA sering simpan sitekey di bundle)."""
    host = urlparse(base_url).netloc
    found: list[dict] = []
    srcs, seen = [], set()
    for m in SCRIPT_SRC.finditer(html):
        s = m.group(1)
        full = urljoin(base_url, s)
        if urlparse(full).netloc != host or full in seen:
            continue
        seen.add(full)
        srcs.append(full)
    for s in srcs[:limit]:
        r = fetch(s, timeout=timeout, proxy=proxy)
        if not r.get("ok"):
            continue
        found += scan_text(r["text"], f"bundle:{urlparse(s).path.rsplit('/', 1)[-1][:40]}")
    return found


# ------------------------------------------------------- validasi ke CF
CF_VALIDATE = ("https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/"
               "turnstile/if/ov2/av0/rcv0/0/{sk}/dark/fbE/normal/auto/")
CF_SITEVERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def validate_sitekey(sitekey: str, timeout: float = 8.0,
                     proxy: Optional[str] = None) -> dict:
    """
    Cek sitekey hidup/mati tanpa solver. Dua bukti:
      A) iframe bootstrap CF: 200 = widget bisa dibuat, 400/404 = kunci mati/salah format.
      B) siteverify: balasan 'invalid-input-secret' = sitekey DIKENAL CF
         (secret kita memang kosong); 'invalid-input-response' = kunci tak dikenal.
    """
    res: dict[str, Any] = {"sitekey": sitekey, "alive": None, "evidence": []}
    url = CF_VALIDATE.format(sk=sitekey)
    try:
        if cr is not None:
            kw: dict[str, Any] = {"impersonate": "chrome131", "timeout": timeout,
                                  "headers": {"User-Agent": UA, "Referer": "https://example.com/"}}
            if proxy:
                kw["proxies"] = {"http": proxy, "https": proxy}
            r = cr.get(url, **kw)
            code = r.status_code
            body = r.text[:400]
        else:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                       "Referer": "https://example.com/"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                code = resp.status
                body = resp.read(400).decode("utf-8", "ignore")
        res["evidence"].append({"probe": "iframe-bootstrap", "status": code})
        res["alive"] = code == 200
        if code != 200:
            res["note"] = body[:160]
    except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
        res["evidence"].append({"probe": "iframe-bootstrap", "status": exc.code})
        res["alive"] = False
    except Exception as exc:  # noqa: BLE001
        res["evidence"].append({"probe": "iframe-bootstrap", "error": f"{type(exc).__name__}: {exc}"})
    if res["alive"] is None:
        # fallback: siteverify
        try:
            if cr is not None:
                r = cr.post(CF_SITEVERIFY, impersonate="chrome131", timeout=timeout,
                            data={"secret": "0x0", "response": "dummy"})
                j = r.json() if "json" in (r.headers.get("content-type") or "") else {}
            else:
                j = {}
            codes = j.get("error-codes", [])
            res["evidence"].append({"probe": "siteverify", "codes": codes})
            res["alive"] = "invalid-input-secret" in codes
        except Exception as exc:  # noqa: BLE001
            res["evidence"].append({"probe": "siteverify", "error": str(exc)[:120]})
    return res


# ------------------------------------------------------- render mode (DOM)
RENDER_JS = r"""
() => {
  const out = {sitekeys: [], widgets: [], iframes: [], token: ''};
  document.querySelectorAll('[data-sitekey]').forEach(el => {
    const b = el.getBoundingClientRect();
    out.sitekeys.push({key: el.getAttribute('data-sitekey'), tag: el.tagName,
                       cls: (el.className || '').toString().slice(0, 70),
                       visible: b.width > 0 && b.height > 0, w: Math.round(b.width), h: Math.round(b.height)});
  });
  document.querySelectorAll('.cf-turnstile, [class*=turnstile], [id*=turnstile]').forEach(el => {
    const b = el.getBoundingClientRect();
    out.widgets.push({tag: el.tagName, id: el.id, cls: (el.className || '').toString().slice(0, 70),
                      sk: el.getAttribute('data-sitekey'), size: el.getAttribute('data-size'),
                      action: el.getAttribute('data-action'),
                      visible: b.width > 0 && b.height > 0, w: Math.round(b.width), h: Math.round(b.height)});
  });
  document.querySelectorAll('iframe').forEach(f => {
    if ((f.src || '').includes('challenges.cloudflare.com')) {
      const b = f.getBoundingClientRect();
      const m = (f.src || '').match(/\/(0x4[A-Za-z0-9_-]+)\//);
      out.iframes.push({src: f.src.slice(0, 180), sk_from_src: m ? m[1] : null,
                        visible: b.width > 0 && b.height > 0, w: Math.round(b.width), h: Math.round(b.height)});
    }
  });
  const ti = document.querySelector('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]');
  out.token = ti ? (ti.value || '') : '';
  out.has_turnstile_global = !!window.turnstile;
  return out;
}
"""


def deep_scan(url: str, render: bool = False, timeout: float = 30.0,
              proxy: Optional[str] = None, validate: bool = True,
              follow_scripts: bool = True, settle: float = 3.5,
              force_render: bool = False) -> dict:
    """Pencarian lengkap: HTML -> bundle -> (opsional) DOM render -> validasi CF.

    force_render=True -> tetap buka browser walaupun HTML sudah ketemu sitekey
    (perlu, karena HTML bisa 403 sementara render dapat data lengkap).
    """
    t0 = time.perf_counter()
    report: dict[str, Any] = {"url": url, "candidates": [], "steps": []}

    r = fetch(url, timeout=timeout, proxy=proxy)
    report["steps"].append({"http": r.get("status"), "bytes": len(r.get("text") or ""),
                            "via": r.get("via"), "err": r.get("err")})
    cands: list[dict] = []
    if r.get("ok"):
        cands += scan_text(r["text"], "html")
        if follow_scripts:
            b = follow_bundles(r["text"], url, limit=8 if not cands else 4, proxy=proxy)
            report["steps"].append({"bundles_scanned": True, "found": len(b)})
            cands += b

    if render or force_render:
        try:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415
            headless = os.getenv("ENI_TS_HEADLESS", "1") == "1"
            with sync_playwright() as pw:
                kw: dict[str, Any] = {"headless": headless,
                                      "args": ["--no-sandbox", "--disable-dev-shm-usage",
                                               "--disable-blink-features=AutomationControlled"]}
                if proxy:
                    kw["proxy"] = {"server": proxy}
                b = pw.chromium.launch(**kw)
                ctx = b.new_context(user_agent=UA, viewport={"width": 1366, "height": 900},
                                    locale="en-US")
                pg = ctx.new_page()
                pg.goto(url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
                pg.wait_for_timeout(int(settle * 1000))
                dom = pg.evaluate(RENDER_JS)
                report["dom"] = dom
                for s in dom.get("sitekeys", []):
                    if s.get("key"):
                        cands.append({"sitekey": s["key"], "pattern": "dom-attr",
                                      "origin": "render", "ctx": f"<{s['tag']} class={s['cls']}>"})
                for w in dom.get("widgets", []):
                    if w.get("sk"):
                        cands.append({"sitekey": w["sk"], "pattern": "dom-widget",
                                      "origin": "render",
                                      "ctx": f"size={w.get('size')} action={w.get('action')}"})
                for f in dom.get("iframes", []):
                    if f.get("sk_from_src"):
                        cands.append({"sitekey": f["sk_from_src"], "pattern": "iframe-src",
                                      "origin": "render", "ctx": f["src"][:120]})
                ctx.close()
                b.close()
        except Exception as exc:  # noqa: BLE001
            report["steps"].append({"render_error": f"{type(exc).__name__}: {exc}"})

    # dedupe
    uniq: dict[str, dict] = {}
    for c in cands:
        uniq.setdefault(c["sitekey"], c)
    report["candidates"] = list(uniq.values())

    if validate and report["candidates"]:
        for c in report["candidates"]:
            c["validation"] = validate_sitekey(c["sitekey"], proxy=proxy)

    report["count"] = len(report["candidates"])
    report["best"] = next((c["sitekey"] for c in report["candidates"]
                           if (c.get("validation") or {}).get("alive")), None) \
        or (report["candidates"][0]["sitekey"] if report["candidates"] else None)
    report["ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return report


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    u = sys.argv[1]
    use_render = "--render" in sys.argv
    out = deep_scan(u, render=use_render or True, validate="--no-validate" not in sys.argv,
                    proxy=os.getenv("ENI_TS_PROXY"))
    print(json.dumps(out, indent=2)[:4000])
