const state = {
  token: "",
  model: null,
  overview: null,
  reorganisation: null,
  selectedPlan: null,
};

const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;");

function headers({ idempotencyKey = null } = {}) {
  const value = { Accept: "application/json" };
  if (state.token) value.Authorization = `Bearer ${state.token}`;
  if (idempotencyKey) value["Idempotency-Key"] = idempotencyKey;
  return value;
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { ...headers(options), ...(options.headers || {}) },
  });
  let body = null;
  try { body = await response.json(); } catch (_) { body = null; }
  if (!response.ok) {
    const detail = body?.detail || body || {};
    const code = detail.code || detail.upstream?.detail?.code || `HTTP_${response.status}`;
    throw new Error(code);
  }
  return body;
}

function setStatus(text, kind = "") {
  const status = $("status");
  status.textContent = text;
  status.className = `status ${kind}`.trim();
}

function card(label, value, stateName = "") {
  return `<article class="card ${esc(stateName)}"><strong>${esc(value)}</strong><span>${esc(label)}</span></article>`;
}

function activateView(name, { updateUrl = true } = {}) {
  document.querySelectorAll(".view").forEach((item) => item.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.remove("active"));
  $(`view-${name}`)?.classList.add("active");
  document.querySelector(`.nav-item[data-view="${name}"]`)?.classList.add("active");
  if (name === "overview") loadOverview();
  if (name === "corpus") loadCorpus();
  if (name === "reorganisation") loadReorganisation();
  if (updateUrl) {
    const nextUrl = name === "overview" ? "/" : `/?view=${encodeURIComponent(name)}`;
    if (`${window.location.pathname}${window.location.search}` !== nextUrl) {
      window.history.replaceState({}, "", nextUrl);
    }
  }
}

function renderModuleCards(overview) {
  const modules = overview?.modules || [];
  $("module-cards").innerHTML = modules.map((module) => card(
    module.name.replaceAll("_", " "),
    module.available ? "Available" : "Pending",
    module.available ? "good" : "warn",
  )).join("") || card("Control plane", "Unavailable", "bad");
}

async function loadOverview() {
  if (!state.token) return;
  try {
    state.overview = await request("/api/control-plane/overview");
    renderModuleCards(state.overview);
    setStatus("Connected", "ok");
    $("fingerprint").textContent = `Control contract ${state.overview.contract_version}`;
  } catch (error) {
    setStatus(`Unavailable · ${error.message}`, "error");
  }
}

function lifecycleChips(values) {
  return `<div class="chips">${Object.entries(values || {})
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([name, count]) => `<span class="chip">${esc(name)} ${count}</span>`)
    .join("")}</div>`;
}

function flattenSignal(value) {
  if (!Array.isArray(value)) return [];
  return value.map((item) => {
    if (typeof item === "string") return item;
    if (item && item.title && item.paths) return `${item.title}: ${item.paths.join(", ")}`;
    return JSON.stringify(item);
  });
}

function renderCorpus(model) {
  state.model = model;
  const summary = model.summary || {};
  $("corpus-cards").innerHTML = [
    card("Published pages", summary.active_pages ?? summary.published_pages ?? 0),
    card("Archived pages", summary.archived_pages ?? 0),
    card("Sections", summary.sections ?? 0),
    card("Review signals", Object.values(model.signals || {}).reduce((sum, value) => sum + flattenSignal(value).length, 0)),
  ].join("");
  $("sections").innerHTML = (model.sections || []).map((row) => `
    <tr>
      <td><strong>${esc(row.name)}</strong></td>
      <td class="numeric">${row.direct_pages}</td>
      <td class="numeric">${row.subtree_pages}</td>
      <td class="numeric">${Math.round((row.concentration || 0) * 100)}%</td>
      <td class="numeric">${row.archived_pages}</td>
      <td>${lifecycleChips(row.lifecycle || row.knowledge_class)}</td>
    </tr>`).join("") || `<tr><td colspan="6">No corpus model available.</td></tr>`;

  const labels = {
    duplicate_titles: "Duplicate titles",
    missing_lifecycle: "Missing classification",
    unknown_lifecycle: "Unknown classification",
    missing_index: "Directories without index",
    dated_paths: "Dated paths",
    unowned: "Unowned pages",
    unverified: "Unverified pages",
  };
  $("signals").innerHTML = Object.entries(model.signals || {}).map(([key, value]) => {
    const rows = flattenSignal(value);
    return `<article class="signal"><h3><span>${esc(labels[key] || key)}</span><span class="count">${rows.length}</span></h3>
      ${rows.length ? `<ul>${rows.slice(0, 100).map((row) => `<li>${esc(row)}</li>`).join("")}</ul>` : `<p class="subtitle">None observed.</p>`}</article>`;
  }).join("");
  $("fingerprint").textContent = `Corpus ${model.fingerprint || "unknown"} · contract ${model.contract_version || "?"}`;
}

