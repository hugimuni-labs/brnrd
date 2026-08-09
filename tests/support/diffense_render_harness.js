"use strict";
// Executes the *actual* inline app script from a rendered diffense HTML
// page (produced by the real `brr.diffense.render.render()`, not a copy)
// against a minimal DOM stub, then dumps the resulting tree so a pytest
// caller can assert on it. This is the real client-side code path a
// browser runs — the only thing faked is the DOM, because there is no
// browser here.
//
// Usage: node diffense_render_harness.js <path-to-rendered.html> [initial-hash]
//
// Prints one JSON object to stdout: { html: "<serialised app subtree>" }

const fs = require("fs");

const file = process.argv[2];
const initialHash = process.argv[3] || "";
const source = fs.readFileSync(file, "utf-8");

const scriptMatch = source.match(/<script>\s*"use strict";[\s\S]*?<\/script>/);
if (!scriptMatch) throw new Error("app script (the \"use strict\" block) not found in " + file);
const appScript = scriptMatch[0].replace(/^<script>/, "").replace(/<\/script>$/, "");

const packTagMatch = source.match(/<script id="diffense-pack" type="application\/json">([\s\S]*?)<\/script>/);
if (!packTagMatch) throw new Error("#diffense-pack script tag not found in " + file);
const packText = packTagMatch[1];

// ---- minimal DOM -----------------------------------------------------
let idCounter = 0;

class TextNode {
  constructor(text) {
    this.nodeType = 3;
    this.text = text;
  }
}

class FakeElement {
  constructor(tag) {
    this.nodeType = 1;
    this.tagName = tag;
    this.children = [];
    this.attrs = {};
    this.className = "";
    this.listeners = {};
    this._rawHtml = null; // set only when `el()` assigns via the `html` prop
    this._id = "n" + idCounter++;
  }
  appendChild(c) {
    this.children.push(c);
    return c;
  }
  setAttribute(k, v) {
    this.attrs[k] = v;
  }
  addEventListener(ev, fn) {
    this.listeners[ev] = fn;
  }
  set innerHTML(v) {
    this._rawHtml = v;
    this.children = [];
  }
  get innerHTML() {
    return this._rawHtml == null ? "" : this._rawHtml;
  }
  set textContent(v) {
    this._rawHtml = null;
    this.children = v ? [new TextNode(String(v))] : [];
  }
  get textContent() {
    if (this._rawHtml != null) return this._rawHtml;
    return this.children.map((c) => (c.nodeType === 3 ? c.text : c.textContent || "")).join("");
  }
}

function escapeForDump(s) {
  // A real DOM text node is never markup — a browser's own `.innerHTML`
  // getter escapes `&`/`<`/`>` back out of text-node content when
  // serialising. Mirror that here, or a legitimate `T(...)`-inserted text
  // node containing `<`/`>` would misreport as an unescaped-HTML finding
  // against this harness's own serialisation, not against the app.
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function serialize(node) {
  if (node == null) return "";
  if (node.nodeType === 3) return escapeForDump(node.text);
  const openAttrs = Object.keys(node.attrs)
    .map((k) => ` ${k}="${node.attrs[k]}"`)
    .join("");
  const cls = node.className ? ` class="${node.className}"` : "";
  const inner = node._rawHtml != null ? node._rawHtml : node.children.map(serialize).join("");
  return `<${node.tagName}${cls}${openAttrs}>${inner}</${node.tagName}>`;
}

const appRoot = new FakeElement("div");
const packNode = { textContent: packText };

const documentStub = {
  getElementById(id) {
    if (id === "app") return appRoot;
    if (id === "diffense-pack") return packNode;
    return null;
  },
  createElement(tag) {
    return new FakeElement(tag);
  },
  createTextNode(s) {
    return new TextNode(s);
  },
  addEventListener() {},
  body: { },
};

const locationStub = { search: "", hash: initialHash, href: "http://localhost/r" };
const windowStub = { addEventListener() {}, scrollTo() {} };

const sandbox = {
  document: documentStub,
  location: locationStub,
  window: windowStub,
  console,
  fetch: () => Promise.reject(new Error("fetch should not be called: pack is inlined")),
  URLSearchParams,
  URL,
  decodeURIComponent,
  Promise,
  JSON,
};

const vm = require("vm");
vm.createContext(sandbox);
vm.runInContext(appScript, sandbox, { filename: file });

// `loadPack().then(render)` inside the script is a microtask chain (the
// pack is inlined, so `Promise.resolve(JSON.parse(raw))` — no real I/O to
// wait on); a couple of event-loop turns is enough for it to settle.
setTimeout(() => {
  process.stdout.write(JSON.stringify({ html: serialize(appRoot) }));
}, 50);
