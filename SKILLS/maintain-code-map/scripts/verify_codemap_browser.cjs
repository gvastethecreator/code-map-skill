#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { pathToFileURL } = require("node:url");

const COMMAND_TIMEOUT_MS = 15_000;
const START_TIMEOUT_MS = 20_000;


function executableOnPath(name) {
  const extensions = process.platform === "win32"
    ? (process.env.PATHEXT || ".EXE;.CMD;.BAT").split(";")
    : [""];
  for (const directory of (process.env.PATH || "").split(path.delimiter).filter(Boolean)) {
    for (const extension of extensions) {
      const candidate = path.join(directory, `${name}${extension}`);
      if (fs.existsSync(candidate)) return candidate;
    }
  }
  return null;
}


function resolveBrowserExecutable(override) {
  const configured = [
    override,
    process.env.CODEMAP_BROWSER_EXECUTABLE,
    process.env.CHROME_PATH,
    process.env.CHROME_BIN,
  ].filter(Boolean);
  for (const value of configured) {
    const resolved = fs.existsSync(value) ? path.resolve(value) : executableOnPath(value);
    if (resolved) return resolved;
    throw new Error(`browser executable does not exist: ${value}`);
  }

  const candidates = process.platform === "win32"
    ? [
        process.env.PROGRAMFILES && path.join(process.env.PROGRAMFILES, "Google/Chrome/Application/chrome.exe"),
        process.env["PROGRAMFILES(X86)"] && path.join(process.env["PROGRAMFILES(X86)"], "Google/Chrome/Application/chrome.exe"),
        process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, "Google/Chrome/Application/chrome.exe"),
        process.env.PROGRAMFILES && path.join(process.env.PROGRAMFILES, "Microsoft/Edge/Application/msedge.exe"),
        process.env["PROGRAMFILES(X86)"] && path.join(process.env["PROGRAMFILES(X86)"], "Microsoft/Edge/Application/msedge.exe"),
      ]
    : process.platform === "darwin"
      ? [
          "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
          "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
          "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
      : [
          "/usr/bin/google-chrome",
          "/usr/bin/google-chrome-stable",
          "/usr/bin/chromium",
          "/usr/bin/chromium-browser",
          "/usr/bin/microsoft-edge",
          "/usr/bin/microsoft-edge-stable",
        ];
  const installed = candidates.filter(Boolean).find(candidate => fs.existsSync(candidate));
  if (installed) return installed;

  for (const name of ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"]) {
    const resolved = executableOnPath(name);
    if (resolved) return resolved;
  }
  throw new Error("Chrome, Edge, or Chromium was not found. Use --browser or CODEMAP_BROWSER_EXECUTABLE.");
}


class CdpPipe {
  constructor(browserExecutable) {
    this.browserExecutable = browserExecutable;
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Map();
    this.buffer = "";
    this.stderr = "";
    this.profileDir = fs.mkdtempSync(path.join(os.tmpdir(), "codemap-browser-"));
    this.process = null;
  }

