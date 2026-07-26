const state = { model: null };

const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;");

function card(label, value) {
  return `<div class="card"><strong>${esc(value)}</strong><span>${esc(label)}</span></div>`;
}

function lifecycleChips(values) {
  return `<div class="chips">${Object.entries(values || {})
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([name, count]) => `<span class="chip">${esc(name)} ${count}</span>`)
    .join("")}</div>`;
}

function renderSections(filter = "") {
  const query = filter.trim().toLowerCase();
  const rows = (state.model?.sections || []).filter((row) =>
    !query || row.name.toLowerCase().includes(query)
  );
  $("sections").innerHTML = rows.map((row) => `
    <tr>
      <td><strong>${esc(row.name)}</strong></td>
      <td class="numeric">${row.direct_pages}</td>
      <td class="numeric">${row.subtree_pages}</td>
      <td class="numeric">${Math.round(row.concentration * 100)}%</td>
      <td class="numeric">${row.archived_pages}</td>
      <td>${lifecycleChips(row.lifecycle)}</td>
    </tr>`).join("") || `<tr><td colspan="6">No matching sections.</td></tr>`;
}

function flattenSignal(value) {
  if (!Array.isArray(value)) return [];
  return value.map((item) => {
    if (typeof item === "string") return item;
    if (item && item.title && item.paths) return `${item.title}: ${item.paths.join(", ")}`;
    return JSON.stringify(item);
  });
}

function renderSignals(signals) {
  const labels = {
    duplicate_titles: "Duplicate titles",
    missing_lifecycle: "Missing lifecycle",
    unknown_lifecycle: "Unknown lifecycle",
    missing_index: "Directories without index",
    dated_paths: "Dated paths",
  };
  $("signals").innerHTML = Object.entries(signals || {}).map(([key, value]) => {
    const rows = flattenSignal(value);
    return `<article class="signal">
      <h3><span>${esc(labels[key] || key)}</span><span class="count">${rows.length}</span></h3>
      ${rows.length ? `<ul>${rows.slice(0, 100).map((row) => `<li>${esc(row)}</li>`).join("")}</ul>` : `<p class="subtitle">None observed.</p>`}
    </article>`;
  }).join("");
}

function render(model) {
  state.model = model;
  const summary = model.summary || {};
  $("cards").innerHTML = [
    card("Active pages", summary.active_pages ?? 0),
    card("Archived pages", summary.archived_pages ?? 0),
    card("Sections", summary.sections ?? 0),
    card("Directories", summary.directories ?? 0),
  ].join("");
  renderSections($("section-filter").value);
  renderSignals(model.signals);
  $("fingerprint").textContent = `Corpus ${model.fingerprint || "unknown"} · contract ${model.contract_version || "?"}`;
}

async function load() {
  const status = $("status");
  status.textContent = "Loading…";
  status.className = "status";
  try {
    const response = await fetch("/api/structure", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const model = await response.json();
    render(model);
    status.textContent = "Current";
    status.className = "status ok";
  } catch (error) {
    status.textContent = `Unavailable · ${error.message}`;
    status.className = "status error";
  }
}

$("refresh").addEventListener("click", load);
$("section-filter").addEventListener("input", (event) => renderSections(event.target.value));
load();
