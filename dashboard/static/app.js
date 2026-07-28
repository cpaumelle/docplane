const $ = (id) => document.getElementById(id);
let token = sessionStorage.getItem("docplane-token") || "";
let selectedPlan = null;

function headers(extra = {}) {
  return { Authorization: `Bearer ${token}`, ...extra };
}
function key(prefix) { return `${prefix}-${crypto.randomUUID()}`; }
function esc(value) { const node = document.createElement("span"); node.textContent = String(value ?? ""); return node.innerHTML; }
async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: headers(options.headers || {}) });
  const payload = await response.json().catch(() => ({ raw: response.statusText }));
  if (!response.ok) throw new Error(payload.detail?.upstream?.message || payload.detail?.code || payload.detail || payload.raw || `HTTP ${response.status}`);
  return payload;
}
function activate(name) {
  document.querySelectorAll(".view").forEach((item) => item.classList.remove("active"));
  document.querySelectorAll(".nav").forEach((item) => item.classList.remove("active"));
  $(`view-${name}`)?.classList.add("active");
  document.querySelector(`.nav[data-view="${name}"]`)?.classList.add("active");
  history.replaceState({}, "", name === "overview" ? "/" : `/?view=${encodeURIComponent(name)}`);
  if (name === "overview") loadOverview();
  if (name === "reorganisation") loadReorganisation();
  if (name === "history") loadHistory();
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
document.querySelectorAll(".nav").forEach((button) => button.addEventListener("click", () => activate(button.dataset.view)));
$("refresh-overview").addEventListener("click", loadOverview);
$("refresh-history").addEventListener("click", loadHistory);
$("refresh-reorganisation").addEventListener("click", loadReorganisation);

// ── Access: operator token issuance (bootstrap-gated; secret never stored server-side) ──
function accessRefreshLock() {
  const unlocked = !!sessionStorage.getItem("docplane-bootstrap");
  if ($("access-issue")) $("access-issue").disabled = !unlocked;
  if ($("access-unlock")) $("access-unlock").hidden = unlocked;
  if ($("access-lock")) $("access-lock").hidden = !unlocked;
}
$("access-unlock")?.addEventListener("click", () => {
  const v = $("access-bootstrap").value.trim();
  if (!v) { $("access-status").textContent = "Enter the bootstrap secret."; return; }
  sessionStorage.setItem("docplane-bootstrap", v);
  $("access-bootstrap").value = "";
  $("access-status").textContent = "Unlocked for this browser tab.";
  accessRefreshLock();
});
$("access-lock")?.addEventListener("click", () => {
  sessionStorage.removeItem("docplane-bootstrap");
  $("access-status").textContent = "Locked.";
  accessRefreshLock();
});
$("access-issue")?.addEventListener("click", async () => {
  const bootstrap = sessionStorage.getItem("docplane-bootstrap") || "";
  const display_name = $("access-name").value.trim();
  const principal_kind = $("access-kind").value;
  if (!bootstrap) { $("access-status").textContent = "Unlock first."; return; }
  if (!display_name) { $("access-status").textContent = "Enter a full name."; return; }
  $("access-status").textContent = "Issuing…";
  try {
    const resp = await fetch("/api/control-plane/issue-token", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-DocPlane-Bootstrap-Token": bootstrap },
      body: JSON.stringify({ display_name, principal_kind }),
    });
    const payload = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const code = payload.detail?.code || payload.detail?.upstream?.detail?.code || ("HTTP " + resp.status);
      $("access-status").textContent = "Failed: " + code;
      if (resp.status === 401) { sessionStorage.removeItem("docplane-bootstrap"); accessRefreshLock(); }
      return;
    }
    const tok = payload.token || "";
    $("access-result").innerHTML =
      "<p><strong>" + esc(payload.display_name) + "</strong> · " + esc(payload.principal_kind) + " · <code>" + esc(payload.token_prefix) + "</code></p>" +
      "<label>Token — shown once, copy now<input id=\"access-token-value\" readonly value=\"" + esc(tok) + "\"></label>" +
      "<div class=\"actions\"><button id=\"access-copy\">Copy token</button></div>";
    $("access-status").textContent = "Issued.";
    $("access-name").value = "";
    $("access-copy")?.addEventListener("click", () => {
      const el = $("access-token-value");
      if (navigator.clipboard) { navigator.clipboard.writeText(el.value).then(() => $("access-status").textContent = "Copied to clipboard.").catch(() => { el.select(); }); }
      else { el.select(); }
    });
  } catch (e) {
    $("access-status").textContent = "Failed: " + (e.message || e);
  }
});
accessRefreshLock();
$("retry-publication").addEventListener("click", async () => { $("certification").textContent = JSON.stringify(await api("/api/control-plane/publication/retry", {method:"POST",headers:{"Idempotency-Key":key("retry"),"Content-Type":"application/json"},body:"{}"}), null, 2); });
$("create-plan").addEventListener("click", () => createPlan().catch((error) => $("reorg-detail").textContent = error.message));
$("add-operation").addEventListener("click", () => addOperation().catch((error) => $("reorg-detail").textContent = error.message));
$("analyze-plan").addEventListener("click", () => planAction("analyze").catch((error) => $("reorg-detail").textContent = error.message));
$("validate-plan").addEventListener("click", () => planAction("validate").catch((error) => $("reorg-detail").textContent = error.message));
$("publish-plan").addEventListener("click", () => planAction("publish").catch((error) => $("reorg-detail").textContent = error.message));
const requested = new URLSearchParams(location.search).get("view");
if (requested && $(`view-${requested}`)) activate(requested);
if (token) connect().catch(() => sessionStorage.removeItem("docplane-token"));