  async start() {
    const args = [
      "--headless=new",
      "--remote-debugging-pipe",
      `--user-data-dir=${this.profileDir}`,
      "--allow-file-access-from-files",
      "--disable-background-networking",
      "--disable-component-update",
      "--disable-default-apps",
      "--disable-extensions",
      "--disable-sync",
      "--metrics-recording-only",
      "--mute-audio",
      "--no-default-browser-check",
      "--no-first-run",
      "about:blank",
    ];
    if (process.getuid?.() === 0) args.splice(-1, 0, "--no-sandbox");
    this.process = spawn(this.browserExecutable, args, {
      stdio: ["ignore", "ignore", "pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    this.input = this.process.stdio[3];
    this.output = this.process.stdio[4];
    this.output.setEncoding("utf8");
    this.output.on("data", chunk => this.onData(chunk));
    this.process.stderr.setEncoding("utf8");
    this.process.stderr.on("data", chunk => { this.stderr = `${this.stderr}${chunk}`.slice(-8_000); });
    this.process.on("error", (cause) => {
      const error = new Error(`browser failed to start: ${cause.message}`);
      for (const entry of this.pending.values()) {
        clearTimeout(entry.timer);
        entry.reject(error);
      }
      this.pending.clear();
    });
    this.process.on("exit", (code, signal) => {
      const detail = this.stderr.trim();
      const error = new Error(`browser exited before CDP closed: code=${code} signal=${signal}${detail ? `\n${detail}` : ""}`);
      for (const entry of this.pending.values()) {
        clearTimeout(entry.timer);
        entry.reject(error);
      }
      this.pending.clear();
    });
    await this.send("Browser.getVersion", {}, null, START_TIMEOUT_MS);
  }

  onData(chunk) {
    this.buffer += chunk;
    let boundary = this.buffer.indexOf("\0");
    while (boundary !== -1) {
      const raw = this.buffer.slice(0, boundary);
      this.buffer = this.buffer.slice(boundary + 1);
      if (raw) this.onMessage(JSON.parse(raw));
      boundary = this.buffer.indexOf("\0");
    }
  }

  onMessage(message) {
    if (message.id) {
      const entry = this.pending.get(message.id);
      if (!entry) return;
      this.pending.delete(message.id);
      clearTimeout(entry.timer);
      if (message.error) entry.reject(new Error(`${entry.method}: ${message.error.message}`));
      else entry.resolve(message.result || {});
      return;
    }
    const key = `${message.sessionId || "browser"}:${message.method}`;
    for (const listener of this.listeners.get(key) || []) listener(message.params || {});
  }

  send(method, params = {}, sessionId = null, timeoutMs = COMMAND_TIMEOUT_MS) {
    if (!this.input?.writable) return Promise.reject(new Error("browser CDP pipe is not writable"));
    const id = this.nextId++;
    const message = { id, method, params };
    if (sessionId) message.sessionId = sessionId;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`${method} timed out after ${timeoutMs}ms`));
      }, timeoutMs);
      this.pending.set(id, { method, resolve, reject, timer });
      this.input.write(`${JSON.stringify(message)}\0`);
    });
  }

  on(sessionId, method, listener) {
    const key = `${sessionId || "browser"}:${method}`;
    const entries = this.listeners.get(key) || [];
    entries.push(listener);
    this.listeners.set(key, entries);
    return () => this.listeners.set(key, entries.filter(entry => entry !== listener));
  }

  waitFor(sessionId, method, timeoutMs = COMMAND_TIMEOUT_MS) {
    return new Promise((resolve, reject) => {
      let timer;
      const off = this.on(sessionId, method, (params) => {
        clearTimeout(timer);
        off();
        resolve(params);
      });
      timer = setTimeout(() => {
        off();
        reject(new Error(`${method} timed out after ${timeoutMs}ms`));
      }, timeoutMs);
    });
  }

  async newPage(viewport) {
    const { targetId } = await this.send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await this.send("Target.attachToTarget", { targetId, flatten: true });
    const page = new CdpPage(this, targetId, sessionId);
    await page.initialize(viewport);
    return page;
  }

  async close() {
    if (!this.process) return;
    const waitForExit = (timeoutMs) => new Promise((resolve) => {
      if (this.process.exitCode !== null || this.process.signalCode !== null) return resolve(true);
      const timer = setTimeout(() => {
        this.process.removeListener("exit", onExit);
        resolve(false);
      }, timeoutMs);
      const onExit = () => {
        clearTimeout(timer);
        resolve(true);
      };
      this.process.once("exit", onExit);
    });
    try {
      await this.send("Browser.close", {}, null, 2_000);
    } catch {}
    if (!(await waitForExit(2_000))) {
      this.process.kill();
      await waitForExit(2_000);
    }
    fs.rmSync(this.profileDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
}


class CdpPage {
  constructor(client, targetId, sessionId) {
    this.client = client;
    this.targetId = targetId;
    this.sessionId = sessionId;
    this.errors = [];
    this.network = [];
  }

  send(method, params = {}) {
    return this.client.send(method, params, this.sessionId);
  }

  async initialize(viewport) {
    this.client.on(this.sessionId, "Runtime.exceptionThrown", ({ exceptionDetails }) => {
      const message = exceptionDetails?.exception?.description || exceptionDetails?.text || "unknown exception";
      this.errors.push(`pageerror: ${message}`);
    });
    this.client.on(this.sessionId, "Runtime.consoleAPICalled", ({ type, args }) => {
      if (type !== "error") return;
      const message = (args || []).map(entry => entry.value ?? entry.description ?? entry.type).join(" ");
      this.errors.push(`console: ${message}`);
    });
    this.client.on(this.sessionId, "Network.requestWillBeSent", ({ request }) => {
      if (request?.url) this.network.push(request.url);
    });
    await Promise.all([
      this.send("Page.enable"),
      this.send("Runtime.enable"),
      this.send("Network.enable"),
    ]);
    await this.send("Network.emulateNetworkConditions", {
      offline: true,
      latency: 0,
      downloadThroughput: -1,
      uploadThroughput: -1,
      connectionType: "none",
    });
    await this.send("Emulation.setDeviceMetricsOverride", {
      width: viewport.width,
      height: viewport.height,
      deviceScaleFactor: 1,
      mobile: false,
    });
  }

  async evaluateFunction(fn, ...args) {
    const expression = `(${fn.toString()})(...${JSON.stringify(args)})`;
    const result = await this.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
      userGesture: true,
    });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    }
    return result.result?.value;
  }

  async goto(url) {
    const loaded = this.client.waitFor(this.sessionId, "Page.loadEventFired");
    const result = await this.send("Page.navigate", { url });
    if (result.errorText) throw new Error(`navigation failed: ${result.errorText}`);
    await loaded;
  }

  async waitForSelector(selector, timeoutMs = COMMAND_TIMEOUT_MS) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      if (await this.count(selector)) return;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw new Error(`selector timed out after ${timeoutMs}ms: ${selector}`);
  }

  count(selector) {
    return this.evaluateFunction(value => document.querySelectorAll(value).length, selector);
  }

  click(selector, index = 0) {
    return this.evaluateFunction((value, position) => {
      const element = document.querySelectorAll(value)[position];
      if (!element) throw new Error(`element not found: ${value}`);
      if (typeof element.click === "function") element.click();
      else element.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
    }, selector, index);
  }

  async close() {
    try {
      await this.client.send("Target.closeTarget", { targetId: this.targetId });
    } catch (error) {
      if (this.client.process?.exitCode === 0) return;
      throw error;
    }
  }
}


