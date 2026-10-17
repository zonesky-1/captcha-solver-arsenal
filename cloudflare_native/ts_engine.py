#!/usr/bin/env python3
"""
ts_engine.py — ENGINE SOLVE TURNSTILE (murni buatan sendiri).

Prinsip (TANPA layanan pihak ketiga, TANPA paket npm solver, TANPA API key):
  Memakai widget Turnstile ASLI milik Cloudflare, dijalankan di browser nyata
  milik kita (Playwright/Chromium), lalu JEMBATAN TOKEN dibuat sendiri:

    1. add_init_script() menempel patch ke window.turnstile begitu api.js
       Cloudflare selesai dimuat (poll 20 ms, bukan menulis ulang file JS CF).
    2. Patch mengganti:
         turnstile.getResponse(cid)        -> token yang KAMI pegang
         turnstile.getResponsePromise(cid)  -> Promise yang resolve ke token itu
         turnstile.reset(cid) / remove(cid) -> no-op (token jangan hilang)
         options.callback di render()       -> ditahan/diteruskan sesuai policy
       Jadi token yang dihasilkan widget CF asli bisa:
         deliver="api"  -> dikembalikan ke pemanggil lewat HTTP, app TIDAK dikasih
         deliver="app"  -> diteruskan ke callback app + tombol submit di-enable
    3. Sumber kebenaran token = hidden input [name=cf-turnstile-response]
       (CF sendiri yang mengisinya) + turnstile.getResponse(). Jadi kalau window
       patch kalah balapan beberapa milidetik, token tetap terbaca.

Tidak ada token yang dibuat-buat: kalau CF tidak memberi token, hasilnya ok:false.
"""
from __future__ import annotations

import atexit
import os
import threading
import time
from typing import Any, Optional

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
# token nyata CF = 500-900+ char; kunci test CF menjawab 'XXXX.DUMMY.TOKEN.XXXX' (21 char).
# Ambang default dibuat longgar supaya uji pipa tetap jalan — set ENI_TS_TOKEN_MIN=40
# kalau mau hanya menerima token produksi.
TOKEN_MIN = int(os.getenv("ENI_TS_TOKEN_MIN", "20"))

# ------------------------------------------------------------------ JS patch
# Dijalankan di setiap dokumen, sebelum script halaman. Tidak menulis ulang JS CF.
PATCH_JS = r"""
(() => {
  if (window.__ENI_TS_PATCHED) return;
  window.__ENI_TS_PATCHED = true;
  const S = window.__ENI_TS = {
    token: '', err: null, sitekey: '', action: '', cdata: '',
    render_args: null, hold: true, forwarded: false, ready: false,
    hold_cb: [], resolvers: [], log: []
  };
  const stamp = (m) => { try { S.log.push(((performance.now()/1000).toFixed(2)) + ' ' + m); } catch(e){} };
  stamp('patch-installed');

  const install = () => {
    const ts = window.turnstile;
    if (!ts || ts.__eni_hooked) return false;
    ts.__eni_hooked = true;
    const orig = { render: ts.render, getResponse: ts.getResponse,
                   reset: ts.reset, remove: ts.remove, execute: ts.execute };

    const remember = (opts, cid) => {
      opts = opts || {};
      S.render_args = {sitekey: opts.sitekey, action: opts.action, cdata: opts.cdata,
                       size: opts.size, theme: opts.theme, appearance: opts.appearance,
                       execution: opts.execution, cid: cid};
      if (opts.sitekey) S.sitekey = opts.sitekey;
      if (opts.action) S.action = opts.action;
      if (opts.cdata) S.cdata = opts.cdata;
      stamp('render sitekey=' + opts.sitekey + ' action=' + (opts.action||'-') +
            ' size=' + (opts.size||'-'));
    };

    ts.render = function (a, b) {
      try {
        if (typeof a === 'string' || (a && a.nodeType === 1)) {
          remember(b, null);
          if (b && typeof b.callback === 'function') {
            const real = b.callback;
            b.callback = function (t) {
              S.token = t;
              stamp('callback token len=' + (t ? t.length : 0));
              if (S.hold) { S.hold_cb.push(real); stamp('cb-held'); }
              else { S.forwarded = true; try { real(t); } catch (e) {} }
              S.resolvers.splice(0).forEach(r => { try { r(t); } catch (e) {} });
            };
          }
          const cid = orig.render.call(ts, a, b);
          S.cid = cid; S.ready = true;
          return cid;
        }
        // bentuk object tunggal {sitekey,...}
        remember(a, null);
        const o = Object.assign({}, a || {});
        if (typeof o.callback === 'function') {
          const real = o.callback;
          o.callback = function (t) {
            S.token = t;
            if (S.hold) { S.hold_cb.push(real); } else { S.forwarded = true; try { real(t); } catch (e) {} }
            S.resolvers.splice(0).forEach(r => { try { r(t); } catch (e) {} });
          };
        }
        const cid = orig.render.call(ts, o);
        S.cid = cid; S.ready = true;
        return cid;
      } catch (e) {
        S.err = 'render: ' + e.message;
        return orig.render.apply(ts, arguments);
      }
    };

    ts.getResponse = function (cid) {
      if (S.token) return S.token;
      try { const v = orig.getResponse.call(ts, cid); return v || ''; } catch (e) { return ''; }
    };

    ts.getResponsePromise = function (cid) {
      if (S.token) return Promise.resolve(S.token);
      return new Promise((res) => {
        let done = false;
        S.resolvers.push((t) => { if (!done) { done = true; res(t); } });
        try {
          const p = orig.getResponsePromise;
          if (typeof p === 'function') p.call(ts, cid).then ? null : null;
        } catch (e) {}
        setTimeout(() => {
          if (!done) {
            const v = readHidden();
            if (v) { done = true; res(v); }
          }
        }, 15000);
      });
    };

    ts.reset = function (cid) { stamp('reset-blocked'); return undefined; };
    ts.remove = function (cid) { stamp('remove-blocked'); return undefined; };
    return true;
  };

  const readHidden = () => {
    const el = document.querySelector('input[name="cf-turnstile-response"]') ||
               document.querySelector('textarea[name="cf-turnstile-response"]');
    return el && el.value ? el.value : '';
  };
  window.__ENI_TS_READ = readHidden;

  // patch juga kalau app memanggil render lewat object global turnstile.render(cid, opts)
  if (!install()) {
    const iv = setInterval(() => { if (install()) clearInterval(iv); }, 15);
    setTimeout(() => clearInterval(iv), 45000);
  }
  document.addEventListener('DOMContentLoaded', install);
})();
"""

