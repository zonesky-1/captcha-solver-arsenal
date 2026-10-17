#!/usr/bin/env python3
"""
probe_pure.py — uji jalur PURE-HTTP: ambil URL iframe challenge Turnstile yang asli
(milik sitekey managed), lalu tarik isinya lewat curl_cffi dan cari struktur PoW /
endpoint yang bisa dihitung tanpa browser.

Tujuan: kalau iframe challenge itu HTML+JS statis yang kerjanya cuma submit PoW,
maka solve bisa murni Python. Kalau isinya cuma loader buat worker/wasm yang butuh
DOM, kita lapor jujur bahwa jalur pure-HTTP mentok di situ.
"""
import json
import re
from curl_cffi import requests as cr
from ts_engine import TurnstileEngine

SK = "0x4AAAAAAADnPIDROrmt1Wwj"   # managed widget di turnstile.zeroclover.io
URL = "https://turnstile.zeroclover.io/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

eng = TurnstileEngine(headless=True)
ctx = eng._browser().new_context(viewport={"width": 1366, "height": 900})
p = ctx.new_page()
p.goto(URL, wait_until="domcontentloaded", timeout=90000)
p.wait_for_timeout(7000)

frames = p.evaluate("""() => [...document.querySelectorAll('iframe')].map(f => ({
    src: f.src || '', id: f.id || '', cls: (f.className||'').toString().slice(0,60),
    w: Math.round(f.getBoundingClientRect().width),
    h: Math.round(f.getBoundingClientRect().height)}))""")
print("=== iframes di halaman ===")
print(json.dumps(frames, indent=1)[:1500])

# isi iframe via frame API (bukan contentDocument)
inner = {}
for fr in p.frames:
    u = fr.url
    if "challenges.cloudflare.com" in u or "challenge-platform" in u:
        try:
            txt = fr.evaluate("() => document.body ? document.body.innerText.slice(0,400) : ''")
        except Exception as e:  # noqa: BLE001
            txt = f"ERR {e}"
        try:
            scripts = fr.evaluate("() => [...document.querySelectorAll('script')].map(s=>(s.src||'(inline)').slice(0,140))")
        except Exception as e:  # noqa: BLE001
            scripts = [f"ERR {e}"]
        try:
            forms = fr.evaluate("() => [...document.querySelectorAll('form')].map(f=>({action:f.action, method:f.method}))")
        except Exception as e:  # noqa: BLE001
            forms = [f"ERR {e}"]
        inner[u] = {"text": txt, "scripts": scripts, "forms": forms,
                    "html_len": len(fr.content()) if hasattr(fr, "content") else None}
print("\n=== isi frame CF ===")
print(json.dumps(inner, indent=1)[:2500])
ctx.close()
eng.close()

# pure-HTTP: coba tarik URL iframe pertama
urls = [f["src"] for f in frames if f.get("src")]
for u in urls[:2]:
    print("\n" + "=" * 70)
    print("PURE-HTTP FETCH:", u[:140])
    try:
        r = cr.get(u, impersonate="chrome131", timeout=20,
                   headers={"User-Agent": UA, "Referer": URL + "/"})
        print("status", r.status_code, "len", len(r.text))
        body = r.text
        print("--- awal 600 char ---")
        print(body[:600].replace("\n", " "))
        print("--- sinyal ---")
        for kw in ["proof", "work", "wasm", "Worker", "sha256", "crypto.subtle",
                   "challenge", "turnstile", "token", "fetch(", "XMLHttpRequest",
                   "importScripts", "WebAssembly"]:
            print(f"  {kw:18s} {body.count(kw)}")
        print("--- script/url di dalam ---")
        for s in sorted(set(re.findall(r'["\'](https?://[^"\']{10,120}|/[a-zA-Z0-9_./-]{10,120})["\']', body)))[:12]:
            print("   ", s)
    except Exception as exc:  # noqa: BLE001
        print("ERR", type(exc).__name__, str(exc)[:160])
