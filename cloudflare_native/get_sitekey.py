#!/usr/bin/env python3
"""
TOOL 1: Ambil Turnstile sitekey dari sebuah URL.
Usage:  python3 get_sitekey.py <url>
        python3 get_sitekey.py <url> --browser   # paksa pakai Camoufox (buat SPA/JS-render)

Cara kerja:
  - Default: curl HTML mentah, regex cari data-sitekey / turnstile container.
  - --browser: render pake Camoufox (geoip=False biar cepat), scan DOM DOM utk sitekey,
    cek input[name=cf-turnstile-response], dan dump nama field token.
Output: sitekey + lokasinya (attribute/elemen) + apakah token field ada.
"""
import sys, re, json
import urllib.request, urllib.parse

def regex_sitekey(html):
    """Cari sitekey pakai beberapa pola umum turnstile."""
    patterns = [
        r'data-sitekey=["\']([A-Za-z0-9_-]{10,})["\']',
        r'["\']sitekey["\']\s*[:=]\s*["\']([A-Za-z0-9_-]{10,})["\']',
        r'turnstile\.render\([^)]*["\']([A-Za-z0-9_-]{10,})["\']',
        r'sitekey=([A-Za-z0-9_-]{10,})',
    ]
    found = []
    for p in patterns:
        for m in re.finditer(p, html):
            val = m.group(1)
            if val not in [x[0] for x in found]:
                # cari konteks sekitar match
                start = max(0, m.start() - 40)
                ctx = html[start:m.end()].replace('\n', ' ')
                found.append((val, ctx))
    return found

def curl_html(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', 'ignore')

def browser_scan(url):
    from camoufox.sync_api import Camoufox
    import os, time
    os.environ["DISPLAY"] = ":99"
    with Camoufox(headless=False, humanize=True, geoip=False) as b:
        page = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
        page.goto(url, wait_until="load", timeout=60000)
        time.sleep(4)
        res = page.evaluate("""() => {
            const out = {sitekeys: [], elements: []};
            // elemen dgn data-sitekey
            document.querySelectorAll('[data-sitekey]').forEach(el => {
                out.sitekeys.push({key: el.getAttribute('data-sitekey'),
                                   tag: el.tagName, cls: (el.className||'').toString().slice(0,60)});
            });
            // semua input utk cek field token + widget
            document.querySelectorAll('input, iframe, div').forEach(el => {
                const n = el.name || el.id || '';
                if (n.includes('turnstile') || n.includes('cf-') || (el.tagName==='IFRAME' && (el.src||'').includes('challenges.cloudflare.com'))) {
                    const b = el.getBoundingClientRect();
                    out.elements.push({tag: el.tagName, name: n.slice(0,50),
                                       src: (el.src||'').slice(0,80),
                                       visible: b.width>0 && b.height>0});
                }
            });
            return out;
        }""")
        return res

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    url = sys.argv[1]
    use_browser = "--browser" in sys.argv

    if not use_browser:
        try:
            html = curl_html(url)
            keys = regex_sitekey(html)
            if keys:
                print(f"SITEKEY_FOUND: {len(keys)}")
                for k, ctx in keys:
                    print(f"  key={k}\n    ctx=...{ctx}")
                sys.exit(0)
            else:
                print("SITEKEY_NOT_IN_HTML (mungkin SPA - coba --browser)")
                print(f"HTML bytes: {len(html)}")
        except Exception as e:
            print(f"CURL_ERR: {e}")

    # fallback / forced browser
    try:
        res = browser_scan(url)
        print(json.dumps(res, indent=2))
        if res.get("sitekeys"):
            for s in res["sitekeys"]:
                print(f"SITEKEY={s['key']} on <{s['tag']} class=\"{s['cls']}\">")
        else:
            print("SITEKEY_NOT_FOUND_IN_DOM")
    except Exception as e:
        print(f"BROWSER_ERR: {e}")

if __name__ == "__main__":
    main()