# dipanggil setelah halaman siap — memastikan widget benar-benar dirender
ENSURE_JS = r"""
(opts) => {
  const S = window.__ENI_TS || (window.__ENI_TS = {token: '', ready: false, log: []});
  const out = {triggered: false, reason: '', count: 0};
  const nodes = document.querySelectorAll('.cf-turnstile, [data-sitekey]');
  out.count = nodes.length;
  if (!window.turnstile) { out.reason = 'turnstile-global-absent'; return out; }
  if (nodes.length > 0) {
    // widget sudah dirender halaman (implicit render) — jangan dobel
    out.reason = 'page-owned-widget';
    out.sitekey = nodes[0].getAttribute('data-sitekey') || '';
    return out;
  }
  if (!opts.sitekey) { out.reason = 'no-widget-and-no-sitekey'; return out; }
  const d = document.createElement('div');
  d.id = '__eni_ts_host';
  d.className = 'cf-turnstile';
  d.setAttribute('data-sitekey', opts.sitekey);
  if (opts.action) d.setAttribute('data-action', opts.action);
  if (opts.cdata) d.setAttribute('data-cdata', opts.cdata);
  d.style.cssText = 'position:fixed;left:8px;bottom:8px;z-index:2147483646;';
  document.body.appendChild(d);
  try {
    window.turnstile.render('#__eni_ts_host', {sitekey: opts.sitekey,
      action: opts.action || undefined, cdata: opts.cdata || undefined});
    out.reason = 'injected-host-manual-render';
    out.triggered = true;
  } catch (e) { out.reason = 'render-fail: ' + e.message; }
  return out;
}
"""

READ_JS = r"""
() => {
  const S = window.__ENI_TS || {};
  let dom = '';
  const el = document.querySelector('input[name="cf-turnstile-response"]') ||
             document.querySelector('textarea[name="cf-turnstile-response"]');
  if (el) dom = el.value || '';
  let api = '';
  try { api = (window.turnstile && window.turnstile.getResponse()) || ''; } catch (e) {}
  // sitekey yang benar-benar dirender halaman (implicit render tidak lewat turnstile.render)
  let domSk = '';
  const w = document.querySelector('[data-sitekey]');
  if (w) domSk = w.getAttribute('data-sitekey') || '';
  if (!domSk) {
    document.querySelectorAll('iframe').forEach(f => {
      if (!domSk && (f.src || '').includes('challenges.cloudflare.com')) {
        const m = (f.src || '').match(/\/(0x4[A-Za-z0-9_-]+)\//);
        if (m) domSk = m[1];
      }
    });
  }
  let iframe_err = '';
  document.querySelectorAll('iframe').forEach(f => {
    if ((f.src || '').includes('challenges.cloudflare.com')) {
      try {
        const e = document.querySelector('body > div[style*="visibility: hidden"] .error')
               || document.querySelector('.cf-error-details') || null;
        if (e) iframe_err = e.innerText.slice(0, 120);
      } catch (x) {}
    }
  });
  return {dom: dom, api: api, held: S.token || '', action: S.action || '',
          sitekey: S.sitekey || domSk || '', dom_sitekey: domSk,
          widget_present: !!(document.querySelector('.cf-turnstile, [data-sitekey]')),
          ready: !!S.ready, err: S.err || null,
          forwarded: !!S.forwarded, log: (S.log || []).slice(-12),
          href: location.href, title: document.title};
}
"""

