const $ = (id) => document.getElementById(id);
const VIEWS = new Set(["overview", "authoring", "reorganisation", "history"]);
const TOKEN_KEY = "docplane-token";
let selectedPlan = null;
let authentication = null;

function headers(extra = {}) {
  const token = authentication?.token();
  return token ? { Authorization: `Bearer ${token}`, ...extra } : { ...extra };
}
function key(prefix) { return `${prefix}-${crypto.randomUUID()}`; }
function esc(value) { const node = document.createElement("span"); node.textContent = String(value ?? ""); return node.innerHTML; }
async function rawApi(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({ raw: response.statusText }));
  if (!response.ok) {
    const error = new Error(payload.detail?.upstream?.message || payload.detail?.message || payload.detail?.code || payload.detail || payload.raw || `HTTP ${response.status}`);
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}
async function api(path, options = {}, retryAuthentication = true) {
  try {
    return await rawApi(path, { ...options, headers: headers(options.headers || {}) });
  } catch (error) {
    if (retryAuthentication && [401, 403].includes(error.status) && authentication) {
      const recovered = await authentication.handleRejectedStatus(error.status);
      if (recovered) return api(path, options, false);
    }
    throw error;
  }
}

function createAuthentication({
  request,
  storage,
  onState = () => {},
  onConnected = () => {},
  onCleared = () => {},
}) {
  let bearer = "";
  let discovery = null;
  let state = "bootstrapping";
  let initializePromise = null;
  let issuancePromise = null;
  let recoveryPromise = null;
  let initialIssueAttempted = false;
  let replacementAttempted = false;

  const acquisition = () => discovery?.authentication?.token_acquisition || {};
  const publishState = (next, detail = {}) => {
    state = next;
    onState(next, detail);
  };
  const clear = (detail = {}) => {
    bearer = "";
    storage.removeItem(TOKEN_KEY);
    onCleared(detail);
  };
  const validate = async (candidate) => {
    const capability = await request("/api/control-plane/capabilities", {
      headers: {Authorization: `Bearer ${candidate}`},
    });
    bearer = candidate;
    storage.setItem(TOKEN_KEY, candidate);
    publishState("connected", {capability});
    onConnected(capability);
    return true;
  };
  const issue = async (kind) => {
    if (issuancePromise) return issuancePromise;
    if (kind === "initial") {
      if (initialIssueAttempted) return false;
      initialIssueAttempted = true;
    } else {
      if (replacementAttempted) return false;
      replacementAttempted = true;
    }
    issuancePromise = (async () => {
      const policy = acquisition();
      const endpoint = policy.endpoint;
      const method = String(policy.method || "").toUpperCase();
      if (!endpoint || method !== "POST") {
        throw new Error("Discovery did not advertise a supported credential-acquisition endpoint");
      }
      publishState("bootstrapping", {accessProfile: policy.access_profile});
      const issued = await request(endpoint, {
        method,
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({client_context: "DocPlane browser dashboard"}),
      });
      if (!issued?.token) throw new Error("Credential acquisition returned no bearer");
      return validate(issued.token);
    })();
    try {
      return await issuancePromise;
    } catch (error) {
      clear({error});
      const rateLimited = error.status === 429;
      publishState("bootstrap-failed", {
        error,
        message: rateLimited
          ? "Contributor issuance is rate limited. Use an existing token or retry later."
          : "Automatic contributor bootstrap failed. Use the routed DocPlane URL or an existing token.",
      });
      return false;
    } finally {
      issuancePromise = null;
    }
  };
  const initialize = () => {
    if (initializePromise) return initializePromise;
    initializePromise = (async () => {
      publishState("bootstrapping");
      try {
        discovery = await request("/api/control-plane/discovery");
      } catch (error) {
        clear({error});
        publishState("bootstrap-failed", {error, message: "DocPlane discovery is unavailable."});
        return false;
      }
      const cached = storage.getItem(TOKEN_KEY) || "";
      if (cached) {
        try {
          return await validate(cached);
        } catch (error) {
          clear({error});
        }
      }
      const policy = acquisition();
      if (policy.self_service === true) return issue("initial");
      publishState("managed-token-required", {
        procedure: policy.procedure || "Obtain a contributor token from the DocPlane operator.",
      });
      return false;
    })();
    return initializePromise;
  };
  const recover = () => {
    if (recoveryPromise) return recoveryPromise;
    recoveryPromise = (async () => {
      clear({reason: "credential-rejected"});
      if (acquisition().self_service !== true || replacementAttempted) {
        publishState("managed-token-required", {procedure: acquisition().procedure});
        return false;
      }
      return issue("replacement");
    })();
    return recoveryPromise;
  };
  const useToken = async (candidate) => {
    clear({reason: "manual-token"});
    try {
      return await validate(candidate);
    } catch (error) {
      clear({error});
      publishState("bootstrap-failed", {error, message: "The supplied contributor token was rejected."});
      return false;
    }
  };
  const handleRejectedStatus = (status) => [401, 403].includes(status) ? recover() : Promise.resolve(false);
  return {
    initialize,
    recover,
    handleRejectedStatus,
    useToken,
    clear,
    token: () => bearer,
    state: () => state,
    discovery: () => discovery,
  };
}

