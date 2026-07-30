const $ = (id) => document.getElementById(id);
const VIEWS = new Set(["overview", "work", "explore", "review", "authoring", "history"]);
const TOKEN_KEY = "docplane-token";
let selectedPlan = null;
let authentication = null;
let observatory = null;
let exploredPages = [];
let pageCursor = null;
let candidateCursor = null;

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
  if (view === "work") loadWork();
  if (view === "explore") loadExplore();
  if (view === "review") loadReview();
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
    const maintenance = overview.modules.maintenance?.data?.queues || {};
    const deployments = overview.modules.deployments?.data?.attempts || [];
    const attention = Object.values(maintenance).reduce((total, items) => total + (items || []).length, 0);
    $("overview-cards").innerHTML = [
      ["Active pages", structure.summary?.active_pages ?? 0],
      ["Archived pages", structure.summary?.archived_pages ?? 0],
      ["Open changes", changes.filter((item) => !["PUBLISHED", "ABANDONED"].includes(item.status)).length],
      ["Active initiatives", Object.entries(work.by_state || {}).filter(([state]) => !["COMPLETE", "ABANDONED"].includes(state)).reduce((total, [,count]) => total + count, 0)],
      ["Needs attention", attention],
      ["Last deployment", deployments[0]?.status || "Unavailable"],
    ].map(([label,value]) => `<article class="card"><strong>${esc(value)}</strong><span>${esc(label)}</span></article>`).join("");
    renderCertification(certification, overview.modules.certification?.available !== false);
    $("recent-changes").innerHTML = changes.length ? changes.slice(0, 8).map((item) => `<button type="button" class="recent-change" data-id="${esc(item.change_id)}"><strong>${esc(item.title)}</strong><span>${esc(item.status)}</span></button>`).join("") : `<p class="muted">No changes yet.</p>`;
    document.querySelectorAll(".recent-change").forEach((button) => button.addEventListener("click", () => navigate("history")));
  } catch (error) {
    $("certification").textContent = error.message;
  }
}

function renderCertification(value, available) {
  if (!available) {
    $("certification").innerHTML = `<strong>Unavailable</strong><p>The certification module is unavailable.</p>`;
    return;
  }
  const working = value.working_state_identity || value.working_identity;
  const deployed = value.deployed_state_identity || value.deployed_identity;
  const failure = value.failure || value.failed_stage;
  let state = "Current";
  let detail = "Working and deployed identities match.";
  if (failure) {
    state = "Deployment failed";
    detail = `Failed at ${failure.stage || failure}. Inspect the latest deployment receipt before retrying.`;
  } else if (working && deployed && working !== deployed) {
    state = "Publication behind";
    detail = "The working corpus state has not been deployed.";
  } else if (value.current === false || value.status === "STALE") {
    state = "Publication behind";
    detail = "The working corpus state has not been deployed.";
  }
  $("certification").innerHTML = `<strong>${esc(state)}</strong><p>${esc(detail)}</p>`;
}