ENABLE_SUBMIT_JS = r"""
() => {
  const S = window.__ENI_TS || {};
  const tok = S.token || (window.__ENI_TS_READ ? window.__ENI_TS_READ() : '');
  const out = {delivered: !!tok, enabled: 0, checked: 0, tag: null, id: null, text: null};
  if (!tok) return out;
  // 1. teruskan ke callback asli app (kalau ditahan)
  S.hold = false;
  (S.hold_cb || []).splice(0).forEach(cb => { try { cb(tok); } catch (e) {} });
  S.forwarded = true;
  // 2. buka semua tombol submit yang masih disabled
  document.querySelectorAll('button, input[type=submit], a[role=button]').forEach(el => {
    out.checked++;
    if (!el.disabled && el.getAttribute('aria-disabled') !== 'true') return;
    const t = ((el.innerText || el.value || '') + '').trim().toLowerCase();
    if (/sign|log ?in|register|create|submit|continue|verif|send|kirim|daftar|next|masuk|lanjut/.test(t)) {
      try {
        el.disabled = false;
        if (typeof el.removeAttribute === 'function') el.removeAttribute('disabled');
        el.setAttribute('aria-disabled', 'false');
        out.enabled++;
        if (!out.tag) { out.tag = el.tagName; out.id = el.id || null; out.text = t.slice(0, 40); }
      } catch (e) {}
    }
  });
  return out;
}
"""

CLICK_SUBMIT_JS = r"""
(sel) => {
  if (sel) {
    const el = document.querySelector(sel);
    if (el) { el.click(); return {clicked: true, by: 'selector'}; }
  }
  const cand = ['button[type=submit]', 'input[type=submit]',
                'form button:not([disabled])'];
  for (const s of cand) {
    const el = document.querySelector(s);
    if (el) { el.click(); return {clicked: true, by: s}; }
  }
  return {clicked: false};
}
"""

# Cari kotak widget Turnstile (host .cf-turnstile atau iframe CF) -> koordinat untuk diklik.
WIDGET_BOX_JS = r"""
() => {
  const out = [];
  const push = (el, kind) => {
    const b = el.getBoundingClientRect();
    if (b.width > 4 && b.height > 4 && b.top > -50 && b.top < innerHeight + 50)
      out.push({kind: kind, x: b.x, y: b.y, w: Math.round(b.width), h: Math.round(b.height)});
  };
  document.querySelectorAll('.cf-turnstile, [data-sitekey], div[id*=turnstile]').forEach(e => push(e, 'host'));
  document.querySelectorAll('iframe').forEach(f => {
    if ((f.src || '').includes('challenges.cloudflare.com')) push(f, 'iframe');
  });
  return out;
}
"""


