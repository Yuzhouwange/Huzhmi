/**
 * 前端冒烟测试（Playwright + 本机 Edge，不下载浏览器）
 *
 *   node tests/ui/smoke.mjs
 *
 * 会自己起一个临时端口的服务，跑完关掉。
 */
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..", "..");
const port = Number(process.env.UI_PORT || 8912);
const base = `http://127.0.0.1:${port}`;
const python = process.env.PYTHON ||
  path.join(root, ".build-venv", "Scripts", "python.exe");

if (!existsSync(python)) {
  console.error(`找不到 Python：${python}`);
  process.exit(2);
}

const server = spawn(
  python,
  ["run.py", "--no-browser", "--no-window", "--port", String(port)],
  { cwd: root, stdio: ["ignore", "pipe", "pipe"], windowsHide: true },
);
let serverLog = "";
server.stdout.on("data", (chunk) => { serverLog += chunk; });
server.stderr.on("data", (chunk) => { serverLog += chunk; });

async function waitHealth() {
  for (let i = 0; i < 60; i += 1) {
    try {
      const response = await fetch(`${base}/api/health`);
      if (response.ok) return;
    } catch {
      // 还没起来
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`服务没起来：\n${serverLog}`);
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

let browser;
let failed = false;
try {
  await waitHealth();
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  page.on("console", (message) => {
    if (message.type() === "error" &&
        !message.text().includes("Failed to load resource")) {
      errors.push(message.text());
    }
  });

  await page.goto(base, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".nav-item", { timeout: 15000 });
  const navCount = await page.locator(".nav-item").count();
  assert(navCount >= 8, `导航项数量不对：${navCount}`);

  const views = ["douyin", "discover", "bilibili", "archive",
                 "audio", "convert", "musicdl", "music"];
  for (const view of views) {
    await page.click(`[data-view="${view}"]`);
    await page.waitForTimeout(450);
    const active = await page.locator(`[data-view="${view}"]`).getAttribute("class");
    assert((active || "").includes("active"), `${view} 没切过去`);
  }

  await page.click('[data-view="discover"]');
  await page.waitForTimeout(1800);
  const hotRows = await page.locator("#hot-list .hot-row").count();
  assert(hotRows > 10, `热榜行数太少：${hotRows}`);

  await page.click('[data-view="convert"]');
  const formats = await page.locator("#cv-format option").count();
  assert(formats >= 9, `转换格式数量不对：${formats}`);
  const quality = await page.locator("#cv-quality option").count();
  assert(quality === 3, `音质档位数量不对：${quality}`);

  await page.click('[data-view="archive"]');
  await page.waitForTimeout(700);
  assert((await page.locator("#archive-hint").innerText()).includes("共"),
    "作品档案没有显示总数");

  await page.click("#btn-settings");
  await page.waitForTimeout(300);
  if (await page.locator("#cfg-rules-url").count()) {
    assert(await page.locator("#cfg-rules-url").isVisible(),
      "规则热更新输入框不可见");
  }

  assert(errors.length === 0, `页面报错：\n${errors.join("\n")}`);
  console.log(`UI OK：${navCount} 个分栏，热榜 ${hotRows} 条，${formats} 种格式`);
} catch (error) {
  failed = true;
  console.error(`UI FAIL：${error.message}`);
  if (serverLog) console.error(serverLog.slice(-2000));
} finally {
  if (browser) await browser.close().catch(() => {});
  server.kill();
  await new Promise((resolve) => setTimeout(resolve, 500));
}
process.exit(failed ? 1 : 0);
