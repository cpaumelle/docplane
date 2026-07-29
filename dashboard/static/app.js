const $ = (id) => document.getElementById(id);
const VIEWS = new Set(["overview", "authoring", "reorganisation", "history"]);
let token = sessionStorage.getItem("docplane-token") || "";
let selectedPlan = null;

function headers(extra = {}) {
  return token ? { Authorization: `Bearer ${token}`, ...extra } : { ...extra };
}
function key(prefix) { return `${prefix}-${crypto.randomUUID()}`; }
function esc(value) { const node = document.createElement("span"); node.textContent = String(value ?? ""); return node.innerHTML; }
async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: headers(options.headers || {}) });
  const payload = await response.json().catch(() => ({ raw: response.statusText }));
  if (!response.ok) throw new Error(payload.detail?.upstream?.message || payload.detail?.code || payload.detail || payload.raw || `HTTP ${response.status}`);
  return payload;
}

function validView(name) { return VIEWS.has(name) ? name : "overview"; }
function viewFromHash() { return validView((location.hash || "").replace(/^#/, "")); }
function activate(name) {
  const view = validView(name);
  document.querySelectorAll(".view").forEach((item) => item.classList.remove("active"));
  document.querySelectorAll(".nav").forEach((item) => item.classList.remove("active"));
  $(`view-${view}`)?.classList.add("active");
  document.querySelector(`.nav[data-view="${view}"]`)?.classList.add("active");
  if (view === "overview") loadOverview();
  if (view === "reorganisation") loadReorganisation();
  if (view === "history") loadHistory();
}
function navigate(name) {
  const view = validView(name);
  if (location.hash.replace(/^#/, "") === view) activate(view);
  else location.hash = view;
}
function initialiseNavigation() {
  const params = new URLSearchParams(location.search);
  const hash = (location.hash || "").replace(/^#/, "");
  const requested = validView(hash || params.get("view") || "overview");
  if (hash !== requested) {
    const url = new URL(location.href);
    url.hash = requested;
    // Initial canonicalisation only: preserve ?view=...&edit=... for authoring.js.
    history.replaceState({ view: requested }, "", url);
  }
  activate(requested);
}

async function loadProductIdentity() {
  try {
    const discovery = await api("/api/control-plane/discovery");
    const name = String(discovery.site_name || discovery.product || "DocPlane").trim() || "DocPlane";
    document.querySelectorAll("[data-product-name]").forEach((node) => { node.textContent = name; });
    document.querySelectorAll("[data-product-aria]").forEach((node) => {
      node.setAttribute("aria-label", `${name} ${node.dataset.productAria}`);
    });
    document.title = `${name} Dashboard`;
  } catch (_error) {
    // The dashboard remains usable with the neutral fallback if discovery is unavailable.
  }
}

async function connect() {
  token = $("token").value.trim();
  if (!token) return;
  const capability = await api("/api/control-plane/capabilities");
  sessionStorage.setItem("docplane-token", token);
  $("identity").textContent = `${capability.principal.display_name} · ${capability.principal.role}`;
  $("retry-publication").disabled = false;
  document.dispatchEvent(new CustomEvent("docplane:connected"));
  await loadOverview();
}

async function loadOverview() {
  if (!token) return;
  try {
    const overview = await api("/api/control-plane/overview");
    const structure = overview.modules.structure?.data || {};
    const certification = overview.modules.certification?.data || {};
    const changes = overview.modules.changes?.data?.changes || [];
    const work = overview.modules.work?.data || {};
    $("overview-cards").innerHTML = [
      ["Active pages", structure.summary?.active_pages ?? 0],
      ["Archived pages", structure.summary?.archived_pages ?? 0],
      ["Open changes", changes.filter((item) => !["PUBLISHED", "ABANDONED"].includes(item.status)).length],
      ["Active initiatives", Object.entries(work.by_state || {}).filter(([state]) => !["COMPLETE", "ABANDONED"].includes(state)).reduce((total, [,count]) => total + count, 0)],
    ].map(([label,value]) => `<article class="card"><strong>${esc(value)}</strong><span>${esc(label)}</span></article>`).join("");
    $("certification").textContent = JSON.stringify(certification, null, 2);
    $("recent-changes").innerHTML = changes.length ? changes.slice(0, 8).map((item) => `<button type="button"><strong>${esc(item.title)}</strong><span>${esc(item.status)}</span></button>`).join("") : `<p class="muted">No changes yet.</p>`;
  } catch (error) {
    $("certification").textContent = error.message;
  }
}

async function loadHistory() {
  if (!token) return;
  try {
    const data = await api("/api/control-plane/changes?limit=200");
    $("changes").innerHTML = data.changes.length ? data.changes.map((item) => `<article class="panel"><strong>${esc(item.title)}</strong><p>${esc(item.purpose)}</p><p><code>${esc(item.change_id)}</code> · ${esc(item.status)} · ${esc(item.updated_at)}</p><pre>${esc(JSON.stringify(item.publication_receipt || item.validation_summary || {}, null, 2))}</pre></article>`).join("") : `<p class="muted">No changes yet.</p>`;
  } catch (error) { $("changes").textContent = error.message; }
}

function renderPlans(plans) {
  $("reorg-plans").innerHTML = plans.length ? plans.map((plan) => `<button class="plan" data-id="${esc(plan.plan_id)}"><strong>${esc(plan.title)}</strong><span>${esc(plan.status)}</span></button>`).join("") : `<p class="muted">No open plans.</p>`;
  document.querySelectorAll(".plan").forEach((button) => button.addEventListener("click", () => selectPlan(button.dataset.id, plans)));
}
function selectPlan(id, plans) {
  selectedPlan = plans.find((plan) => plan.plan_id === id) || null;
  $("reorg-detail").textContent = JSON.stringify(selectedPlan, null, 2);
  ["add-operation", "analyze-plan", "validate-plan", "publish-plan"].forEach((name) => $(name).disabled = !selectedPlan || selectedPlan.status === "PUBLISHED");
}
async function loadReorganisation() {
  if (!token) return;
  try { const data = await api("/api/control-plane/reorganisation/plans?status=open"); renderPlans(data.plans || []); } catch (error) { $("reorg-plans").textContent = error.message; }
}
async function createPlan() {
  const body = { title: $("plan-title").value.trim(), purpose: $("plan-purpose").value.trim(), workspace_key: $("plan-workspace").value.trim() || "reference" };
  const plan = await api("/api/control-plane/reorganisation/plans", { method:"POST", headers:{"Content-Type":"application/json","Idempotency-Key":key("plan")}, body:JSON.stringify(body) });
  selectedPlan = plan;
  await loadReorganisation();
}
async function addOperation() {
  const body = { operation_type: $("operation-type").value, payload: JSON.parse($("operation-payload").value || "{}") };
  const pageId = $("operation-page-id").value.trim();
  const revision = $("operation-revision").value.trim();
  if (pageId) body.page_resource_id = pageId;
  if (revision) body.expected_revision = revision;
  selectedPlan = await api(`/api/control-plane/reorganisation/plans/${selectedPlan.plan_id}/operations`, { method:"POST", headers:{"Content-Type":"application/json","Idempotency-Key":key("operation")}, body:JSON.stringify(body) });
  $("reorg-detail").textContent = JSON.stringify(selectedPlan, null, 2);
}
async function planAction(action) {
  selectedPlan = await api(`/api/control-plane/reorganisation/plans/${selectedPlan.plan_id}/${action}`, { method:"POST", headers:{"Content-Type":"application/json","Idempotency-Key":key(action)}, body:"{}" });
  $("reorg-detail").textContent = JSON.stringify(selectedPlan, null, 2);
  if (action === "publish") await Promise.all([loadReorganisation(), loadOverview(), loadHistory()]);
}

$("token").value = token;
$("connect").addEventListener("click", () => connect().catch((error) => $("identity").textContent = error.message));
document.querySelectorAll(".nav").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.view)));
$("refresh-overview").addEventListener("click", loadOverview);
$("refresh-history").addEventListener("click", loadHistory);
$("refresh-reorganisation").addEventListener("click", loadReorganisation);
$("retry-publication").addEventListener("click", async () => { $("certification").textContent = JSON.stringify(await api("/api/control-plane/publication/retry", {method:"POST",headers:{"Idempotency-Key":key("retry"),"Content-Type":"application/json"},body:"{}"}), null, 2); });
$("create-plan").addEventListener("click", () => createPlan().catch((error) => $("reorg-detail").textContent = error.message));
$("add-operation").addEventListener("click", () => addOperation().catch((error) => $("reorg-detail").textContent = error.message));
$("analyze-plan").addEventListener("click", () => planAction("analyze").catch((error) => $("reorg-detail").textContent = error.message));
$("validate-plan").addEventListener("click", () => planAction("validate").catch((error) => $("reorg-detail").textContent = error.message));
$("publish-plan").addEventListener("click", () => planAction("publish").catch((error) => $("reorg-detail").textContent = error.message));
window.addEventListener("hashchange", () => activate(viewFromHash()));
initialiseNavigation();
loadProductIdentity();
if (token) connect().catch(() => sessionStorage.removeItem("docplane-token"));
