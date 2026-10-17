/*
 * solver.js
 * Solves Cloudflare/Turnstile in a real Chrome browser, verifies HTTP 200,
 * then fires 4-5 requests using the SAME fingerprint (same browser session =
 * same TLS/HTTP2 fingerprint, same UA, same cookies, same IP) and prints
 * the full details of every request.
 *
 * Usage:
 *   node solver.js <url> [--timeout <sec>] [--requests <n>] [--headless]
 *   node solver.js https://example.com
 * TO MAKE A FLOODER YOU NEED SAME TLS / HTTP2 FINGERPRINTS , SAME UA , SAME COOKIE AND SAME IP OR THE TURNSTILE WILL APPEAR AGAIN CAUSING 403 ERROR 
 */

const { connect } = require("puppeteer-real-browser");

const argv = process.argv.slice(2);
let targetUrl = null;
let timeoutSec = 120;
let requestCount = 5;
let headless = false;
for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--timeout") timeoutSec = parseInt(argv[++i]) || 120;
    else if (a === "--requests") requestCount = parseInt(argv[++i]) || 5;
    else if (a === "--headless") headless = true;
    else if (!a.startsWith("--") && !targetUrl) targetUrl = a;
}

if (!targetUrl) {
    console.error("Usage: node solver.js <url> [--timeout <sec>] [--requests <n>] [--headless]");
    console.error("Example: node solver.js https://example.com");
    process.exit(1);
}

function sleep(ms) {
    return new Promise(r => setTimeout(r, ms));
}

async function solveAndProbe() {
    let browser = null;
    let page = null;

    try {
        console.log("[*] Launching real Chrome (same fingerprint as the final requests)...");
        const resp = await connect({
            headless: headless,
            args: [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-accelerated-2d-canvas",
                "--no-first-run",
                "--no-zygote",
                "--disable-gpu",
                "--window-size=1920,1080"
            ],
            turnstile: true,
            connectOption: {
                defaultViewport: null
            }
        });

        browser = resp.browser;
        page = resp.page;

        await page.evaluateOnNewDocument(() => {
            Object.defineProperty(navigator, "webdriver", { get: () => undefined });
        });

        const ua = await page.evaluate(() => navigator.userAgent);
        console.log("[+] Real Chrome launched");
        console.log("[+] User-Agent   : " + ua);

        console.log("[*] Solving Turnstile challenge...");
        try {
            await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 60000 });
        } catch (e) {}

        const deadline = Date.now() + timeoutSec * 1000;
        let cfClearance = null;
        let solveSec = 0;

        while (Date.now() < deadline) {
            await sleep(500);
            solveSec += 0.5;

            try {
                const cookies = await page.cookies();
                cfClearance = cookies.find(c => c.name === "cf_clearance");
                if (!cfClearance) continue;

                const status = await page.evaluate(async (u) => {
                    try {
                        const r = await fetch(u, {
                            method: "GET",
                            credentials: "include",
                            redirect: "follow",
                            cache: "no-store"
                        });
                        return r.status;
                    } catch (e) {
                        return 0;
                    }
                }, targetUrl);

                if (status === 200) {
                    console.log("[+] cf_clearance acquired in " + solveSec.toFixed(1) + "s, target returns HTTP 200");
                    break;
                }
            } catch (e) {}
        }

        if (!cfClearance) {
            console.error("[!] Failed to obtain cf_clearance within " + timeoutSec + "s");
            return false;
        }

        console.log("[+] Fingerprint : REAL Chrome (TLS = Chrome JA3, HTTP/2 = Chrome SETTINGS)");
        console.log("[+] IP          : " + (await page.evaluate(async () => {
            try {
                const r = await fetch("https://api.ipify.org?format=json", { cache: "no-store" });
                return (await r.json()).ip;
            } catch (e) { return "?"; }
        })));
        console.log("");
        console.log("[*] Sending " + requestCount + " requests with the SAME fingerprint + cookie...");
        console.log("    " + "-".repeat(70));

        let okCount = 0;
        for (let i = 1; i <= requestCount; i++) {
            const r = await page.evaluate(async (u) => {
                const t0 = performance.now();
                try {
                    const res = await fetch(u, {
                        method: "GET",
                        credentials: "include",
                        redirect: "follow",
                        cache: "no-store"
                    });
                    const text = await res.text();
                    const title = (text.match(/<title[^>]*>([^<]*)<\/title>/i) || [])[1] || "";
                    return {
                        status: res.status,
                        ms: Math.round(performance.now() - t0),
                        size: text.length,
                        type: res.headers.get("content-type") || "",
                        title: title.trim()
                    };
                } catch (e) {
                    return { status: 0, ms: Math.round(performance.now() - t0), size: 0, type: "", title: e.message };
                }
            }, targetUrl);

            if (r.status === 200) okCount++;
            console.log("    #" + i + "  status=" + r.status + "  time=" + r.ms + "ms  size=" + r.size + "B  type=" + r.type + (r.title ? "  title=\"" + r.title + "\"" : ""));
            await sleep(700 + Math.random() * 800);
        }

        console.log("    " + "-".repeat(70));
        console.log("");
        console.log("[+] Result      : " + okCount + "/" + requestCount + " requests returned HTTP 200");
        console.log("[+] cf_clearance: " + cfClearance.value);

        return true;
    } catch (e) {
        console.error("[!] Error: " + e.message);
        return false;
    } finally {
        try { if (page) await page.close(); } catch (e) {}
        try { if (browser) await browser.close(); } catch (e) {}
    }
}

(async () => {
    const ok = await solveAndProbe();
    process.exit(ok ? 0 : 1);
})();