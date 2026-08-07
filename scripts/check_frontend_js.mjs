#!/usr/bin/env node
/**
 * 前端 JS 语法检查：提取 webui/index.html 的 <script> 块并用 new Function 严格校验。
 *
 * 用法：
 *   node scripts/check_frontend_js.mjs          # 检查默认 index.html
 *   node scripts/check_frontend_js.mjs path     # 指定 HTML 文件
 */
import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const here = dirname(fileURLToPath(import.meta.url));
const htmlPath = process.argv[2] || join(here, "..", "webui", "index.html");

const html = readFileSync(htmlPath, "utf-8");
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];

if (scripts.length === 0) {
  console.error(`未找到 <script> 块: ${htmlPath}`);
  process.exit(1);
}

let ok = true;
scripts.forEach((m, i) => {
  try {
    new Function(m[1]);
    console.log(`[ok] script block #${i + 1} (${m[1].length} chars)`);
  } catch (e) {
    ok = false;
    console.error(`[error] script block #${i + 1}: ${e.message}`);
  }
});

if (!ok) process.exit(1);
console.log(`FRONTEND JS SYNTAX OK (${scripts.length} blocks)`);
