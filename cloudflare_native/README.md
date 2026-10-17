# Cloudflare Turnstile Solver (Native / Non-Wrapper)

Solver Cloudflare Turnstile murni tanpa wrapper pihak ketiga (tanpa npm solver luar, tanpa API key berbayar). Menggunakan engine headless browser (Playwright / Camoufox) dengan teknik **JS hook injection** pada `window.turnstile` dan DOM listener untuk menangkap token `cf-turnstile-response` secara real-time.

---

## 🚀 Fitur Utama
- **Murni Buatan Sendiri**: Tidak bergantung pada API service eksternal atau package solver berbayar.
- **Dukungan Proxy Penuh**: Mendukung HTTP & SOCKS5 (termasuk WARP / proxy berotasi).
- **Auto Sitekey Finder**: Mampu memindai sitekey dari HTML, script bundle SPA, maupun DOM secara otomatis (`sk_finder.py`).
- **Interactive Solver**: Menangani Turnstile tipe invisible, interactive (klik checkbox), maupun managed challenge.
- **Multiple Mode**:
  1. **CLI / Standalone**: Jalankan sekali untuk mendapatkan token ke stdout atau file.
  2. **Python SDK / Direct Engine**: Import class `TurnstileEngine` langsung ke dalam bot / script farming.
  3. **FastAPI Microservice**: Jalankan sebagai REST API background service untuk dipakai multi-worker / multi-thread.

---

## 📦 Instalasi & Kebutuhan

### 1. Kebutuhan Sistem
- Python 3.9+
- Linux / macOS / Windows

### 2. Install Dependencies
```bash
pip install -r requirements.txt
playwright install chromium
```
*(Opsional jika menggunakan Camoufox untuk evasiveness lebih tinggi: `camoufox fetch`)*

---

## 🛠️ Cara Penggunaan

### Mode 1: Standalone CLI (`solve_turnstile.py`)
Mendapatkan token langsung dari command line.

```bash
# Penggunaan dasar
python3 solve_turnstile.py https://target.com/login 0x4AAAAAAAx...

# Tanpa sitekey (auto-detect dari DOM)
python3 solve_turnstile.py https://target.com/login

# Dengan Proxy
export TURNSTILE_PROXY="socks5://user:pass@192.168.1.100:1080"
python3 solve_turnstile.py https://target.com/login 0x4AAAAAAAx...
```

Token akan di-print ke stdout dan otomatis disimpan ke `/tmp/turnstile_token.txt` (atau sesuai env `TURNSTILE_OUT`).

---

### Mode 2: Import Langsung ke Python (`ts_engine.py`)
Cocok untuk bot / scraper async atau multithreaded.

```python
from ts_engine import TurnstileEngine

# Inisialisasi engine dengan proxy opsional
engine = TurnstileEngine(
    headless=True,
    proxy="socks5://user:pass@ip:port"  # atau None untuk IP lokal
)

# Solve turnstile
result = engine.solve(
    url="https://target.com/login",
    sitekey="0x4AAAAAAAx...",
    action=None,
    timeout=45.0,
    click_submit=False  # Set True jika ingin auto-klik tombol submit di halaman
)

if result.get("ok"):
    token = result["value"]
    print(f"Berhasil! Token ({len(token)} chars): {token}")
else:
    print("Gagal:", result.get("error"))
```

---

### Mode 3: Jalankan Sebagai Background REST API (`eni_ts_api.py`)

#### 1. Jalankan Service API:
```bash
# Default bind di 127.0.0.1:8123
python3 eni_ts_api.py
```

Atau custom host/port:
```bash
ENI_TS_HOST=127.0.0.1 ENI_TS_PORT=8123 python3 eni_ts_api.py
```

#### 2. Request via cURL:
```bash
# Scan Sitekey dari URL target
curl -s "http://127.0.0.1:8123/sitekey?url=https://target.com/login"

# Solve Turnstile dan ambil token
curl -s "http://127.0.0.1:8123/solve?url=https://target.com/login&sitekey=0x4AAAAAAAx..."
```

#### 3. Request via Python Client (`client.py`):
```python
from client import TurnstileClient

client = TurnstileClient(base_url="http://127.0.0.1:8123")

# Auto-scan sitekey
info = client.get_sitekey("https://target.com/login")
sitekey = info["sitekey"]

# Dapatkan token
res = client.solve("https://target.com/login", sitekey)
if res.get("ok"):
    print("Token:", res["value"])
```

---

## 🔍 Tool Bantu: Sitekey Finder (`get_sitekey.py` / `sk_finder.py`)
Mencari sitekey di halaman target (termasuk SPA bundle JS yang tersembunyi):

```bash
python3 get_sitekey.py https://target.com/login
```

---

## ⚙️ Konfigurasi Proxy
Format proxy yang didukung:
- `socks5://user:password@ip:port`
- `socks5://ip:port`
- `http://user:password@ip:port`
- `http://ip:port`

Variabel Environment yang didukung:
- `TURNSTILE_PROXY`: Proxy untuk `solve_turnstile.py`
- `ENI_TS_PROXY`: Proxy default untuk `ts_engine.py` & `eni_ts_api.py`

---

## 📌 Catatan Penting & Troubleshooting
1. **IP Datacenter Trust**: Cloudflare Turnstile menilai reputasi IP. Jika IP server/datacenter memiliki trust score rendah, Cloudflare mungkin meminta challenge interaktif atau loop. Gunakan proxy residensial / WARP bersih via `ENI_TS_PROXY`.
2. **Headless Mode**: Default engine berjalan dalam mode headless. Jika perlu debugging visual di desktop, set `headless=False` pada `TurnstileEngine`.
