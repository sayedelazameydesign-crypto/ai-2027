import { buildReport, categories, extensions, filterExtensions, normalizeStatuses, statusLabels } from "./catalog.mjs";

const STORAGE_KEY = "ai-2027:productivity-kit:v1";
const byId = (id) => document.getElementById(id);
const formatNumber = new Intl.NumberFormat("ar-EG").format;
const filters = byId("filters");
const search = byId("search");
const cards = new Map();
let category = "all";

function storageUnavailable() {
  byId("storage-note").textContent = "التخزين غير متاح. نزّل السجل قبل إغلاق التبويب.";
}

function loadStatuses() {
  let raw;
  try {
    raw = localStorage.getItem(STORAGE_KEY);
  } catch {
    storageUnavailable();
  }
  try {
    return normalizeStatuses(raw ? JSON.parse(raw) : null);
  } catch {
    return normalizeStatuses(null);
  }
}

const statuses = loadStatuses();

function updateProgress() {
  const passed = extensions.filter(({ id }) => statuses[id] === "pass").length;
  byId("progress").max = extensions.length;
  byId("progress").value = passed;
  byId("passed-count").textContent = `${formatNumber(passed)} من ${formatNumber(extensions.length)} نجحت`;
}

function updateFilter() {
  const visible = new Set(filterExtensions(category, search.value).map(({ id }) => id));
  for (const [id, card] of cards) card.hidden = !visible.has(id);
  byId("visible-count").textContent = `نعرض ${formatNumber(visible.size)} من ${formatNumber(extensions.length)} أدوات`;
  byId("empty-results").hidden = visible.size !== 0;
  for (const button of filters.children) button.setAttribute("aria-pressed", String(button.dataset.category === category));
}

function makeFilter(value, label) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "filter";
  button.dataset.category = value;
  button.textContent = label;
  button.addEventListener("click", () => {
    category = value;
    updateFilter();
  });
  filters.append(button);
}

function renderCard(entry) {
  const card = byId("extension-template").content.firstElementChild.cloneNode(true);
  const find = (selector) => card.querySelector(selector);
  const kind = categories[entry.category];
  card.id = entry.id;
  card.classList.add(`accent-${kind.accent}`);
  const pill = find(".result-pill");
  const showStatus = (status) => {
    card.dataset.result = status;
    pill.textContent = statusLabels[status];
    pill.hidden = status === "not_run";
  };
  showStatus(statuses[entry.id]);
  find("use").setAttribute("href", `#i-${entry.icon}`);
  const heading = find("h3");
  heading.textContent = entry.name;
  heading.id = `${entry.id}-title`;
  card.setAttribute("aria-labelledby", heading.id);
  find(".category").textContent = kind.label;
  find(".description").textContent = entry.description;
  find(".license").textContent = entry.plan;
  find(".scope").textContent = entry.scope;
  const source = find(".source");
  source.href = entry.source;
  source.setAttribute("aria-label", `المصدر الرسمي: ${entry.name}`);
  find(".how").textContent = entry.how;
  find(".expected").textContent = `علامة النجاح: ${entry.expected}`;
  if (entry.testLink) {
    const target = find(".target");
    [target.href, target.textContent] = entry.testLink;
    target.hidden = false;
  }
  const select = find("select");
  select.setAttribute("aria-label", `نتيجة فحصك اليدوي: ${entry.name}`);
  for (const [value, label] of Object.entries(statusLabels)) select.add(new Option(label, value));
  select.value = statuses[entry.id];
  select.addEventListener("change", () => {
    if (!Object.hasOwn(statusLabels, select.value)) return;
    statuses[entry.id] = select.value;
    showStatus(select.value);
    updateProgress();
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(statuses));
    } catch {
      storageUnavailable();
    }
  });
  cards.set(entry.id, card);
  byId("cards").append(card);
}

function downloadReport() {
  const blob = new Blob([`${JSON.stringify(buildReport(statuses), null, 2)}\n`], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "arena-productivity-results.json";
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

makeFilter("all", "الكل");
for (const [id, item] of Object.entries(categories)) makeFilter(id, item.label);
for (const entry of extensions) renderCard(entry);
search.addEventListener("input", updateFilter);
byId("export-report").addEventListener("click", downloadReport);
updateFilter();
updateProgress();
for (const id of ["catalog-controls", "catalog-meta", "progress-panel"]) byId(id).hidden = false;