async function loadWork() {
  if (!authentication?.token()) return;
  try {
    const [queues, inbox, initiatives] = await Promise.all([
      api("/api/control-plane/work/queues"),
      api("/api/control-plane/work/captures?status=INBOX"),
      api("/api/control-plane/initiatives?limit=200"),
    ]);
    const states = queues.by_state || {};
    const wip = queues.wip_limit ?? 0;
    const active = states.ACTIVE ?? 0;
    $("work-cards").innerHTML = [
      ["Inbox", queues.inbox ?? 0],
      ["Now", wip ? `${active}/${wip}` : active],
      ["Roadmap", states.BACKLOG ?? 0],
      ["Blocked", states.BLOCKED ?? 0],
      ["Soaking", states.SOAKING ?? 0],
      ["Parked", states.PARKED ?? 0],
      ["Decisions needed", queues.decisions_needed ?? 0],
      ["Parked review due", queues.parked_review_due ?? 0],
      ["Soak review due", queues.soak_review_due ?? 0],
    ].map(([label, value]) => `<article class="card"><strong>${esc(value)}</strong><span>${esc(label)}</span></article>`).join("");
    if (wip && active > wip) {
      $("work-cards").insertAdjacentHTML("beforeend", `<article class="card"><strong>⚠</strong><span>Now exceeds the WIP limit — finish or park before starting more</span></article>`);
    }
    const open = (initiatives.initiatives || []);
    $("attach-target").innerHTML = `<option value="">Select an initiative…</option>` + open.map((item) => `<option value="${esc(item.initiative_id)}">${esc(item.title)} (${esc(item.work_state)})</option>`).join("");
    $("work-inbox").innerHTML = (inbox.captures || []).length ? inbox.captures.map((item) => `<article class="panel"><strong>${esc(item.kind)}</strong><p>${esc(item.body)}</p><p class="muted">${esc(item.created_at)}</p><div class="actions"><button class="capture-promote" data-id="${esc(item.capture_id)}">Promote</button><button class="capture-attach" data-id="${esc(item.capture_id)}">Attach</button><button class="capture-discard" data-id="${esc(item.capture_id)}">Discard</button></div></article>`).join("") : `<p class="muted">Inbox zero.</p>`;
    document.querySelectorAll(".capture-promote").forEach((button) => button.addEventListener("click", () => triageCapture(button.dataset.id, "promote").catch((error) => { $("work-inbox").textContent = error.message; })));
    document.querySelectorAll(".capture-attach").forEach((button) => button.addEventListener("click", () => triageCapture(button.dataset.id, "attach").catch((error) => { $("work-inbox").textContent = error.message; })));
    document.querySelectorAll(".capture-discard").forEach((button) => button.addEventListener("click", () => triageCapture(button.dataset.id, "discard").catch((error) => { $("work-inbox").textContent = error.message; })));
    const recent = (queues.recently_completed || []).map((item) => `<article class="panel"><strong>${esc(item.title)}</strong><p class="muted">completed ${esc(item.completed_at)}</p></article>`).join("");
    $("work-initiatives").innerHTML = (open.length ? open.map((item) => `<article class="panel"><strong>${esc(item.title)}</strong><p class="muted">${esc(item.initiative_key)} · ${esc(item.work_state)} · ${esc(item.priority)}</p><p>${esc(item.objective || "")}</p></article>`).join("") : `<p class="muted">No open initiatives.</p>`) + (recent ? `<h2>Recently completed</h2>${recent}` : "");
  } catch (error) {
    $("work-inbox").textContent = error.message;
  }
}

