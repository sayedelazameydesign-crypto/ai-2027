import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { categories, extensions, filterExtensions, normalizeStatuses, buildReport } from "../website/productivity-kit/catalog.mjs";

const root = new URL("../website/productivity-kit/", import.meta.url);
const read = (file) => readFileSync(new URL(file, root), "utf8");
const index = read("index.html");
const lab = read("lab.html");

const essentials = [
  "refined-github", "octotree", "ui-vision", "singlefile", "zotero-connector",
  "ublock-origin-lite", "bitwarden", "vimium",
];

test("the kit includes the eight local tools and only one optional advanced adapter", () => {
  assert.deepEqual(extensions.filter((item) => item.category !== "advanced").map((item) => item.id), essentials);
  assert.deepEqual(filterExtensions("advanced", "").map((item) => item.id), ["browserskill"]);
  assert.equal(new Set(extensions.map((item) => item.id)).size, extensions.length);
  for (const item of extensions) {
    assert.equal(new URL(item.source).protocol, "https:");
    assert.ok(Object.hasOwn(categories, item.category));
    assert.ok(index.includes(`id="i-${item.icon}"`), `missing icon for ${item.id}`);
    assert.ok(item.scope && item.how && item.expected, `missing test or scope for ${item.id}`);
  }
});

test("search and category combine, and ignore case and Arabic diacritics", () => {
  assert.deepEqual(filterExtensions("all", " REFINED ").map((item) => item.id), ["refined-github"]);
  assert.deepEqual(filterExtensions("security", "الاَمَان").map((item) => item.id), ["ublock-origin-lite", "bitwarden"]);
  assert.deepEqual(filterExtensions("research", "github"), []);
  assert.deepEqual(filterExtensions("all", ""), extensions);
});

test("no automatic passes and no arbitrary or inherited values in local state", () => {
  const defaults = normalizeStatuses(null);
  assert.equal(Object.keys(defaults).length, extensions.length);
  assert.ok(Object.values(defaults).every((status) => status === "not_run"));
  assert.deepEqual(normalizeStatuses("pass"), defaults);
  const raw = Object.create({ octotree: "pass" });
  raw.singlefile = "pass";
  raw.vimium = "toString";
  raw.bitwarden = ["pass"];
  raw.unexpected = "pass";
  assert.deepEqual(normalizeStatuses(raw), { ...defaults, singlefile: "pass" });
});

test("the JSON export has explicit self-report provenance and no arbitrary fields", () => {
  const raw = { "ui-vision": "pass", octotree: "fail", browserskill: "blocked", query: "unrelated text" };
  const report = buildReport(raw, new Date("2026-11-18T00:00:00Z"));
  assert.deepEqual(Object.keys(report).sort(), ["exported_at", "kit", "results", "schema_version", "source"]);
  assert.equal(report.source, "manual_local_self_report");
  assert.equal(report.exported_at, "2026-11-18T00:00:00.000Z");
  assert.equal(report.schema_version, 1);
  assert.deepEqual(report.results, extensions.map(({ id, name }) => ({ id, name, status: raw[id] ?? "not_run" })));
});

test("the lab uses local assets, no form submission, and a stable offline reference", () => {
  const homepage = read("../index.html");
  assert.match(homepage, /href="productivity-kit\/"/);
  for (const page of [index, lab]) {
    assert.match(page, /<html lang="ar" dir="rtl">/);
    assert.match(page, /connect-src 'none'; form-action 'none'/);
    assert.doesNotMatch(page, /<(?:script|img|iframe)\b[^>]*\bsrc="(?:https?:)?\/\//i);
  }
  assert.doesNotMatch(lab, /<form\b/i);
  assert.match(lab, /href="#reference"/);
  assert.match(lab, /id="reference"[\s\S]+ARENA-KIT-2027/);
  for (const item of extensions) {
    const target = item.testLink?.[0];
    if (target?.startsWith("lab.html")) {
      const fragment = new URL(target, root).hash.slice(1);
      if (fragment) assert.ok(lab.includes(`id="${fragment}"`), `missing target for ${item.id}`);
    } else if (target) {
      assert.equal(new URL(target).protocol, "https:");
    }
  }
  for (const file of ["sample.svg", "ads/banners/demo.svg"]) {
    assert.ok(lab.includes(`src="${file}"`));
    assert.match(read(file), /^<svg\b/);
  }
});

test("the Ui.Vision macro can only open the loopback fixture and run four fixed commands", () => {
  const { Commands } = JSON.parse(read("ui-vision-lab.json"));
  assert.deepEqual(Commands, [
    { Command: "open", Target: "http://127.0.0.1:8787/productivity-kit/lab.html", Value: "" },
    { Command: "type", Target: "id=lab-input", Value: "ARENA-2027" },
    { Command: "click", Target: "id=lab-run", Value: "" },
    { Command: "assertText", Target: "id=lab-result", Value: "LAB_OK" },
  ]);
  for (const { Target } of Commands.slice(1)) assert.ok(lab.includes(Target.replace(/^id=/, 'id="') + '"'));
  assert.match(lab, /href="ui-vision-lab\.json" download/);
  assert.match(read("lab.mjs"), /input\.value === "ARENA-2027"[\s\S]+setResult\("LAB_OK", "pass"\)/);
});
