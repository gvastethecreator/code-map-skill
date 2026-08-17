#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { resolveBrowserExecutable, CdpPipe } = require("../SKILLS/maintain-code-map/scripts/verify_codemap_browser.cjs");

const ROOT = path.resolve(__dirname, "..");
const CATALOG = path.join(ROOT, "examples", "catalog.json");
const OUT_DIR = path.join(ROOT, "docs", "examples");
const VIEWPORT = { width: 1440, height: 900 };


async function prepareGalleryView(page) {
  await page.waitForSelector(".node");
  await page.evaluateFunction(() => new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  }));
  await page.evaluateFunction(() => {
    const hide = (selector) => {
      document.querySelectorAll(selector).forEach((node) => {
        node.style.setProperty("display", "none", "important");
      });
    };
    hide(".skip, .left, .right, .toolbar, .card-legend, .status-line");
    const shell = document.getElementById("app-shell");
    if (shell) {
      shell.style.setProperty("grid-template-columns", "1fr", "important");
      shell.style.setProperty("grid-template-rows", "1fr", "important");
    }
    const map = document.querySelector(".map-shell");
    if (map) {
      map.style.setProperty("min-height", "100vh", "important");
    }
    document.getElementById("fit")?.click();
  });
  await page.evaluateFunction(() => new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  }));
  await new Promise((resolve) => setTimeout(resolve, 250));
}


async function captureMap(page, outputPath) {
  const box = await page.evaluateFunction(() => {
    const map = document.querySelector(".map-shell");
    if (!map) throw new Error("map shell missing");
    const rect = map.getBoundingClientRect();
    return {
      x: Math.max(0, rect.x),
      y: Math.max(0, rect.y),
      width: Math.ceil(rect.width),
      height: Math.ceil(rect.height),
      nodes: document.querySelectorAll(".node").length,
      title: document.getElementById("repo-name")?.textContent || "",
    };
  });
  if (box.nodes < 4) throw new Error(`too few nodes in ${outputPath}: ${box.nodes}`);
  const screenshot = await page.send("Page.captureScreenshot", {
    format: "png",
    fromSurface: true,
    clip: {
      x: box.x,
      y: box.y,
      width: box.width,
      height: box.height,
      scale: 1,
    },
  });
  fs.writeFileSync(outputPath, Buffer.from(screenshot.data, "base64"));
  return box;
}


async function main() {
  const catalog = JSON.parse(fs.readFileSync(CATALOG, "utf8"));
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const browserExecutable = resolveBrowserExecutable(process.env.CODEMAP_BROWSER_EXECUTABLE);
  const browser = new CdpPipe(browserExecutable);
  await browser.start();
  const page = await browser.newPage({ width: VIEWPORT.width, height: VIEWPORT.height });
  await page.send("Emulation.setDeviceMetricsOverride", {
    width: VIEWPORT.width,
    height: VIEWPORT.height,
    deviceScaleFactor: 2,
    mobile: false,
  });
  try {
    for (const example of catalog) {
      const htmlPath = path.join(ROOT, example.html);
      const outputPath = path.join(OUT_DIR, `${example.slug}.png`);
      await page.goto(pathToFileURL(htmlPath).href);
      await prepareGalleryView(page);
      const box = await captureMap(page, outputPath);
      process.stdout.write(`${example.slug}: ${box.nodes} nodes, ${box.width}x${box.height} -> ${path.relative(ROOT, outputPath)}\n`);
    }
  } finally {
    await page.close();
    await browser.close();
  }
}


main().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exitCode = 1;
});