async function loadCorpus() {
  if (!state.token) return;
  try {
    const model = await request("/api/structure");
    renderCorpus(model);
  } catch (error) {
    $("corpus-cards").innerHTML = card("Corpus API", `Pending · ${error.message}`, "warn");
    $("sections").innerHTML = `<tr><td colspan="6">The versioned structure observer endpoint is not available yet.</td></tr>`;
    $("signals").innerHTML = "";
  }
}

function renderPlanList(plans) {
  const rows = plans?.plans || plans?.items || [];
  $("reorg-plans").innerHTML = rows.length ? rows.map((plan) => `
    <button class="plan-row" data-plan-id="${esc(plan.plan_id || plan.id)}">
      <strong>${esc(plan.title || plan.plan_id)}</strong>
      <span>${esc(plan.status || "DRAFT")}</span>
    </button>`).join("") : `<p class="muted">No open plans.</p>`;
  document.querySelectorAll(".plan-row[data-plan-id]").forEach((button) => {
    button.addEventListener("click", () => selectPlan(button.dataset.planId));
  });
}

function renderTree(tree) {
  const value = tree?.tree || tree?.nodes || tree;
  if (!value) {
    $("reorg-tree").innerHTML = `<p class="muted">Tree endpoint unavailable.</p>`;
    return;
  }
  $("reorg-tree").innerHTML = `<pre>${esc(JSON.stringify(value, null, 2))}</pre>`;
}

function planStatus(plan) {
  return plan?.status || plan?.plan_state || "DRAFT";
}

function setPlanActions(plan) {
  const present = Boolean(plan);
  $("add-operation").disabled = !present;
  $("analyze-plan").disabled = !present;
  $("validate-plan").disabled = !present;
  $("submit-plan").disabled = !present;
  $("execute-plan").disabled = !present || !["APPROVED", "VALIDATED"].includes(planStatus(plan));
  $("compensate-plan").disabled = !present || !["EXECUTED", "CERTIFIED", "STABILIZING", "CLOSED"].includes(planStatus(plan));
}

function renderPlan(plan) {
  state.selectedPlan = plan;
  setPlanActions(plan);
  const impact = plan?.impact_analysis || plan?.analysis || null;
  $("reorg-impact").innerHTML = impact
    ? `<pre>${esc(JSON.stringify(impact, null, 2))}</pre>`
    : `<p class="muted">No impact analysis recorded.</p>`;
  const certification = {
    status: planStatus(plan),
    candidate_identity: plan?.candidate_identity,
    change_id: plan?.change_id,
    deployment_attempt_id: plan?.deployment_attempt_id,
    release_id: plan?.release_id,
    certification_state: plan?.certification_state,
    stabilization: plan?.stabilization,
  };
  $("reorg-certification").innerHTML = `<pre>${esc(JSON.stringify(certification, null, 2))}</pre>`;
  document.querySelectorAll("#reorg-workflow li").forEach((item) => item.classList.remove("current"));
  const current = document.querySelector(`#reorg-workflow li[data-step="${planStatus(plan)}"]`);
  current?.classList.add("current");
}

