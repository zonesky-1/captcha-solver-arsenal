# 🛡️ Captcha Solver Arsenal

Modular, production-grade Captcha & Turnstile solving suite featuring Cloudflare native interception, real-browser TLS fingerprint maintenance, Tencent TCaptcha, Aliyun slider inpainting, and NopeCHA automated bypass engines.

---

## 📂 Struktur Modul

```text
captcha-solver-arsenal/
├── cloudflare_native/       # Native Turnstile Engine (JS hook injection & direct DOM extraction)
│   ├── ts_engine.py         # Core Turnstile hook engine (Playwright / Camoufox)
│   ├── solve_turnstile.py   # Standalone CLI solver
│   ├── sk_finder.py         # Automated Sitekey extraction dari SPA bundle / HTML
│   ├── eni_ts_api.py        # FastAPI microservice daemon untuk multi-worker
│   ├── client.py            # SDK client untuk interaksi dengan microservice
│   └── README.md            # Dokumentasi lengkap modul Turnstile native
├── puppeteer_solver/        # Real-browser fingerprint keeper & Cloudflare clearance probe
│   └── solver.js            # puppeteer-real-browser engine (Chrome JA3 & HTTP/2 SETTINGS keeper)
├── tcaptcha_tencent/        # Tencent TCaptcha 2.0 full Python SDK & slider bypass
│   ├── tcaptcha/            # Core package: detect, decision, drag, risk, browser engine
│   └── README.md            # Dokumentasi tcaptcha package
├── nopecha_turnstile/       # NopeCHA Turnstile demo solver & verification suite
│   └── nopecha_solver.py    # Patchright + Xvfb token minting & siteverify checker
└── aliyun_skills/           # Aliyun Captcha & Slider Inpainting Reference Architecture
    └── security/            # Pedoman teknis, recovery preflight, & solver package docs
```

---

## 🚀 Fitur Unggulan

1. **Cloudflare Turnstile Native (`cloudflare_native/`):**
   - 100% murni tanpa ketergantungan API pihak ketiga atau solver berbayar.
   - JS Hook Injection pada `window.turnstile` sebelum challenge dijalankan.
   - Mendukung mode Invisible, Managed Challenge, maupun Interactive Checkbox.
   - Tersedia sebagai CLI standalone, Python SDK class, atau REST API via FastAPI.

2. **Real Chrome Fingerprint Keeper (`puppeteer_solver/solver.js`):**
   - Menghasilkan dan mempertahankan `cf_clearance` cookie menggunakan browser Chrome nyata.
   - Menjaga konsistensi TLS JA3 fingerprint, HTTP/2 settings, User-Agent, dan IP address.

3. **Tencent TCaptcha 2.0 SDK (`tcaptcha_tencent/`):**
   - Modul lengkap Python untuk otomasi slider captcha Tencent.
   - Dilengkapi modul deteksi gap gambar, simulasi drag trajectory berbasis fisika, serta analisis profil risiko.

4. **NopeCHA Solver (`nopecha_turnstile/`):**
   - Engine Patchright stealth untuk memanen token Turnstile pada demonstrasi NopeCHA.
   - Validasi token langsung via Cloudflare siteverify endpoint.

5. **Aliyun Captcha Skills (`aliyun_skills/`):**
   - Dokumentasi mendalam dan playbook analisis untuk mitigasi slider captcha Aliyun dan inpainting model.

---

## 🛠️ Instalasi & Persyaratan

### 1. Dependensi Python:
```bash
pip install playwright patchright fastapi uvicorn requests aiohttp
playwright install chromium
```

### 2. Dependensi Node.js (untuk `puppeteer_solver/`):
```bash
cd puppeteer_solver
npm install puppeteer-real-browser
```

---

## 📖 Contoh Penggunaan Cepat

### Jalankan Turnstile Native CLI:
```bash
cd cloudflare_native
python3 solve_turnstile.py https://target.com/login 0x4AAAAAAAx...
```

### Jalankan Real-Browser Clearance Solver:
```bash
cd puppeteer_solver
node solver.js https://target.com --requests 5
```

### Jalankan Tencent TCaptcha Solver:
```bash
cd tcaptcha_tencent
python3 -m tcaptcha --help
```