async function startDashboardAuthentication(controller, onReady) {
  const connected = await controller.initialize();
  if (connected) await onReady();
  return connected;
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

async function loadOverview() {
  if (!authentication?.token()) return;
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
  if (!authentication?.token()) return;
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
  if (!authentication?.token()) return;
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

function setAuthenticatedControls(enabled) {
  [
    "retry-publication",
    "authoring-propose", "authoring-validate", "authoring-publish",
    "create-plan", "add-operation", "analyze-plan", "validate-plan", "publish-plan",
  ].forEach((id) => {
    const node = $(id);
    if (node) node.disabled = !enabled;
  });
}
function clearAuthenticatedView() {
  $("token").value = "";
  $("identity").textContent = "Not connected";
  $("overview-cards").innerHTML = "";
  $("certification").textContent = "Not connected.";
  $("recent-changes").textContent = "Connect to load.";
  $("changes").textContent = "Connect to load.";
  setAuthenticatedControls(false);
}
function showAuthenticationState(state, detail = {}) {
  $("auth-status").dataset.state = state;
  if (state === "bootstrapping") $("auth-status").textContent = "Connecting…";
  if (state === "connected") $("auth-status").textContent = "Connected";
  if (state === "managed-token-required") {
    $("auth-status").textContent = "Contributor token required";
    $("auth-guidance").textContent = detail.procedure || "Obtain a contributor token from the DocPlane operator.";
    $("auth-fallback").open = true;
  }
  if (state === "bootstrap-failed") {
    $("auth-status").textContent = "Connection failed";
    $("auth-guidance").textContent = detail.message || "Automatic contributor bootstrap failed.";
    $("auth-fallback").open = true;
  }
}

if (typeof document !== "undefined") {
  authentication = createAuthentication({
    request: rawApi,
    storage: sessionStorage,
    onState: showAuthenticationState,
    onCleared: clearAuthenticatedView,
    onConnected: (capability) => {
      $("identity").textContent = `${capability.principal.display_name} · ${capability.principal.role}`;
      $("auth-guidance").textContent = "";
      $("auth-fallback").open = false;
      setAuthenticatedControls(true);
      document.dispatchEvent(new CustomEvent("docplane:connected"));
    },
  });
  $("connect").addEventListener("click", async () => {
    const candidate = $("token").value.trim();
    if (candidate && await authentication.useToken(candidate)) await loadOverview();
  });
  document.addEventListener("docplane:authentication-rejected", async () => {
    if (await authentication.recover()) await loadOverview();
  });
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
  startDashboardAuthentication(authentication, loadOverview);
}

globalThis.DocPlaneAuth = {createAuthentication, startDashboardAuthentication, TOKEN_KEY};
