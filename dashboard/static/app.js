const $ = (id) => document.getElementById(id);
const VIEWS = new Set(["overview", "work", "explore", "review", "classify", "authoring", "model", "observe", "history"]);
const TOKEN_KEY = "docplane-token";
let selectedPlan = null;
let authentication = null;
let observatory = null;
let exploredPages = [];
let explorePath = "";
let pageCursor = null;
let pageTotal = null;
let candidateCursor = null;
let exploreFilterTimer = null;
const selectedPages = new Map();
let classifyPages = [];
let classifyIndex = 0;
let classifySection = "";
let classifyScope = "__missing__";
let classifySummary = null;
let classifyDirectories = [];
let classifyTotal = null;
const previewCache = new Map();
let modelKind = "";
let modelKinds = [];

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

let toastTimer = null;
function notify(message, kind = "info") {
  const toast = $("toast");
  if (!toast) return;
  toast.textContent = message;
  toast.dataset.kind = kind;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, 6000);
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
function parseHash() {
  const raw = (location.hash || "").replace(/^#/, "");
  const [name, ...rest] = raw.split("=");
  return { view: validView(name), arg: rest.length ? decodeURIComponent(rest.join("=")) : null };
}
function activate(name, arg = null) {
  const view = validView(name);
  document.querySelectorAll(".view").forEach((item) => item.classList.remove("active"));
  document.querySelectorAll(".nav").forEach((item) => item.classList.remove("active"));
  $(`view-${view}`)?.classList.add("active");
  document.querySelector(`.nav[data-view="${view}"]`)?.classList.add("active");
  if (view === "overview") loadOverview();
  if (view === "work") loadWork();
  if (view === "explore") {
    if (arg !== null) explorePath = arg;
    loadExplore();
  }
  if (view === "review") loadReview();
  if (view === "classify") {
    if (arg !== null) classifySection = arg;
    loadClassify();
  }
  if (view === "model") loadModel(arg);
  if (view === "observe") loadObserve();
  if (view === "history") loadHistory();
}
function navigate(name, arg = null) {
  const view = validView(name);
  const target = arg ? `${view}=${encodeURIComponent(arg)}` : view;
  if (location.hash.replace(/^#/, "") === target) activate(view, arg);
  else location.hash = target;
}
function syncHashArg(view, arg) {
  // Keep the location shareable without minting history entries per click.
  const target = arg ? `#${view}=${encodeURIComponent(arg)}` : `#${view}`;
  if (document.querySelector(`.nav[data-view="${view}"]`)?.classList.contains("active")) {
    history.replaceState(null, "", target);
  }
}
function initialiseNavigation() {
  const params = new URLSearchParams(location.search);
  const hash = (location.hash || "").replace(/^#/, "");
  const parsed = parseHash();
  const requested = hash ? parsed.view : validView(params.get("view") || "overview");
  if (!hash) {
    const url = new URL(location.href);
    url.hash = requested;
    // Initial canonicalisation only: preserve ?view=...&edit=... for authoring.js.
    history.replaceState({ view: requested }, "", url);
  }
  activate(requested, hash ? parsed.arg : null);
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
    const coverage = overview.modules.coverage?.data || null;
    const attention = Object.values(maintenance).reduce((total, items) => total + (items || []).length, 0);
    const classification = structure.summary?.classification || {};
    const coverageGaps = coverage
      ? (coverage.unwatched_services || []).length + (coverage.paging_alerts_without_runbook || []).length
      : null;
    const campaigns = [
      ["Unclassified pages", classification.missing ?? "?", "classify", "Burn down the classification backfill"],
      ["Review candidates", structure.review_candidates?.total ?? 0, "review", "Ranked structural attention queue"],
      ["Work inbox", work.inbox ?? 0, "work", "Untriaged captures"],
      ["Coverage gaps", coverageGaps ?? "—", "observe", "Unwatched services and paging alerts without runbooks"],
      ["Needs attention", attention, "review", "Maintenance queues"],
      ["Open changes", changes.filter((item) => !["PUBLISHED", "ABANDONED"].includes(item.status)).length, "history", "Changes not yet published"],
      ["Active pages", structure.summary?.active_pages ?? 0, "explore", "Browse the corpus"],
    ];
    $("overview-cards").innerHTML = campaigns.map(([label, value, target, hint]) =>
      `<button type="button" class="card card--link" data-target="${esc(target)}" title="${esc(hint)}"><strong>${esc(value)}</strong><span>${esc(label)}</span></button>`).join("");
    $("overview-cards").querySelectorAll(".card--link").forEach((button) =>
      button.addEventListener("click", () => navigate(button.dataset.target)));
    renderCertification(certification, overview.modules.certification?.available !== false);
    $("recent-changes").innerHTML = changes.length ? changes.slice(0, 8).map((item) => `<button type="button" class="recent-change" data-id="${esc(item.change_id)}"><strong>${esc(item.title)}</strong><span>${esc(item.status)}</span></button>`).join("") : `<p class="muted">No changes yet.</p>`;
    document.querySelectorAll(".recent-change").forEach((button) => button.addEventListener("click", () => navigate("history")));
  } catch (error) {
    $("certification").textContent = error.message;
  }
}

function renderCertification(value, available) {
  if (!available || value.state === "UNAVAILABLE") {
    $("certification").innerHTML = `<strong>Unavailable</strong><p>The certification module is unavailable.</p>`;
    return;
  }
  const working = value.working_state_identity || value.working_identity;
  const deployed = value.deployed_state_identity || value.deployed_identity;
  const failure = value.last_failure || value.failure || value.failed_stage;
  let state = "Current";
  let detail = "Working and deployed identities match.";
  if (value.state === "DEPLOYMENT_FAILED" || failure) {
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
    $("work-inbox").innerHTML = (inbox.captures || []).length ? inbox.captures.map((item) => {
      // Origin context is the triage superpower of the distraction ledger:
      // "where was I when I thought this" renders right on the card.
      const origin = item.origin || {};
      const originLine = [origin.tool || origin.channel, origin.context].filter(Boolean).join(" · ");
      return `<article class="panel"><strong>${esc(item.kind)}</strong><p>${esc(item.body)}</p>${originLine ? `<p class="muted origin-line">from ${esc(originLine)}</p>` : ""}<p class="muted">${esc(item.created_at)}</p><div class="actions"><button class="capture-promote" data-id="${esc(item.capture_id)}">Promote</button><button class="capture-attach" data-id="${esc(item.capture_id)}">Attach</button><button class="capture-discard" data-id="${esc(item.capture_id)}">Discard</button></div></article>`;
    }).join("") : `<p class="muted">Inbox zero.</p>`;
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

function exploreSearchActive() {
  return Boolean($("explore-query").value.trim());
}

function renderBreadcrumbs() {
  const element = $("explore-breadcrumbs");
  if (exploreSearchActive()) {
    element.innerHTML = `<span class="crumb crumb--current">Search results</span><button class="crumb-clear" id="explore-clear-search">Clear search</button>`;
    $("explore-clear-search").addEventListener("click", () => { $("explore-query").value = ""; refreshExploreListing(); });
    return;
  }
  const segments = explorePath ? explorePath.split("/") : [];
  element.innerHTML = [
    `<button class="crumb${segments.length ? "" : " crumb--current"}" data-path="">Corpus</button>`,
    ...segments.map((segment, index) => {
      const path = segments.slice(0, index + 1).join("/");
      const last = index === segments.length - 1;
      return `<span class="crumb-sep">/</span>` + (last
        ? `<span class="crumb crumb--current">${esc(segment)}</span>`
        : `<button class="crumb" data-path="${esc(path)}">${esc(segment)}</button>`);
    }),
  ].join("");
  element.querySelectorAll("button.crumb").forEach((button) => button.addEventListener("click", () => {
    explorePath = button.dataset.path;
    refreshExploreListing();
  }));
}

function renderDirectories() {
  if (exploreSearchActive()) {
    $("explore-directories").innerHTML = `<p class="muted">Search is corpus-wide. Clear it to browse by directory.</p>`;
    return;
  }
  const prefix = explorePath ? `${explorePath}/` : "";
  const children = (observatory?.directories || []).filter((row) =>
    row.path.startsWith(prefix) && row.path !== explorePath && !row.path.slice(prefix.length).includes("/"));
  $("explore-directories").innerHTML = children.length ? children.map((row) => {
    const pages = row.direct_pages + row.descendant_pages;
    return `
    <button class="directory" data-path="${esc(row.path)}">
      <span class="directory__name">${esc(row.path.slice(prefix.length))}</span>
      <span class="directory__meta">${esc(pages)} page${pages === 1 ? "" : "s"}${row.archived_pages ? ` · ${esc(row.archived_pages)} archived` : ""}${row.protected ? ` · <span class="chip chip--warn">protected</span>` : ""}</span>
    </button>`;
  }).join("") : `<p class="muted">No subdirectories here.</p>`;
  $("explore-directories").querySelectorAll(".directory").forEach((button) => button.addEventListener("click", () => {
    explorePath = button.dataset.path;
    refreshExploreListing();
  }));
}

// Mirrors docs-api app.generator.page_domain — the canonical page → domain
// derivation for the four-domain model. Keep in lockstep with it.
function pageDomain(page) {
  if ((page.knowledge_class || "").trim().toUpperCase() === "WORK_NOTE") return "work";
  const head = (page.path || "").split("/")[0];
  return head === "observe" || head === "model" || head === "work" ? head : "know";
}

const KNOWLEDGE_CLASSES = ["ARCHITECTURE", "OPERATION", "REFERENCE", "POLICY", "DECISION", "EVIDENCE", "DESIGN", "WORK_NOTE"];

function renderSelectionBar() {
  const bar = $("explore-selection");
  bar.hidden = selectedPages.size === 0;
  $("explore-selection-count").textContent = `${selectedPages.size} selected`;
  $("explore-move-stage").disabled = !authentication?.token() || selectedPages.size === 0;
  $("explore-directory-options").innerHTML = (observatory?.directories || [])
    .map((row) => `<option value="${esc(row.path)}"></option>`).join("");
}

function renderPages() {
  const rows = exploredPages;
  $("explore-pages-title").textContent = exploreSearchActive()
    ? "Search results"
    : (explorePath ? explorePath.split("/").pop() : "Top-level pages");
  $("explore-count").textContent = pageTotal === null ? "" : `${rows.length} of ${pageTotal}`;
  $("explore-pages").innerHTML = rows.length ? rows.map((page) => {
    const verification = (page.verification_state || "UNVERIFIED").toUpperCase();
    const domain = pageDomain(page);
    // The class chip IS the switcher: a select styled as the chip, driving
    // the governed trust classify verb (optimistic-locked, audited).
    const classOptions = KNOWLEDGE_CLASSES.map((value) =>
      `<option value="${value}"${page.knowledge_class === value ? " selected" : ""}>${value.toLowerCase().replace(/_/g, " ")}</option>`).join("");
    const chips = [
      `<span class="dp-badge" data-domain="${esc(domain)}">${esc(domain)}</span>`,
      `<select class="chip chip-class${page.knowledge_class ? "" : " chip-class--unset"}" data-id="${esc(page.resource_id)}" title="Knowledge class — changing it records a governed reclassification">
         <option value=""${page.knowledge_class ? "" : " selected"} disabled hidden>set class…</option>${classOptions}</select>`,
      `<span class="chip chip--${verification === "VERIFIED" ? "ok" : "quiet"}">${esc(verification.toLowerCase())}</span>`,
      page.status === "archived" ? `<span class="chip chip--warn">archived</span>` : "",
    ].join("");
    return `
    <div class="page-line">
      <label class="page-line__select"><input type="checkbox" class="page-select" data-id="${esc(page.resource_id)}"${selectedPages.has(page.resource_id) ? " checked" : ""} aria-label="Select ${esc(page.path)}"></label>
      <div class="page-line__id" title="${esc(page.path)} · revision ${esc(page.revision)}"><strong>${esc(page.title || page.path)}</strong><code>${esc(page.path)}</code></div>
      <div class="page-line__chips">${chips}</div>
      <div class="page-line__actions"><a href="/?view=authoring&edit=${encodeURIComponent(page.path)}#authoring">Edit</a><button class="page-history" data-path="${esc(page.path)}">History</button><button class="page-verify" data-id="${esc(page.resource_id)}" data-path="${esc(page.path)}">Verify</button></div>
    </div>`;
  }).join("") : `<p class="muted">${exploreSearchActive() ? "No pages match this search." : "No pages directly in this directory."}</p>`;
  $("explore-pages").querySelectorAll(".page-history").forEach((button) => button.addEventListener("click", () => navigate("history")));
  $("explore-pages").querySelectorAll(".page-verify").forEach((button) => button.addEventListener("click", () => requestVerification({page_resource_id: button.dataset.id}, button.dataset.path)));
  $("explore-pages").querySelectorAll(".chip-class").forEach((select) => select.addEventListener("change", () => reclassifyPage(select.dataset.id, select.value, select)));
  $("explore-pages").querySelectorAll(".page-select").forEach((box) => box.addEventListener("change", () => {
    const page = exploredPages.find((item) => item.resource_id === box.dataset.id);
    if (!page) return;
    if (box.checked) selectedPages.set(page.resource_id, page);
    else selectedPages.delete(page.resource_id);
    renderSelectionBar();
  }));
  renderSelectionBar();
}

// Shared governed classify call: echoes the page's current trust metadata with
// only the class changed — the classify verb replaces the whole set under an
// optimistic lock, so a stale row loses cleanly (409/422) instead of clobbering
// concurrent edits. Returns the trust row on success; throws with a humane
// message on failure.
async function classifyCall(resourceId, knowledgeClass, previousClass, origin) {
  const trust = await api(`/api/v1/pages/${resourceId}/trust`);
  try {
    await api(`/api/v1/pages/${resourceId}/classification`, {
      method: "POST",
      headers: {"Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID()},
      body: JSON.stringify({
        expected_metadata_version: trust.metadata_version,
        workspace_id: trust.workspace_id,
        publication_state: trust.publication_state,
        knowledge_class: knowledgeClass,
        criticality: trust.criticality,
        owner_principal_id: trust.owner_principal_id,
        review_due_at: trust.review_due_at,
        provenance: trust.provenance,
        reason: `Reclassified ${previousClass || "(unset)"} → ${knowledgeClass} from ${origin}`,
      }),
    });
  } catch (error) {
    const detail = error.payload?.detail || {};
    const explanation = detail.code === "WORKSPACE_KNOWLEDGE_CLASS_MISMATCH"
      ? "WORK_NOTE is reserved for pages in a WORK workspace (and vice versa)."
      : detail.code === "PAGE_METADATA_VERSION_STALE"
        ? "Page metadata changed underneath you — the list will refresh."
        : (error.message || "Reclassification failed.");
    const wrapped = new Error(explanation);
    wrapped.code = detail.code;
    throw wrapped;
  }
  return trust;
}

async function reclassifyPage(resourceId, knowledgeClass, select) {
  const page = exploredPages.find((item) => item.resource_id === resourceId);
  if (!page || !knowledgeClass || page.knowledge_class === knowledgeClass) return;
  select.disabled = true;
  try {
    const trust = await classifyCall(resourceId, knowledgeClass, page.knowledge_class, "Explore");
    page.knowledge_class = knowledgeClass;
    page.verification_state = trust.verification_state;
    notify(`${page.path} → ${knowledgeClass.toLowerCase().replace(/_/g, " ")}`);
  } catch (error) {
    notify(error.message, "warn");
  } finally {
    select.disabled = false;
    if (exploreSearchActive()) renderPages(); else refreshExploreListing();
  }
}

async function loadPageBatch(reset = false) {
  if (reset) { exploredPages = []; pageCursor = null; pageTotal = null; }
  const params = new URLSearchParams({limit: "100"});
  if (pageCursor) params.set("after", pageCursor);
  if (exploreSearchActive()) {
    params.set("q", $("explore-query").value.trim());
  } else {
    // Browsing mode is a server-scoped drill-down, never a client-side sieve.
    params.set("path_prefix", explorePath);
    if (!$("explore-descendants").checked) params.set("depth", "direct");
  }
  if ($("explore-class").value) params.set("knowledge_class", $("explore-class").value);
  if ($("explore-family").value) params.set("identifier_family", $("explore-family").value);
  if ($("explore-archive").value) params.set("archive_state", $("explore-archive").value);
  if ($("explore-dated").checked) params.set("dated_only", "true");
  const data = await api(`/api/control-plane/observatory/pages?${params}`);
  exploredPages.push(...(data.pages?.items || []));
  pageCursor = data.pages?.next_after || null;
  pageTotal = data.pages?.total ?? null;
  $("explore-more").hidden = !data.pages?.has_more;
  renderPages();
}

function refreshExploreListing() {
  renderBreadcrumbs();
  renderDirectories();
  syncHashArg("explore", explorePath);
  return loadPageBatch(true).catch((error) => { $("explore-pages").textContent = error.message; });
}

async function loadExplore() {
  if (!authentication?.token()) return;
  try {
    observatory = await api("/api/control-plane/observatory?candidate_limit=50");
    candidateCursor = observatory.review_candidates?.next_after || null;
    $("explore-class").innerHTML = `<option value="">All</option><option value="__missing__">(missing)</option>` + (observatory.facets?.knowledge_class || []).map((value) => `<option>${esc(value)}</option>`).join("");
    $("explore-family").innerHTML = `<option value="">All</option>` + (observatory.facets?.identifier_family || []).map((value) => `<option>${esc(value)}</option>`).join("");
    await refreshExploreListing();
  } catch (error) {
    $("explore-directories").textContent = error.message;
  }
}

async function stageMovePlan() {
  const target = $("explore-move-target").value.trim().replace(/^\/+|\/+$/g, "");
  if (!selectedPages.size) return;
  if (!target) { notify("Choose a target directory first.", "warn"); return; }
  const pages = [...selectedPages.values()];
  $("explore-move-stage").disabled = true;
  try {
    const plan = await api("/api/control-plane/reorganisation/plans", {
      method: "POST",
      headers: {"Content-Type": "application/json", "Idempotency-Key": key("plan")},
      body: JSON.stringify({
        title: `Move ${pages.length} page${pages.length === 1 ? "" : "s"} to ${target}/`,
        purpose: `Staged from Explore: ${pages.map((page) => page.path).join(", ")}`.slice(0, 3900),
        workspace_key: "reference",
      }),
    });
    for (const page of pages) {
      const filename = page.path.split("/").pop();
      await api(`/api/control-plane/reorganisation/plans/${plan.plan_id}/operations`, {
        method: "POST",
        headers: {"Content-Type": "application/json", "Idempotency-Key": key("operation")},
        body: JSON.stringify({
          operation_type: "MOVE_PAGE",
          page_resource_id: page.resource_id,
          expected_revision: String(page.revision),
          payload: {to_path: `${target}/${filename}`, create_redirect: true},
        }),
      });
    }
    selectedPages.clear();
    renderSelectionBar();
    notify(`Plan staged with ${pages.length} move${pages.length === 1 ? "" : "s"} — analyzing…`);
    navigate("review");
    const data = await api("/api/control-plane/reorganisation/plans?status=open");
    renderPlans(data.plans || []);
    const staged = (data.plans || []).find((item) => item.plan_id === plan.plan_id) || plan;
    selectPlan(staged);
    await planAction("analyze");
  } catch (error) {
    notify(error.message, "warn");
  } finally {
    $("explore-move-stage").disabled = false;
  }
}

function renderCandidates(values, append = false) {
  const html = values.length ? values.map((candidate) => `
    <article class="candidate ${candidate.protected ? "candidate--protected" : ""}">
      <div><strong>${esc(candidate.path)}</strong><span>${esc(candidate.severity)} · score ${esc(candidate.score)} · ${esc(candidate.evidence_count)} resources${candidate.protected ? " · protected surface" : ""}</span></div>
      <ul>${candidate.reasons.map((reason) => `<li><code>${esc(reason.code)}</code> ${esc(reason.explanation)}</li>`).join("")}</ul>
      <div class="actions"><button class="candidate-inspect" data-path="${esc(candidate.path)}">Inspect</button><button class="candidate-verify" data-path="${esc(candidate.path)}">Verify scope</button><button class="candidate-capture" data-path="${esc(candidate.path)}" data-reasons="${esc(candidate.reasons.map((item) => item.code).join(", "))}">Capture work</button></div>
    </article>`).join("") : `<p class="muted">No structural candidates in this snapshot.</p>`;
  if (append) $("review-candidates").insertAdjacentHTML("beforeend", html);
  else $("review-candidates").innerHTML = html;
  document.querySelectorAll(".candidate-inspect").forEach((button) => button.onclick = () => {
    // Candidates are directory scopes: drill the browser straight there and
    // stage any moves from the selection bar.
    $("explore-query").value = "";
    navigate("explore", button.dataset.path);
  });
  document.querySelectorAll(".candidate-verify").forEach((button) => button.onclick = () => requestVerification({path_prefix: button.dataset.path}, button.dataset.path));
  document.querySelectorAll(".candidate-capture").forEach((button) => button.onclick = () => captureFinding(`Review ${button.dataset.path}: ${button.dataset.reasons}`, "corpus-observatory").then(() => {
    $("verification-scope-guidance").textContent = `Captured ${button.dataset.path} in the Work inbox.`;
  }));
}

async function captureFinding(body, tool) {
  await api("/api/control-plane/work/captures", {
    method: "POST",
    headers: {"Content-Type": "application/json", "Idempotency-Key": key("finding-capture")},
    body: JSON.stringify({body, kind: "FINDING", origin: {channel: "WEB", tool}}),
  });
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
    $("changes").innerHTML = data.changes.length ? data.changes.map((item) => {
      const receipt = item.publication_receipt || item.validation_summary || null;
      const operations = receipt?.operations_checked ?? receipt?.operation_count;
      const meta = [
        item.status,
        operations !== undefined ? `${operations} op${operations === 1 ? "" : "s"}` : "",
        item.updated_at,
      ].filter(Boolean).join(" · ");
      return `<article class="panel change-row">
        <div><strong>${esc(item.title)}</strong><span class="chip chip--${item.status === "PUBLISHED" ? "ok" : "quiet"}">${esc(item.status.toLowerCase())}</span></div>
        <p>${esc(item.purpose)}</p>
        <p class="muted"><code>${esc(item.change_id)}</code> · ${esc(meta)}</p>
        ${receipt ? `<details><summary>Receipt</summary><pre>${esc(JSON.stringify(receipt, null, 2))}</pre></details>` : ""}
      </article>`;
    }).join("") : `<p class="muted">No changes yet.</p>`;
  } catch (error) { $("changes").textContent = error.message; }
}

function renderPlans(plans) {
  $("reorg-plans").innerHTML = plans.length ? plans.map((plan) => `<button class="plan" data-id="${esc(plan.plan_id)}"><strong>${esc(plan.title)}</strong><span>${esc(plan.status)}</span></button>`).join("") : `<p class="muted">No open plans. Stage one from Explore.</p>`;
  document.querySelectorAll(".plan").forEach((button) => button.addEventListener("click", () => {
    const plan = plans.find((item) => item.plan_id === button.dataset.id) || null;
    selectPlan(plan);
  }));
}
function describeOperation(operation) {
  const payload = operation.payload || {};
  const parts = [`${operation.sequence ?? ""}`.trim(), operation.operation_type];
  if (payload.to_path) parts.push(`→ ${payload.to_path}`);
  if (payload.to_nav_path) parts.push(`(nav ${payload.to_nav_path})`);
  const analysis = operation.analysis_result;
  const verdict = analysis
    ? (analysis.passed === false || (analysis.errors || []).length
        ? `<span class="chip chip--warn">blocked</span>`
        : `<span class="chip chip--ok">ok</span>`)
    : "";
  const errors = (analysis?.errors || []).map((error) => `<li>${esc(typeof error === "string" ? error : error.code || JSON.stringify(error))}</li>`).join("");
  return `<div class="op-row"><span>${esc(parts.filter(Boolean).join(" "))}</span>${verdict}</div>${errors ? `<ul class="op-errors">${errors}</ul>` : ""}`;
}
function renderPlanDetail() {
  if (!selectedPlan) { $("reorg-detail").innerHTML = `<p class="muted">No plan selected.</p>`; return; }
  const receipt = selectedPlan.validation_receipt || selectedPlan.analysis_receipt || null;
  const receiptLine = receipt
    ? `${receipt.passed ? "Passed" : "Not passing"} · ${(receipt.errors || []).length} error(s) · ${(receipt.warnings || []).length} warning(s) · ${receipt.operations_checked ?? "?"} operation(s) checked`
    : "Not analyzed yet.";
  $("reorg-detail").innerHTML = `
    <div><strong>${esc(selectedPlan.title)}</strong> <span class="chip chip--${selectedPlan.status === "VALIDATED" ? "ok" : "quiet"}">${esc(selectedPlan.status.toLowerCase())}</span></div>
    <p class="muted">${esc(selectedPlan.purpose || "")}</p>
    <div class="list">${(selectedPlan.operations || []).map(describeOperation).join("") || `<p class="muted">No operations.</p>`}</div>
    <p class="muted">${esc(receiptLine)}</p>
    ${(receipt?.errors || []).length ? `<ul class="op-errors">${receipt.errors.map((error) => `<li>${esc(typeof error === "string" ? error : error.code || JSON.stringify(error))}</li>`).join("")}</ul>` : ""}
    <details><summary>Raw plan</summary><pre>${esc(JSON.stringify(selectedPlan, null, 2))}</pre></details>`;
}
function selectPlan(plan) {
  selectedPlan = plan;
  renderPlanDetail();
  ["analyze-plan", "validate-plan", "publish-plan"].forEach((name) => $(name).disabled = !selectedPlan || selectedPlan.status === "PUBLISHED");
}
async function loadReorganisation() {
  if (!authentication?.token()) return;
  try { const data = await api("/api/control-plane/reorganisation/plans?status=open"); renderPlans(data.plans || []); } catch (error) { $("reorg-plans").textContent = error.message; }
}
async function planAction(action) {
  if (!selectedPlan) return;
  selectedPlan = await api(`/api/control-plane/reorganisation/plans/${selectedPlan.plan_id}/${action}`, { method:"POST", headers:{"Content-Type":"application/json","Idempotency-Key":key(action)}, body:"{}" });
  selectPlan(selectedPlan);
  if (action === "publish") {
    notify("Plan published — certification refreshing.");
    await Promise.all([loadReorganisation(), loadOverview(), loadHistory()]);
  }
}

/* ---------- Classify workbench ---------- */

function classifyCurrent() { return classifyPages[classifyIndex] || null; }

async function loadClassify() {
  if (!authentication?.token()) return;
  try {
    const snapshot = await api("/api/control-plane/observatory?candidate_limit=1");
    classifySummary = snapshot.summary?.classification || {missing: 0, missing_by_section: {}};
    classifyDirectories = snapshot.directories || [];
    const params = new URLSearchParams({limit: "100", knowledge_class: classifyScope});
    params.set("path_prefix", classifySection || "");
    const data = await api(`/api/control-plane/observatory/pages?${params}`);
    classifyPages = data.pages?.items || [];
    classifyTotal = data.pages?.total ?? classifyPages.length;
    classifyIndex = 0;
    renderClassify();
  } catch (error) {
    $("classify-current").textContent = error.message;
  }
}

function classifySectionCounts() {
  // Backfill scope burns down the server's classification audit; class-review
  // scopes derive per-section counts from the directory aggregates when the
  // snapshot carries them.
  if (classifyScope === "__missing__") {
    return {total: classifySummary?.missing ?? 0, sections: Object.entries(classifySummary?.missing_by_section || {})};
  }
  const heads = new Map();
  let total = 0;
  for (const row of classifyDirectories) {
    if (row.path.includes("/")) continue;
    const count = row.knowledge_class?.[classifyScope] ?? 0;
    if (count > 0) { heads.set(row.path, count); total += count; }
  }
  return {total: heads.size ? total : (classifyTotal ?? 0), sections: [...heads.entries()]};
}

function renderClassifyBurndown() {
  const {total, sections} = classifySectionCounts();
  $("classify-burndown").innerHTML = [
    `<button class="chip chip-filter${classifySection ? "" : " chip-filter--active"}" data-section="">All <strong>${esc(total)}</strong></button>`,
    ...sections.sort((a, b) => b[1] - a[1]).map(([section, count]) =>
      `<button class="chip chip-filter${classifySection === section ? " chip-filter--active" : ""}" data-section="${esc(section)}">${esc(section)} <strong>${esc(count)}</strong></button>`),
  ].join("");
  $("classify-burndown").querySelectorAll(".chip-filter").forEach((button) => button.addEventListener("click", () => {
    classifySection = button.dataset.section;
    syncHashArg("classify", classifySection);
    loadClassify();
  }));
}

async function renderClassifyPreview(page) {
  const preview = $("classify-preview");
  if (!page) { preview.hidden = true; return; }
  preview.hidden = false;
  if (previewCache.has(page.resource_id)) {
    preview.textContent = previewCache.get(page.resource_id);
    return;
  }
  preview.textContent = "Loading page body…";
  try {
    const body = await api(`/api/control-plane/authoring/pages/${page.resource_id}`);
    const content = String(body.content || "").slice(0, 2500) + (String(body.content || "").length > 2500 ? "\n…" : "");
    previewCache.set(page.resource_id, content);
    if (classifyCurrent()?.resource_id === page.resource_id) preview.textContent = content;
  } catch (_error) {
    preview.textContent = "Body preview unavailable — classify from the path and title, or open the page in Author.";
  }
}

function renderClassify() {
  renderClassifyBurndown();
  const remaining = classifyPages.length;
  const scopeLabel = classifyScope === "__missing__" ? "unclassified" : classifyScope.toLowerCase().replace(/_/g, " ");
  $("classify-progress").textContent = remaining
    ? `${remaining} of ${classifyTotal ?? remaining} ${scopeLabel} page${remaining === 1 ? "" : "s"} loaded${classifySection ? ` in ${classifySection}/` : ""} · position ${classifyIndex + 1}`
    : "";
  const page = classifyCurrent();
  if (!page) {
    $("classify-current").innerHTML = classifyScope === "__missing__" && (classifySummary?.missing ?? 0) === 0
      ? `<strong>Nothing left to backfill.</strong><p class="muted">Every page carries a governed knowledge class. Switch the scope above to review a class for accuracy.</p>`
      : `<strong>Scope clear.</strong><p class="muted">No ${scopeLabel} pages loaded${classifySection ? ` in ${classifySection}/` : ""}. Pick another section or scope above.</p>`;
    $("classify-actions").innerHTML = "";
    $("classify-preview").hidden = true;
    $("classify-queue").innerHTML = "";
    return;
  }
  $("classify-current").innerHTML = `
    <strong>${esc(page.title || page.path)}</strong>
    <p><code>${esc(page.path)}</code></p>
    <p class="muted">${esc(page.identifier_family || "")}${page.dated ? " · dated file" : ""}${page.status === "archived" ? " · archived" : ""} · revision ${esc(page.revision)}</p>
    <p><a href="/?view=authoring&edit=${encodeURIComponent(page.path)}#authoring">Open in Author</a></p>`;
  $("classify-actions").innerHTML = KNOWLEDGE_CLASSES.map((value, index) =>
    `<button class="class-assign" data-class="${value}"><kbd>${index + 1}</kbd> ${value.toLowerCase().replace(/_/g, " ")}</button>`).join("")
    + `<button class="class-skip ghost" id="classify-skip"><kbd>s</kbd> skip</button>`;
  $("classify-actions").querySelectorAll(".class-assign").forEach((button) => button.addEventListener("click", () => assignClass(button.dataset.class)));
  $("classify-skip").addEventListener("click", () => stepClassify(1));
  $("classify-queue").innerHTML = classifyPages.slice(classifyIndex + 1, classifyIndex + 11).map((item) =>
    `<div class="queue-row"><strong>${esc(item.title || item.path)}</strong><code>${esc(item.path)}</code></div>`).join("") || `<p class="muted">End of the loaded queue.</p>`;
  renderClassifyPreview(page);
}

function stepClassify(delta) {
  if (!classifyPages.length) return;
  classifyIndex = Math.min(Math.max(classifyIndex + delta, 0), classifyPages.length - 1);
  renderClassify();
}

function dropCurrentFromQueue() {
  classifyPages.splice(classifyIndex, 1);
  if (classifyTotal !== null) classifyTotal = Math.max(classifyTotal - 1, 0);
  if (classifyIndex >= classifyPages.length) classifyIndex = Math.max(classifyPages.length - 1, 0);
}

async function assignClass(knowledgeClass) {
  const page = classifyCurrent();
  if (!page) return;
  if (page.knowledge_class === knowledgeClass) {
    // Review mode: re-affirming the current class is a skip, not a write.
    dropCurrentFromQueue();
    notify(`${page.path} already ${knowledgeClass.toLowerCase().replace(/_/g, " ")} — confirmed, no write.`);
    renderClassify();
    return;
  }
  try {
    await classifyCall(page.resource_id, knowledgeClass, page.knowledge_class, "Classify workbench");
    dropCurrentFromQueue();
    if (classifyScope === "__missing__" && classifySummary) {
      classifySummary.missing = Math.max((classifySummary.missing || 1) - 1, 0);
      const section = page.path.split("/")[0];
      const bySection = classifySummary.missing_by_section || {};
      if (bySection[section]) {
        bySection[section] -= 1;
        if (bySection[section] <= 0) delete bySection[section];
      }
    }
    notify(`${page.path} → ${knowledgeClass.toLowerCase().replace(/_/g, " ")}`);
    renderClassify();
  } catch (error) {
    notify(error.message, "warn");
    if (error.code === "PAGE_METADATA_VERSION_STALE") await loadClassify();
  }
}

function classifyKeyHandler(event) {
  if (!$("view-classify").classList.contains("active")) return;
  if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  const index = "12345678".indexOf(event.key);
  if (index >= 0) { event.preventDefault(); assignClass(KNOWLEDGE_CLASSES[index]); return; }
  if (event.key === "j" || event.key === "ArrowDown") { event.preventDefault(); stepClassify(1); }
  if (event.key === "k" || event.key === "ArrowUp") { event.preventDefault(); stepClassify(-1); }
  if (event.key === "s") { event.preventDefault(); stepClassify(1); }
}

/* ---------- Model view ---------- */

async function loadModel(entityId = null) {
  if (!authentication?.token()) return;
  try {
    if (!modelKinds.length) {
      const contracts = await api("/api/v1/model/contracts");
      modelKinds = Object.keys(contracts.kinds || {}).sort();
    }
    $("model-kinds").innerHTML = [
      `<button class="chip chip-filter${modelKind ? "" : " chip-filter--active"}" data-kind="">All</button>`,
      ...modelKinds.map((kind) =>
        `<button class="chip chip-filter${modelKind === kind ? " chip-filter--active" : ""}" data-kind="${esc(kind)}">${esc(kind.toLowerCase())}</button>`),
    ].join("");
    $("model-kinds").querySelectorAll(".chip-filter").forEach((button) => button.addEventListener("click", () => {
      modelKind = button.dataset.kind;
      loadModel();
    }));
    const params = new URLSearchParams({limit: "500", status: "ACTIVE"});
    if (modelKind) params.set("entity_kind", modelKind);
    const [entities, artifacts] = await Promise.all([
      api(`/api/v1/model/entities?${params}`),
      api("/api/v1/model/artifacts?status=DECLARED"),
    ]);
    $("model-count").textContent = `${entities.count} of ${entities.total}`;
    $("model-entities").innerHTML = (entities.entities || []).length ? entities.entities.map((entity) => `
      <button class="entity" data-id="${esc(entity.entity_id)}">
        <span class="chip chip--quiet">${esc(entity.entity_kind.toLowerCase())}</span>
        <strong>${esc(entity.display_name)}</strong>
        <code>${esc(entity.entity_key)}</code>
      </button>`).join("") : `<p class="muted">No entities yet. The model is harvested — seed it with the archmap and compose imports.</p>`;
    $("model-entities").querySelectorAll(".entity").forEach((button) => button.addEventListener("click", () => selectEntity(button.dataset.id)));
    $("model-artifacts").innerHTML = (artifacts.artifacts || []).length ? artifacts.artifacts.map((artifact) => `
      <div class="queue-row"><strong>${esc(artifact.artifact_key || artifact.artifact_id)}</strong><span class="muted">${esc(artifact.generator_name || "")} ${esc(artifact.generator_version || "")}</span></div>`).join("") : `<p class="muted">No generated artifacts declared.</p>`;
    if (entityId) await selectEntity(entityId);
  } catch (error) {
    $("model-entities").textContent = error.message;
  }
}

function renderLinkGroup(title, links, direction) {
  if (!links.length) return "";
  return `<h3>${esc(title)}</h3><div class="list">${links.map((link) => {
    const other = `${link.entity_kind.toLowerCase()} · ${link.entity_key}`;
    const id = direction === "out" ? link.to_entity_id : link.from_entity_id;
    return `<button class="entity entity--link" data-id="${esc(id)}"><span class="chip chip--quiet">${esc(link.relation.toLowerCase())}</span> ${esc(other)}${link.note ? ` <span class="muted">${esc(link.note)}</span>` : ""}</button>`;
  }).join("")}</div>`;
}

async function selectEntity(entityId) {
  try {
    const [entity, status] = await Promise.all([
      api(`/api/v1/model/entities/${entityId}`),
      api(`/api/v1/model/entities/${entityId}/status`).catch(() => null),
    ]);
    syncHashArg("model", entityId);
    $("model-detail-title").textContent = entity.display_name;
    const attributes = Object.entries(entity.attributes || {});
    const currentStatus = status?.current_status || [];
    const generated = status?.generated_artifacts || [];
    $("model-detail").innerHTML = `
      <p><code>${esc(entity.uri)}</code></p>
      <p class="muted">${esc(entity.entity_kind.toLowerCase())} · v${esc(entity.version)} · ${esc(entity.status.toLowerCase())} · updated ${esc(entity.updated_at)}</p>
      ${attributes.length ? `<table class="kv">${attributes.map(([name, value]) =>
        `<tr><th>${esc(name)}</th><td>${esc(typeof value === "object" ? JSON.stringify(value) : value)}</td></tr>`).join("")}</table>` : `<p class="muted">No attributes recorded.</p>`}
      ${renderLinkGroup("Wires out", entity.links_out || [], "out")}
      ${renderLinkGroup("Wires in", entity.links_in || [], "in")}
      ${(entity.pages || []).length ? `<h3>Pages</h3><div class="list">${entity.pages.map((page) =>
        `<div class="queue-row"><span class="chip chip--quiet">${esc(page.relation.toLowerCase())}</span><a href="/?view=authoring&edit=${encodeURIComponent(page.path)}#authoring">${esc(page.title || page.path)}</a><code>${esc(page.path)}</code></div>`).join("")}</div>` : ""}
      ${currentStatus.length ? `<h3>Current status</h3><div class="list">${currentStatus.map((row) =>
        `<div class="queue-row"><span class="chip chip--${(row.outcome || "").toUpperCase() === "NOMINAL" ? "ok" : "quiet"}">${esc((row.outcome || "?").toLowerCase())}</span><strong>${esc(row.observation_kind || "")}</strong><span class="muted">${esc(row.summary || "")} · ${esc(row.observed_at || "")}</span></div>`).join("")}</div>` : ""}
      ${generated.length ? `<h3>Generated artifacts</h3><div class="list">${generated.map((artifact) =>
        `<div class="queue-row"><strong>${esc(artifact.artifact_key)}</strong><span class="chip chip--${artifact.freshness === "FRESH" ? "ok" : "warn"}">${esc((artifact.freshness || "unknown").toLowerCase())}</span></div>`).join("")}</div>` : ""}`;
    $("model-detail").querySelectorAll(".entity--link").forEach((button) => button.addEventListener("click", () => selectEntity(button.dataset.id)));
  } catch (error) {
    $("model-detail").textContent = error.message;
  }
}

/* ---------- Observe view ---------- */

async function loadObserve() {
  if (!authentication?.token()) return;
  try {
    const [coverage, workItems, observations] = await Promise.all([
      api("/api/v1/observe/coverage"),
      api("/api/v1/observe/coverage/work-items?status=OPEN").catch(() => ({items: [], total: 0})),
      api("/api/v1/observations?limit=1000").catch(() => ({observations: []})),
    ]);
    const unwatched = coverage.unwatched_services || [];
    const services = coverage.services || [];
    const byKey = new Map(services.map((service) => [service.entity_key, service]));
    $("observe-cards").innerHTML = [
      ["Unwatched services", unwatched.length],
      ["Rules without description", (coverage.rules_without_description || []).length],
      ["Paging alerts without runbook", (coverage.paging_alerts_without_runbook || []).length],
      ["Unwired rules", (coverage.rules_without_service || []).length],
      ["Monitor rules", coverage.rule_count ?? 0],
      ["Open gap items", workItems.total ?? (workItems.items || []).length],
    ].map(([label, value]) => `<article class="card"><strong>${esc(value)}</strong><span>${esc(label)}</span></article>`).join("");
    $("observe-unwatched").innerHTML = unwatched.length ? unwatched.map((entityKey) => {
      const criticality = byKey.get(entityKey)?.criticality;
      return `<div class="queue-row"><strong>${esc(entityKey)}</strong>${criticality ? `<span class="chip chip--warn">${esc(criticality.toLowerCase())}</span>` : ""}<button class="gap-capture" data-key="${esc(entityKey)}">Capture work</button></div>`;
    }).join("") : `<p class="muted">Every active service has at least one rule watching it.</p>`;
    $("observe-unwatched").querySelectorAll(".gap-capture").forEach((button) => button.addEventListener("click", async () => {
      await captureFinding(`Coverage gap: service ${button.dataset.key} has nothing watching it`, "observe-coverage");
      notify(`Captured ${button.dataset.key} in the Work inbox.`);
    }));
    const gapList = (values) => values.length ? `<ul>${values.map((value) => `<li><code>${esc(value)}</code></li>`).join("")}</ul>` : `<p class="muted">None.</p>`;
    $("observe-gaps").innerHTML = `
      <details><summary>Rules without a plain-English description (${(coverage.rules_without_description || []).length})</summary>${gapList(coverage.rules_without_description || [])}</details>
      <details><summary>Paging alerts without a runbook (${(coverage.paging_alerts_without_runbook || []).length})</summary>${gapList(coverage.paging_alerts_without_runbook || [])}</details>
      <details><summary>Rules not wired to a service (${(coverage.rules_without_service || []).length})</summary>${gapList(coverage.rules_without_service || [])}</details>`;
    $("observe-work-items").innerHTML = (workItems.items || []).length ? workItems.items.slice(0, 30).map((item) =>
      `<div class="queue-row"><span class="chip chip--quiet">${esc((item.gap_kind || "").toLowerCase())}</span><strong>${esc(item.subject_entity_key || item.page_path || "")}</strong><span class="muted">${esc(item.status)} · since ${esc(item.first_seen_at)}</span></div>`).join("") : `<p class="muted">No open coverage work items.</p>`;
    const rows = (observations.observations || []).slice(-30).reverse();
    $("observe-observations").innerHTML = rows.length ? rows.map((row) =>
      `<div class="queue-row"><span class="chip chip--${(row.outcome || "").toUpperCase() === "NOMINAL" ? "ok" : "quiet"}">${esc((row.outcome || "?").toLowerCase())}</span><strong>${esc(row.observation_kind)}</strong><span class="muted">${esc(row.summary || "")} · ${esc(row.observed_at || row.recorded_at || "")}</span></div>`).join("") : `<p class="muted">No observations recorded yet.</p>`;
  } catch (error) {
    $("observe-unwatched").textContent = error.message;
  }
}

/* ---------- Shell wiring ---------- */

function setAuthenticatedControls(enabled) {
  [
    "retry-publication",
    "authoring-propose", "authoring-validate", "authoring-publish",
    "analyze-plan", "validate-plan", "publish-plan",
    "capture-save", "verify-section", "explore-move-stage",
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
  $("explore-breadcrumbs").innerHTML = "";
  $("explore-count").textContent = "";
  explorePath = "";
  selectedPages.clear();
  $("explore-selection").hidden = true;
  $("review-candidates").textContent = "Connect to load.";
  $("verification-requests").textContent = "Connect to load.";
  $("reorg-plans").textContent = "Connect to load.";
  $("reorg-detail").textContent = "No plan selected.";
  $("classify-burndown").textContent = "Connect to load.";
  $("classify-current").textContent = "Connect to load.";
  $("classify-actions").innerHTML = "";
  $("classify-queue").innerHTML = "";
  $("classify-preview").hidden = true;
  $("model-kinds").textContent = "Connect to load.";
  $("model-entities").textContent = "Connect to load.";
  $("model-detail").textContent = "Select an entity to see its attributes, wires, pages and live status.";
  $("model-artifacts").textContent = "Connect to load.";
  $("observe-cards").innerHTML = "";
  $("observe-unwatched").textContent = "Connect to load.";
  $("observe-gaps").textContent = "Connect to load.";
  $("observe-work-items").textContent = "Connect to load.";
  $("observe-observations").textContent = "Connect to load.";
  classifyPages = [];
  classifySummary = null;
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
  $("refresh-classify").addEventListener("click", loadClassify);
  $("classify-class-scope").addEventListener("change", () => {
    classifyScope = $("classify-class-scope").value;
    classifySection = "";
    loadClassify();
  });
  $("refresh-model").addEventListener("click", () => { modelKinds = []; loadModel(); });
  $("refresh-observe").addEventListener("click", loadObserve);
  ["explore-query", "explore-class", "explore-family", "explore-archive", "explore-dated", "explore-descendants"].forEach((id) => $(id).addEventListener("input", () => {
    clearTimeout(exploreFilterTimer);
    // The query toggles between browse and search modes, so refresh the whole
    // listing (breadcrumbs and directories included), not just the page batch.
    exploreFilterTimer = setTimeout(refreshExploreListing, 180);
  }));
  $("explore-more").addEventListener("click", () => loadPageBatch(false).catch((error) => { $("explore-pages").textContent = error.message; }));
  $("explore-move-stage").addEventListener("click", () => stageMovePlan());
  $("explore-selection-clear").addEventListener("click", () => { selectedPages.clear(); renderPages(); });
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
  $("troubleshoot-auth").addEventListener("click", () => {
    $("auth-presentation").hidden = false;
    $("auth-fallback").open = true;
    $("auth-guidance").textContent = "Use a bearer only for managed mode or troubleshooting.";
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
  $("analyze-plan").addEventListener("click", () => planAction("analyze").catch((error) => notify(error.message, "warn")));
  $("validate-plan").addEventListener("click", () => planAction("validate").catch((error) => notify(error.message, "warn")));
  $("publish-plan").addEventListener("click", () => planAction("publish").catch((error) => notify(error.message, "warn")));
  document.addEventListener("keydown", classifyKeyHandler);
  window.addEventListener("hashchange", () => { const parsed = parseHash(); activate(parsed.view, parsed.arg); });
  initialiseNavigation();
  loadProductIdentity();
  startDashboardAuthentication(authentication, loadOverview);
}

globalThis.DocPlaneAuth = {createAuthentication, startDashboardAuthentication, TOKEN_KEY};
