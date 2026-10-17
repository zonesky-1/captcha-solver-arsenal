#!/usr/bin/env python3
"""probe_iframe.py — lihat isi iframe CF + apakah token muncul di halaman test managed."""
import json
from ts_engine import TurnstileEngine

JS = """() => {
  const ifr = [...document.querySelectorAll('iframe')].map(f => ({
      src: (f.src || '').slice(0, 150),
      w: Math.round(f.getBoundingClientRect().width),
      h: Math.round(f.getBoundingClientRect().height)}));
  const cf = document.querySelector('iframe[src*=challenges]');
  let inner = '';
  try {
    if (cf && cf.contentDocument) {
      inner = cf.contentDocument.body ? cf.contentDocument.body.innerText.slice(0, 300) : '';
    }
  } catch (e) { inner = 'ERR:' + e.message; }
  const host = document.querySelector('.cf-turnstile');
  const tok = document.querySelector('input[name="cf-turnstile-response"]');
  return {ifr: ifr, inner: inner,
          host_html: host ? host.outerHTML.slice(0, 220) : '',
          token_value: tok ? tok.value : '',
          ts_global: !!window.turnstile};
}"""

eng = TurnstileEngine(headless=True)
ctx = eng._browser().new_context(viewport={"width": 1366, "height": 900})
p = ctx.new_page()
p.goto("https://demo.turnstile.workers.dev/", wait_until="domcontentloaded", timeout=60000)
p.wait_for_timeout(4000)
print(json.dumps(p.evaluate(JS), indent=1)[:2500])
ctx.close()
eng.close()