async function saveCapture() {
  const body = $("capture-body").value.trim();
  if (!body) return;
  await api("/api/control-plane/work/captures", { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": key("capture") }, body: JSON.stringify({ body, kind: $("capture-kind").value, origin: { channel: "WEB", tool: "dashboard-work-view" } }) });
  $("capture-body").value = "";
  await loadWork();
}

async function triageCapture(id, action) {
  const payload = {};
  if (action === "attach") {
    const target = $("attach-target").value;
    if (!target) { $("work-inbox").textContent = "Select an attach target initiative first."; return; }
    payload.initiative_id = target;
  }
  await api(`/api/control-plane/work/captures/${id}/${action}`, { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": key("triage") }, body: JSON.stringify(payload) });
  await loadWork();
}

function renderDirectories() {
  const rows = observatory?.directories || [];
  $("explore-directories").innerHTML = rows.length ? rows.map((row) => `
    <button class="directory" data-path="${esc(row.path)}">
      <strong>${esc(row.path)}</strong>
      <span>${esc(row.direct_pages)} direct · ${esc(row.descendant_pages)} descendants · ${esc(row.archived_pages)} archived</span>
      <span>depth ${esc(row.max_depth)} · ${esc(row.size_bytes)} bytes${row.protected ? " · protected" : ""}</span>
    </button>`).join("") : `<p class="muted">No directories in this snapshot.</p>`;
  document.querySelectorAll(".directory").forEach((button) => button.addEventListener("click", () => {
    $("explore-query").value = button.dataset.path;
    renderPages();
  }));
}

function renderPages() {
  const query = $("explore-query").value.trim().toLowerCase();
  const knowledge = $("explore-class").value;
  const archive = $("explore-archive").value;
  const dated = $("explore-dated").checked;
  const rows = exploredPages.filter((page) =>
    (!query || `${page.path} ${page.title || ""}`.toLowerCase().includes(query))
    && (!knowledge || page.knowledge_class === knowledge)
    && (!archive || page.status === archive)
    && (!dated || /\d{4}-\d{2}-\d{2}/.test(page.path))
  );
  $("explore-pages").innerHTML = rows.length ? rows.map((page) => `
    <article class="page-row">
      <strong>${esc(page.title || page.path)}</strong>
      <code>${esc(page.path)}</code>
      <span>${esc(page.knowledge_class || "unclassified")} · ${esc(page.verification_state || "unknown")} · revision ${esc(page.revision)}</span>
      <div class="actions"><a href="/?view=authoring&edit=${encodeURIComponent(page.path)}#authoring">Inspect / edit</a><button class="page-history" data-path="${esc(page.path)}">History</button><button class="page-verify" data-id="${esc(page.resource_id)}" data-path="${esc(page.path)}">Verify</button></div>
    </article>`).join("") : `<p class="muted">No loaded pages match these filters.</p>`;
  document.querySelectorAll(".page-history").forEach((button) => button.addEventListener("click", () => navigate("history")));
  document.querySelectorAll(".page-verify").forEach((button) => button.addEventListener("click", () => requestVerification({page_resource_id: button.dataset.id}, button.dataset.path)));
}

async function loadPageBatch(reset = false) {
  if (reset) { exploredPages = []; pageCursor = null; }
  const suffix = pageCursor ? `&after=${encodeURIComponent(pageCursor)}` : "";
  const data = await api(`/api/control-plane/observatory/pages?limit=100${suffix}`);
  exploredPages.push(...(data.pages?.items || []));
  pageCursor = data.pages?.next_after || null;
  $("explore-more").hidden = !data.pages?.has_more;
  const classes = [...new Set(exploredPages.map((page) => page.knowledge_class).filter(Boolean))].sort();
  const selected = $("explore-class").value;
  $("explore-class").innerHTML = `<option value="">All</option>` + classes.map((value) => `<option ${value === selected ? "selected" : ""}>${esc(value)}</option>`).join("");
  renderPages();
}

async function loadExplore() {
  if (!authentication?.token()) return;
  try {
    observatory = await api("/api/control-plane/observatory?candidate_limit=50");
    candidateCursor = observatory.review_candidates?.next_after || null;
    renderDirectories();
    await loadPageBatch(true);
  } catch (error) {
    $("explore-directories").textContent = error.message;
  }
}

function renderCandidates(values, append = false) {
  const html = values.length ? values.map((candidate) => `
    <article class="candidate ${candidate.protected ? "candidate--protected" : ""}">
      <div><strong>${esc(candidate.path)}</strong><span>${esc(candidate.severity)} · score ${esc(candidate.score)} · ${esc(candidate.evidence_count)} resources${candidate.protected ? " · protected surface" : ""}</span></div>
      <ul>${candidate.reasons.map((reason) => `<li><code>${esc(reason.code)}</code> ${esc(reason.explanation)}</li>`).join("")}</ul>
      <div class="actions"><button class="candidate-inspect" data-path="${esc(candidate.path)}">Inspect</button><button class="candidate-verify" data-path="${esc(candidate.path)}">Verify scope</button><button class="candidate-capture" data-path="${esc(candidate.path)}" data-reasons="${esc(candidate.reasons.map((item) => item.code).join(", "))}">Capture work</button><button class="candidate-plan" data-path="${esc(candidate.path)}">Begin plan</button></div>
    </article>`).join("") : `<p class="muted">No structural candidates in this snapshot.</p>`;
  if (append) $("review-candidates").insertAdjacentHTML("beforeend", html);
  else $("review-candidates").innerHTML = html;
  document.querySelectorAll(".candidate-inspect").forEach((button) => button.onclick = () => {
    navigate("explore");
    $("explore-query").value = button.dataset.path;
    renderPages();
  });
  document.querySelectorAll(".candidate-verify").forEach((button) => button.onclick = () => requestVerification({path_prefix: button.dataset.path}, button.dataset.path));
  document.querySelectorAll(".candidate-capture").forEach((button) => button.onclick = () => captureCandidate(button.dataset.path, button.dataset.reasons));
  document.querySelectorAll(".candidate-plan").forEach((button) => button.onclick = () => {
    $("plan-title").value = `Review ${button.dataset.path}`;
    $("plan-purpose").value = `Governed reorganisation candidate surfaced from ${button.dataset.path}.`;
    document.querySelector(".advanced").open = true;
    document.querySelector(".advanced").scrollIntoView({behavior: "smooth"});
  });
}

async function captureCandidate(path, reasons) {
  await api("/api/control-plane/work/captures", {
    method: "POST",
    headers: {"Content-Type": "application/json", "Idempotency-Key": key("observatory-capture")},
    body: JSON.stringify({body: `Review ${path}: ${reasons}`, kind: "FINDING", origin: {channel: "WEB", tool: "corpus-observatory"}}),
  });
  $("verification-scope-guidance").textContent = `Captured ${path} in the Work inbox.`;
}

async function loadReview() {
  if (!authentication?.token()) return;
  try {
    const [model, requests] = await Promise.all([
      api("/api/control-plane/observatory?candidate_limit=50"),
      api("/api/control-plane/verification-requests?status=OPEN"),
    ]);
    observatory = model;
    candidateCursor = model.review_candidates?.next_after || null;
    renderCandidates(model.review_candidates?.items || []);
    $("review-more").hidden = !model.review_candidates?.has_more;
    $("verification-requests").innerHTML = (requests.requests || []).length ? requests.requests.map((item) => `<article class="panel"><strong>${esc(item.reason)}</strong><p>${esc(item.note || item.path_prefix || item.page_resource_id || item.entity_id)}</p><p class="muted">${esc(item.requested_at)} · ${esc((item.briefing || {}).page_count ?? "?")} page(s)</p></article>`).join("") : `<p class="muted">No open requests.</p>`;
    await loadReorganisation();
  } catch (error) {
    $("review-candidates").textContent = error.message;
  }
}

async function requestVerification(scope, label) {
  try {
    await api("/api/control-plane/verification-requests", { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": key("verify") }, body: JSON.stringify({ ...scope, note: `Requested from Corpus Observatory Review: ${label}` }) });
    $("verification-scope-guidance").textContent = `Verification requested for ${label}.`;
    await loadReview();
  } catch (error) {
    const detail = error.payload?.detail?.upstream || error.payload?.detail || {};
    $("verification-scope-guidance").textContent = detail.code === "VERIFICATION_SCOPE_TOO_LARGE"
      ? `${detail.remedy || "Split the scope into narrower path prefixes."} Limit: ${detail.limit || 200} pages.`
      : error.message;
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
    "capture-save", "verify-section",
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
  $("work-cards").innerHTML = "";
  $("work-inbox").textContent = "Connect to load.";
  $("work-initiatives").textContent = "Connect to load.";
  $("explore-directories").textContent = "Connect to load.";
  $("explore-pages").textContent = "Connect to load.";
  $("review-candidates").textContent = "Connect to load.";
  $("verification-requests").textContent = "Connect to load.";
  setAuthenticatedControls(false);
}
function showAuthenticationState(state, detail = {}) {
  $("auth-presentation").hidden = state === "connected";
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
  $("refresh-work").addEventListener("click", loadWork);
  $("refresh-explore").addEventListener("click", loadExplore);
  $("refresh-review").addEventListener("click", loadReview);
  ["explore-query", "explore-class", "explore-archive", "explore-dated"].forEach((id) => $(id).addEventListener("input", renderPages));
  $("explore-more").addEventListener("click", () => loadPageBatch(false).catch((error) => { $("explore-pages").textContent = error.message; }));
  $("review-more").addEventListener("click", async () => {
    if (!candidateCursor) return;
    try {
      const data = await api(`/api/control-plane/observatory?candidate_limit=50&candidate_after=${encodeURIComponent(candidateCursor)}`);
      renderCandidates(data.review_candidates?.items || [], true);
      candidateCursor = data.review_candidates?.next_after || null;
      $("review-more").hidden = !data.review_candidates?.has_more;
    } catch (error) { $("review-candidates").textContent = error.message; }
  });
  $("export-observatory").addEventListener("click", async () => {
    try {
      const data = await api("/api/control-plane/observatory/export");
      const link = document.createElement("a");
      link.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: "application/json"}));
      link.download = `docplane-observatory-${data.fingerprint}.json`;
      link.click();
      URL.revokeObjectURL(link.href);
    } catch (error) { $("explore-pages").textContent = error.message; }
  });
  $("capture-save").addEventListener("click", () => saveCapture().catch((error) => { $("work-inbox").textContent = error.message; }));
  $("verify-section").addEventListener("click", () => {
    const prefix = $("verify-prefix").value.trim().replace(/\/+$/, "");
    if (!prefix) return;
    requestVerification({ path_prefix: prefix }, prefix);
  });
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
