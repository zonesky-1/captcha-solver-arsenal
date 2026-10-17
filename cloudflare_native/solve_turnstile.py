#!/usr/bin/env python3
"""
TOOL 2: Solve Turnstile pakai sitekey (via Camoufox native render).
Usage:  python3 solve_turnstile.py <url> [sitekey]

Cara kerja (skill camoufox-turnstile-automation):
  - Camoufox render halaman, turnstile auto-solve kalau IP punya baseline trust.
  - Submit button jadi enabled setelah token populate (~645 chars) di
    input[name=cf-turnstile-response].
  - Tool ini menunggu token, lalu:
      a) PRINT token mentah ke stdout (buat dipakai program lain), DAN
      b) simpan ke /tmp/turnstile_token.txt, DAN
      c) kalau ada tombol, klik submit biar flow lanjut.
  - Opsional env TURNSTILE_OUT=<path> utk nimpuk lokasi output.

Kalau sitekey di-skip, tool ini auto-scan dari DOM/data-sitekey.
Exit code 0 = dapat token; 1 = gagal/timeout.

PENTING (dari skill):
  - IP datacenter bisa gak dapet trust -> token tetep kosong. Butuh proxy/rotasi.
  - Proxy: env TURNSTILE_PROXY="socks5://user:pass@host:port"
"""
import os, sys, time, json

def load_sitekey_from_page(page):
    res = page.evaluate("""() => {
        const el = document.querySelector('[data-sitekey]');
        return el ? el.getAttribute('data-sitekey') : null;
    }""")
    return res

def solve(page, sitekey):
    # kalo ada sitekey & widget belum dirender via data attr, render manual
    if sitekey and not page.evaluate("() => !!document.querySelector('[data-sitekey], .cf-turnstile, [class*=turnstile]')"):
        page.evaluate("""(sk) => {
            if (window.turnstile) {
                const d = document.createElement('div');
                d.id = '__ts';
                document.body.appendChild(d);
                window.turnstile.render('#__ts', {sitekey: sk,
                    callback: function(t){ console.log('TS_TOKEN='+t); }});
            }
        }""", sitekey)

    # tunggu token muncul di field
    deadline = time.time() + 120
    token = ""
    while time.time() < deadline:
        token = page.evaluate("""() => {
            const f = document.querySelector('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]');
            return f ? f.value : '';
        }""")
        if token and len(token) > 50:
            break
        time.sleep(2)

    if token:
        print(f"TOKEN={token[:80]}... (len={len(token)})")
        out = os.environ.get("TURNSTILE_OUT", "/tmp/turnstile_token.txt")
        with open(out, "w") as f:
            f.write(token)
        print(f"SAVED={out}")
        return token
    return ""

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    url = sys.argv[1]
    sitekey = sys.argv[2] if len(sys.argv) > 2 else None

    from camoufox.sync_api import Camoufox
    os.environ["DISPLAY"] = ":99"

    proxy = os.environ.get("TURNSTILE_PROXY")
    kw = {"headless": False, "humanize": True, "geoip": True if not proxy else False}
    if proxy:
        kw["proxy"] = {"server": proxy}

    with Camoufox(**kw) as b:
        page = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
        page.goto(url, wait_until="load", timeout=90000)
        time.sleep(5)

        if not sitekey:
            sitekey = load_sitekey_from_page(page)
            print(f"Auto sitekey = {sitekey}")

        token = solve(page, sitekey)
        # screenshot biar bisa diinspeksi
        page.screenshot(path="/tmp/turnstile_last.png", full_page=False)

        if token:
            print("RESULT=PASS")
            sys.exit(0)
        else:
            print("RESULT=FAIL (IP gak dapet trust / proxy needed)")
            sys.exit(1)

if __name__ == "__main__":
    main()