async function selectPlan(planId) {
  try {
    const plan = await request(`/api/control-plane/reorganisation/plans/${encodeURIComponent(planId)}`);
    renderPlan(plan);
  } catch (error) {
    $("reorg-impact").innerHTML = `<p class="error-text">${esc(error.message)}</p>`;
  }
}

async function loadReorganisation() {
  if (!state.token) return;
  try {
    const model = await request("/api/control-plane/reorganisation");
    state.reorganisation = model;
    renderPlanList(model.modules?.plans?.data || {});
    renderTree(model.modules?.tree?.data || null);
    const cert = model.modules?.certification;
    if (cert?.available && !state.selectedPlan) {
      $("reorg-certification").innerHTML = `<pre>${esc(JSON.stringify(cert.data, null, 2))}</pre>`;
    }
  } catch (error) {
    $("reorg-plans").innerHTML = `<p class="error-text">${esc(error.message)}</p>`;
  }
}

function randomKey(prefix) {
  return `${prefix}-${crypto.randomUUID()}`;
}

async function createPlan() {
  const title = $("plan-title").value.trim();
  const objective = $("plan-objective").value.trim();
  if (!title || !objective) {
    setStatus("Plan title and objective are required", "error");
    return;
  }
  try {
    const plan = await request("/api/control-plane/reorganisation/plans", {
      method: "POST",
      idempotencyKey: randomKey("reorg-plan"),
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, objective, scope: $("plan-scope").value }),
    });
    renderPlan(plan);
    await loadReorganisation();
    setStatus("Plan created", "ok");
  } catch (error) {
    setStatus(`Plan unavailable · ${error.message}`, "error");
  }
}

async function addOperation() {
  if (!state.selectedPlan) return;
  const body = {
    operation_type: $("operation-type").value,
    source: $("operation-source").value.trim(),
    destination: $("operation-destination").value.trim(),
  };
  try {
    const plan = await request(`/api/control-plane/reorganisation/plans/${state.selectedPlan.plan_id || state.selectedPlan.id}/operations`, {
      method: "POST",
      idempotencyKey: randomKey("reorg-op"),
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    renderPlan(plan);
  } catch (error) {
    setStatus(`Operation refused · ${error.message}`, "error");
  }
}

async function planAction(action, payload = {}) {
  if (!state.selectedPlan) return;
  const id = state.selectedPlan.plan_id || state.selectedPlan.id;
  try {
    const plan = await request(`/api/control-plane/reorganisation/plans/${id}/${action}`, {
      method: "POST",
      idempotencyKey: randomKey(`reorg-${action}`),
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    renderPlan(plan);
    setStatus(`${action} complete`, "ok");
  } catch (error) {
    setStatus(`${action} refused · ${error.message}`, "error");
  }
}

$("connect").addEventListener("click", async () => {
  state.token = $("token").value.trim();
  if (!state.token) {
    setStatus("Token required", "error");
    return;
  }
  setStatus("Connecting…");
  try {
    const discovery = await request("/api/control-plane/discovery");
    setStatus(`Connected · ${discovery.product}`, "ok");
    await loadOverview();
  } catch (error) {
    state.token = "";
    setStatus(`Connection failed · ${error.message}`, "error");
  }
});

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => activateView(button.dataset.view));
});
const requestedView = new URLSearchParams(window.location.search).get("view");
if (requestedView && document.getElementById(`view-${requestedView}`)) {
  activateView(requestedView, { updateUrl: false });
}
$("refresh-overview").addEventListener("click", loadOverview);
$("refresh-corpus").addEventListener("click", loadCorpus);
$("refresh-reorganisation").addEventListener("click", loadReorganisation);
$("create-plan").addEventListener("click", createPlan);
$("add-operation").addEventListener("click", addOperation);
$("analyze-plan").addEventListener("click", () => planAction("analyze"));
$("validate-plan").addEventListener("click", () => planAction("validate"));
$("submit-plan").addEventListener("click", () => planAction("submit"));
$("execute-plan").addEventListener("click", () => planAction("execute", { acknowledgement: "operator-approved" }));
$("compensate-plan").addEventListener("click", () => planAction("compensate", { reason: "operator-requested compensation" }));