function parseArgs(argv) {
  const args = { browserExecutable: null, files: [] };
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === "--browser") {
      if (!argv[index + 1]) throw new Error("--browser requires an executable path or command");
      args.browserExecutable = argv[index + 1];
      index += 1;
    } else if (argv[index].startsWith("--")) {
      throw new Error(`unknown option: ${argv[index]}`);
    } else {
      args.files.push(path.resolve(argv[index]));
    }
  }
  if (!args.files.length) args.files.push(path.resolve("docs/codemap/codemap.html"));
  return args;
}


async function verifyFile(browser, file) {
  const page = await browser.newPage({ width: 1280, height: 800 });
  try {
    await page.goto(pathToFileURL(file).href);
    await page.waitForSelector(".node");
    const probe = await page.evaluateFunction(() => ({
      nodes: document.querySelectorAll(".node").length,
      details: document.getElementById("details")?.textContent || "",
      resources: performance.getEntriesByType("resource").map(entry => entry.name).filter(name => !name.startsWith("file:")),
    }));
    await page.click(".node");
    const selected = await page.evaluateFunction(() => ({
      focused: document.querySelectorAll(".node.focus").length,
      details: document.getElementById("details")?.textContent || "",
    }));
    const externalNetwork = page.network.filter(url => !url.startsWith("file:") && !url.startsWith("about:") && !url.startsWith("data:"));
    const checks = {
      nodesVisible: probe.nodes > 0,
      clickSelects: selected.focused === 1 && selected.details.trim() !== probe.details.trim() && !selected.details.includes("Select a card"),
      selfContained: probe.resources.length === 0 && externalNetwork.length === 0,
      errors: page.errors.length === 0,
    };
    return {
      file,
      nodes: probe.nodes,
      errors: page.errors,
      network: externalNetwork,
      checks,
      ok: Object.values(checks).every(Boolean),
    };
  } finally {
    await page.close();
  }
}


async function main() {
  const args = parseArgs(process.argv.slice(2));
  const browserExecutable = resolveBrowserExecutable(args.browserExecutable);
  const browser = new CdpPipe(browserExecutable);
  await browser.start();
  try {
    const results = [];
    for (const file of args.files) results.push(await verifyFile(browser, file));
    const output = { browser: browserExecutable, transport: "cdp-pipe", ok: results.every(result => result.ok), results };
    process.stdout.write(`${JSON.stringify(output, null, 2)}\n`);
    process.exitCode = output.ok ? 0 : 1;
  } finally {
    await browser.close();
  }
}


if (require.main === module) {
  main().catch((error) => {
    process.stderr.write(`${error.stack || error.message}\n`);
    process.exitCode = 1;
  });
}

module.exports = {
  resolveBrowserExecutable,
  CdpPipe,
};
