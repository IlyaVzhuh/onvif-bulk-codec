// Switch main and sub stream codec through the camera's TpseeCAM web UI (port 8080).
// ONVIF SetVideoEncoderConfiguration is accepted but ignored by this firmware, and the web API
// is ECDH + WASM encrypted, so drive the real UI.
// Usage: node web_codec.mjs [--dry-run] [--codec H265] IP [IP...]
import { chromium } from "playwright-core";
import { readFileSync, mkdirSync } from "node:fs";
import os from "node:os";

const args = process.argv.slice(2);
const dryRun = args.includes("--dry-run");
const codecIdx = args.indexOf("--codec");
const codec = codecIdx >= 0 ? args[codecIdx + 1] : "H265";
const ips = args.filter((a, i) => !a.startsWith("--") && (codecIdx < 0 || i !== codecIdx + 1));
if (!ips.length) throw new Error("no camera IPs given");
const passwords = readFileSync(`${os.homedir()}/.config/onvif-pass`, "utf8").split("\n").map((l) => l.trim()).filter(Boolean);
mkdirSync("shots", { recursive: true });

const FORM = [
  "MainEncodeFormat", "MainResolution", "MainBitRateControl", "MainIFrameInterval", "MainBitRate", "MainFrameRate",
  "SubEncodeFormat", "SubResolution", "SubBitRateControl", "SubIFrameInterval", "SubBitRate", "SubFrameRate",
];
const readForm = (page) =>
  page.evaluate((names) => {
    const out = {};
    for (const n of names) out[n] = document.querySelector(`#VideoEncodingSettings [name='${n}']`)?.value ?? null;
    out.MainOptions = [...document.querySelectorAll("#VideoEncodingSettings select[name='MainEncodeFormat'] option")].map((o) => o.value);
    out.SubOptions = [...document.querySelectorAll("#VideoEncodingSettings select[name='SubEncodeFormat'] option")].map((o) => o.value);
    return out;
  }, FORM);

async function openEncoding(page) {
  await page.evaluate(async () => {
    replaceIndexContent("video-encoding-settings");
    await VideoEncodingSettings_GetParam();
  });
  await page.waitForFunction(
    () => document.querySelectorAll("#VideoEncodingSettings select[name='MainEncodeFormat'] option").length > 0,
    null, { timeout: 20_000 });
  return readForm(page);
}

async function login(page, ip) {
  for (const pw of passwords) {
    await page.goto(`http://${ip}:8080/`, { waitUntil: "load", timeout: 60_000 });
    await page.locator("#username").waitFor({ timeout: 20_000 });
    // Login does an ECDH exchange in libtps.wasm; the camera serves it with the wrong MIME type,
    // so it loads through a slow fallback. Clicking before it's ready silently does nothing.
    await page.waitForFunction(() => typeof Module !== "undefined" && Module.calledRun, null, { timeout: 60_000 });
    await page.fill("#username", process.env.ONVIF_USER || "admin");
    await page.fill("#password", pw);
    await page.click("#loginSubmit");
    const ok = await page.waitForFunction(() => window.g_IsLogin === true && window.g_SecretKey != null, null, { timeout: 45_000 })
      .then(() => true, () => false);
    if (ok) return;
  }
  throw new Error("web login failed");
}

async function one(browser, ip) {
  const page = await browser.newPage();
  try {
    await login(page, ip);
    const before = await openEncoding(page);
    if (!before.MainOptions.includes(codec) || !before.SubOptions.includes(codec))
      throw new Error(`${codec} not offered: main=${before.MainOptions} sub=${before.SubOptions}`);
    if (before.MainEncodeFormat === codec && before.SubEncodeFormat === codec) return { ip, status: "already", before };
    if (dryRun) return { ip, status: "dry-run", before };

    // The settings pane stays hidden (we load the form without navigating), so set the selects
    // directly; SetParam reads them from the DOM, and the codec onchange handler is a no-op.
    await page.evaluate((codec) => {
      for (const n of ["MainEncodeFormat", "SubEncodeFormat"]) {
        const sel = document.querySelector(`#VideoEncodingSettings select[name='${n}']`);
        sel.value = codec;
        sel.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }, codec);
    const staged = await readForm(page);
    for (const n of FORM.filter((n) => !n.endsWith("EncodeFormat")))
      if (staged[n] !== before[n]) throw new Error(`form changed ${n}: ${before[n]} -> ${staged[n]}; not saving`);
    await page.evaluate(() => VideoEncodingSettings_SetParam());
    // The camera often starts restarting before the toast shows; the re-read below is the real check.
    await page.locator(".alert-success").first().waitFor({ timeout: 20_000 }).catch(() => {});

    // The camera restarts its services after saving; log in again to read back what stuck.
    let after;
    for (let t = 0; !after && t < 12; t++) {
      await page.waitForTimeout(10_000);
      after = await login(page, ip).then(() => openEncoding(page), () => undefined);
    }
    if (!after) throw new Error("saved, but couldn't log back in to verify");
    const diff = FORM.filter((n) => after[n] !== (n.endsWith("EncodeFormat") ? codec : before[n]));
    if (diff.length) throw new Error(`after save, unexpected: ${diff.map((n) => `${n}=${after[n]}`).join(", ")}`);
    return { ip, status: "changed", before, after };
  } catch (e) {
    await page.screenshot({ path: `shots/${ip}.png` }).catch(() => {});
    return { ip, status: "FAILED", error: String(e.message).split("\n")[0] };
  } finally {
    await page.close();
  }
}

const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || "/usr/lib/chromium/chromium", headless: true });
let failed = 0;
const queue = [...ips];
const worker = async () => {
  for (let ip; (ip = queue.shift()); ) {
    const r = await one(browser, ip);
    if (r.status === "FAILED") failed++;
    console.log(JSON.stringify(r));
  }
};
await Promise.all(Array.from({ length: Math.min(4, ips.length) }, worker));
await browser.close();
process.exit(failed ? 1 : 0);
