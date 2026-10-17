#!/usr/bin/env python3
"""
client.py — Python Client / Helper untuk Turnstile Solver.
Bisa dipakai langsung via import Engine lokal atau via HTTP REST API.
"""

from __future__ import annotations

import json
import time
from typing import Optional
import urllib.request
import urllib.parse


class TurnstileClient:
    """Client ringan untuk memanggil Turnstile API Service."""

    def __init__(self, base_url: str = "http://127.0.0.1:8123"):
        self.base_url = base_url.rstrip("/")

    def get_sitekey(self, target_url: str, render: bool = True, validate: bool = True) -> dict:
        """Cari sitekey dari URL target."""
        params = urllib.parse.urlencode({
            "url": target_url,
            "render": "true" if render else "false",
            "validate": "true" if validate else "false",
        })
        url = f"{self.base_url}/sitekey?{params}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def solve(
        self,
        target_url: str,
        sitekey: str,
        action: Optional[str] = None,
        timeout: float = 45.0,
        click_submit: bool = False,
        submit_selector: Optional[str] = None,
    ) -> dict:
        """Solve Turnstile dan dapatkan token."""
        payload = {
            "url": target_url,
            "sitekey": sitekey,
            "timeout": str(timeout),
            "click_submit": "true" if click_submit else "false",
        }
        if action:
            payload["action"] = action
        if submit_selector:
            payload["submit_selector"] = submit_selector

        params = urllib.parse.urlencode(payload)
        url = f"{self.base_url}/solve?{params}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout + 10) as resp:
            return json.loads(resp.read().decode("utf-8"))


if __name__ == "__main__":
    # Contoh pemakaian langsung
    import sys
    if len(sys.argv) < 3:
        print("Penggunaan: python3 client.py <target_url> <sitekey>")
        print("Contoh: python3 client.py https://example.com 0x4AAAAAAAx...")
        sys.exit(1)

    client = TurnstileClient()
    target_url = sys.argv[1]
    sitekey = sys.argv[2]

    print(f"[*] Solving Turnstile untuk {target_url} ...")
    res = client.solve(target_url, sitekey)
    print("[+] Hasil:", json.dumps(res, indent=2))