# ------------------------------------------------------------------ engine
class TurnstileEngine:
    """Satu instance = satu Playwright proses + pool browser per-thread."""

    def __init__(self, headless: Optional[bool] = None, proxy: Optional[str] = None,
                 viewport: tuple[int, int] = (1366, 900)) -> None:
        self.headless = (os.getenv("ENI_TS_HEADLESS", "1") == "1") if headless is None else headless
        self.proxy = proxy or os.getenv("ENI_TS_PROXY") or None
        self.viewport = viewport
        self._pw = None
        self._local = threading.local()
        self.stats: dict[str, Any] = {"solves": 0, "ok": 0, "fail": 0, "total_ms": 0.0,
                                      "by_deliver": {}}
        self._lock = threading.Lock()
        atexit.register(self.close)

    # per-thread proxy (biar bisa beda egress tiap worker)
    def _thread_proxy(self) -> str | None:
        return getattr(self._local, "proxy", None) or self.proxy

    def set_thread_proxy(self, proxy: Optional[str]) -> None:
        self._local.proxy = proxy or None

    # ---------------------------------------------------------- lifecycle
    def _playwright(self):
        if self._pw is None:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415
            self._pw = sync_playwright().start()
        return self._pw

    def _browser(self):
        b = getattr(self._local, "browser", None)
        if b is not None:
            try:
                if b.is_connected():
                    return b
            except Exception:  # noqa: BLE001
                pass
        pw = self._playwright()
        kw: dict[str, Any] = {
            "headless": self.headless,
            "args": ["--no-sandbox", "--disable-dev-shm-usage",
                     "--disable-blink-features=AutomationControlled",
                     "--disable-features=IsolateOrigins,site-per-process"],
        }
        tpx = self._thread_proxy()
        if tpx:
            kw["proxy"] = {"server": tpx}
        b = pw.chromium.launch(**kw)
        self._local.browser = b
        return b

    def close(self) -> None:
        b = getattr(self._local, "browser", None)
        try:
            if b:
                b.close()
        except Exception:  # noqa: BLE001
            pass
        self._local.browser = None
        try:
            if self._pw:
                self._pw.stop()
                self._pw = None
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------- solve
    def solve(self, url: str, sitekey: Optional[str] = None, *,
              action: Optional[str] = None, cdata: Optional[str] = None,
              timeout: float = 45.0, deliver: str = "api",
              click_submit: bool = False, submit_selector: Optional[str] = None,
              settle: float = 2.0, keep_open: float = 0.0,
              wait_until: str = "domcontentloaded",
              extra_headers: Optional[dict] = None,
              proxy: Optional[str] = None,
              interact: bool = True, max_clicks: int = 4) -> dict:
        """
        deliver="api" -> token ditahan, app tidak menerima (default; aman dari auto-submit)
        deliver="app" -> token diteruskan ke callback app + tombol submit di-enable
        proxy         -> egress khusus panggilan ini (thread-local; browser di-rebuild)
        interact      -> auto-klik kotak widget Turnstile (managed sering minta)
        max_clicks    -> berapa kali maksimal klik
        """
        t0 = time.perf_counter()
        out: dict[str, Any] = {"ok": False, "url": url, "sitekey": sitekey,
                               "deliver": deliver, "backend": "playwright-native-widget"}
        ctx = None
        try:
            # ganti egress -> browser wajib rebuild
            if proxy is not None and proxy != self._thread_proxy():
                self.close()
                self.set_thread_proxy(proxy)
            b = self._browser()
            ctx = b.new_context(user_agent=UA, viewport={"width": self.viewport[0],
                                                         "height": self.viewport[1]},
                                locale="en-US", timezone_id="America/New_York",
                                extra_http_headers=extra_headers or {})
            ctx.set_default_timeout(int(timeout * 1000))
            ctx.add_init_script(PATCH_JS)
            page = ctx.new_page()
            out["ua"] = UA
            page.goto(url, wait_until=wait_until, timeout=int(timeout * 1000))
            page.wait_for_timeout(int(settle * 1000))

            # pastikan widget hidup
            ensure = page.evaluate(ENSURE_JS, {"sitekey": sitekey, "action": action,
                                              "cdata": cdata})
            out["ensure"] = ensure

            # RANTAI INTERAKTIF: managed Turnstile sering minta klik di tengah proses.
            # Kita klik pusat kotak widget pakai gerakan mouse manusia, berulang —
            # ini yang bikin situs "susah" akhirnya lewat, tanpa solver pihak ketiga.
            clicks = 0
            token, seen = "", []
            deadline = time.perf_counter() + timeout
            last_click = 0.0
            while time.perf_counter() < deadline:
                st = page.evaluate(READ_JS)
                if not seen:
                    seen.append(st)
                tok = st.get("dom") or st.get("api") or st.get("held") or ""
                if tok and len(tok) >= TOKEN_MIN:
                    token = tok
                    out["state"] = st
                    break
                if st.get("err") and "error" in str(st["err"]).lower():
                    out["state"] = st
                # belum ada token -> coba klik kotak widget (kalau ada & belum sering)
                if interact and clicks < max_clicks and (time.perf_counter() - last_click) > 3.5:
                    try:
                        boxes = page.evaluate(WIDGET_BOX_JS)
                    except Exception:  # noqa: BLE001
                        boxes = []
                    box = next((b for b in boxes if b["kind"] == "iframe"), None) or \
                          (boxes[0] if boxes else None)
                    if box and box["w"] > 20:
                        cx = box["x"] + min(24, box["w"] * 0.12)
                        cy = box["y"] + box["h"] / 2
                        try:
                            page.mouse.move(max(0, cx - 70), max(0, cy - 45))
                            page.wait_for_timeout(120)
                            page.mouse.move(cx - 22, cy + 6, steps=6)
                            page.wait_for_timeout(90)
                            page.mouse.move(cx, cy, steps=4)
                            page.wait_for_timeout(110)
                            page.mouse.click(cx, cy)
                            clicks += 1
                            out["clicks"] = clicks
                            out.setdefault("click_boxes", []).append(box)
                        except Exception as exc:  # noqa: BLE001
                            out["click_err"] = f"{type(exc).__name__}: {exc}"
                        last_click = time.perf_counter()
                time.sleep(0.25)
            out["poll_ms"] = round((time.perf_counter() - t0) * 1000, 1)

            if not token:
                st = page.evaluate(READ_JS)
                out["state"] = st
                page.screenshot(path="/tmp/eni_ts_fail.png")
                out["screenshot"] = "/tmp/eni_ts_fail.png"
                out["error"] = ("token tidak keluar dari widget CF — cek IP/egress trust, "
                                "sitekey salah, atau sitekey ditolak untuk domain ini")
                out["hints"] = {
                    "sitekey_in_dom": st.get("sitekey"),
                    "widget_ready": st.get("ready"),
                    "log": st.get("log"),
                    "cf_marked": st.get("err"),
                }
                return self._finish(out, t0)

            # token didapat
            out["token"] = token
            out["len"] = len(token)
            out["token_prefix"] = token[:12]
            out["delivered_to_app"] = False

            if deliver == "app" or click_submit:
                res = page.evaluate(ENABLE_SUBMIT_JS)
                out["app_delivery"] = res
                out["delivered_to_app"] = bool(res.get("delivered"))
                if deliver == "app":
                    page.wait_for_timeout(300)

            if click_submit:
                click = page.evaluate(CLICK_SUBMIT_JS, submit_selector)
                out["clicked"] = click
                page.wait_for_timeout(2000)

            if keep_open > 0:
                page.wait_for_timeout(int(keep_open * 1000))
                out["final_url"] = page.url
                out["final_state"] = page.evaluate(READ_JS)
                try:
                    page.screenshot(path="/tmp/eni_ts_last.png")
                    out["screenshot"] = "/tmp/eni_ts_last.png"
                except Exception:  # noqa: BLE001
                    pass
            out["ok"] = True
            return self._finish(out, t0)
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"{type(exc).__name__}: {exc}"
            return self._finish(out, t0)
        finally:
            try:
                if ctx:
                    ctx.close()
            except Exception:  # noqa: BLE001
                pass

    def _finish(self, out: dict, t0: float) -> dict:
        out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        with self._lock:
            self.stats["solves"] += 1
            self.stats["total_ms"] += out["ms"]
            key = f'{out.get("deliver")}/{"ok" if out.get("ok") else "fail"}'
            self.stats["by_deliver"][key] = self.stats["by_deliver"].get(key, 0) + 1
            if out.get("ok"):
                self.stats["ok"] += 1
            else:
                self.stats["fail"] += 1
        return out

    # ------------------------------------------- prewarm buffer (in-proc)
    def prewarm(self, url: str, sitekey: str, ttl: float = 120.0) -> dict:
        """Panaskan token di latar belakang supaya permintaan berikutnya instan."""
        key = f"{url}|{sitekey}"

        def run():
            r = self.solve(url, sitekey, timeout=60)
            if r.get("ok"):
                with self._lock:
                    self.buffers.setdefault(key, []).append({"token": r["token"], "ts": time.time()})
        with self._lock:
            if not hasattr(self, "buffers"):
                self.buffers: dict[str, list[dict]] = {}
        th = threading.Thread(target=run, daemon=True)
        th.start()
        return {"queued": True, "key": key}

    def take_buffered(self, url: str, sitekey: str, ttl: float = 120.0) -> Optional[str]:
        key = f"{url}|{sitekey}"
        with self._lock:
            buf = getattr(self, "buffers", {}).get(key, [])
            fresh = [x for x in buf if time.time() - x["ts"] < ttl]
            self.buffers[key] = fresh
            if fresh:
                return fresh.pop(0)["token"]
        return None


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    u = sys.argv[1]
    sk = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("-") else None
    eng = TurnstileEngine()
    r = eng.solve(u, sk, deliver="api", keep_open=0)
    print(json.dumps({k: v for k, v in r.items() if k != "token"}, indent=2)[:3000])
    if r.get("token"):
        print("TOKEN:", r["token"][:60], f'... len={r["len"]}')
    eng.close()
