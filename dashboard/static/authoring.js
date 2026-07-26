(() => {
  "use strict";

  const state = {
    token: "",
    page: null,
    fullContent: "",
    originalEditContent: "",
    scope: "document",
    headingId: "",
    editor: null,
    preview: null,
    change: null,
    keys: {
      change: null,
      operation: null
    }
  };

  const byId = (id) => document.getElementById(id);
  const tokenInput = byId("author-token");
  const status = byId("author-status");
  const queryInput = byId("page-query");
  const searchResults = byId("search-results");
  const scopeSelect = byId("edit-scope");
  const sectionSelect = byId("section-id");
  const previewButton = byId("preview-change");
  const createButton = byId("create-change");
  const submitButton = byId("submit-change");
  const dirtyState = byId("dirty-state");
  const previewFrame = byId("preview-frame");

  function setStatus(message, kind = "") {
    status.textContent = message;
    status.className = `status ${kind}`.trim();
  }

  function errorMessage(payload, fallback) {
    const detail = payload && payload.detail;
    if (detail && typeof detail === "object") {
      const upstream = detail.upstream && detail.upstream.detail;
      return upstream?.message || upstream?.code || detail.message || detail.code || fallback;
    }
    return fallback;
  }

  async function api(path, options = {}) {
    if (!state.token) {
      throw new Error("Connect with a named human token first.");
    }
    const headers = {
      Accept: "application/json",
      Authorization: `Bearer ${state.token}`,
      ...(options.headers || {})
    };
    if (options.body !== undefined) {
      headers["Content-Type"] = "application/json";
    }
    const response = await fetch(path, {
      method: options.method || "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body)
    });
    let payload = null;
    if (response.status !== 204) {
      try {
        payload = await response.json();
      } catch (_error) {
        payload = null;
      }
    }
    if (!response.ok) {
      throw new Error(errorMessage(payload, `Request failed (${response.status})`));
    }
    return payload;
  }

  function randomKey(prefix) {
    const value = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    return `${prefix}-${value}`;
  }

  function resetProposal() {
    state.preview = null;
    state.change = null;
    state.keys.change = null;
    state.keys.operation = null;
    createButton.disabled = true;
    submitButton.disabled = true;
    byId("change-receipt").textContent = "No proposal receipt yet.";
  }

  function updateDirtyState() {
    if (!state.editor || !state.page) {
      dirtyState.textContent = "Clean";
      return;
    }
    const dirty = state.editor.getValue() !== state.originalEditContent;
    dirtyState.textContent = dirty ? "Unsaved proposal" : "Clean";
    dirtyState.className = `status ${dirty ? "warning" : ""}`.trim();
    previewButton.disabled = !dirty;
  }

  function editorChanged() {
    resetProposal();
    updateDirtyState();
  }

  function lineBlocks(content) {
    return content.match(/[^\n]*\n|[^\n]+$/g) || [];
  }

  function sectionSource(page, headingId) {
    const section = (page.outline || []).find((item) => item.heading_id === headingId);
    if (!section) {
      return "";
    }
    return lineBlocks(page.content).slice(section.start_line - 1, section.end_line).join("");
  }

  function setEditorSource(content, context) {
    state.originalEditContent = content;
    state.editor.setValue(content);
    byId("editor-context").textContent = context;
    resetProposal();
    updateDirtyState();
    state.editor.focus();
  }

  function pageMetadata(page) {
    const target = byId("page-metadata");
    target.replaceChildren();
    const entries = [
      ["Resource", page.resource_id],
      ["Revision", page.revision],
      ["Path", page.path]
    ];
    for (const [label, value] of entries) {
      const row = document.createElement("div");
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = label;
      dd.textContent = value || "None";
      row.append(dt, dd);
      target.append(row);
    }
  }

  function populateSections(page) {
    sectionSelect.replaceChildren();
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Select an explicit-ID section";
    sectionSelect.append(placeholder);
    for (const item of page.outline || []) {
      if (!item.explicit_id) continue;
      const option = document.createElement("option");
      option.value = item.heading_id;
      option.textContent = `${"—".repeat(Math.max(0, item.level - 1))} ${item.title}`.trim();
      sectionSelect.append(option);
    }
    const hasSections = sectionSelect.options.length > 1;
    scopeSelect.disabled = false;
    scopeSelect.querySelector('option[value="section"]').disabled = !hasSections;
    sectionSelect.disabled = true;
  }

  async function loadPage(resourceId) {
    setStatus("Loading page…");
    const page = await api(`/api/control-plane/authoring/pages/${resourceId}`);
    state.page = page;
    state.fullContent = page.content || "";
    state.scope = "document";
    state.headingId = "";
    scopeSelect.value = "document";
    pageMetadata(page);
    populateSections(page);
    byId("editor-title").textContent = page.title;
    byId("change-title").value = `Update ${page.title}`;
    setEditorSource(state.fullContent, `${page.path} · revision ${page.revision}`);
    setStatus("Connected", "ok");
  }

  function renderSearchResults(results) {
    searchResults.replaceChildren();
    if (!results.length) {
      searchResults.textContent = "No matching canonical pages.";
      searchResults.className = "search-results muted";
      return;
    }
    searchResults.className = "search-results";
    for (const item of results) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "search-result";
      const title = document.createElement("strong");
      const path = document.createElement("span");
      title.textContent = item.title;
      path.textContent = item.path;
      button.append(title, path);
      button.addEventListener("click", () => {
        loadPage(item.resource_id).catch((error) => setStatus(error.message, "error"));
      });
      searchResults.append(button);
    }
  }

  async function searchPages() {
    const query = queryInput.value.trim();
    if (query.length < 2) {
      setStatus("Enter at least two characters.", "warning");
      return;
    }
    setStatus("Searching…");
    const body = await api(`/api/control-plane/authoring/search?q=${encodeURIComponent(query)}`);
    renderSearchResults(body.results || []);
    setStatus(`Found ${body.count || 0} result(s)`, "ok");
  }

  function renderDiagnostics(items) {
    const target = byId("diagnostics");
    target.replaceChildren();
    if (!items.length) {
      target.textContent = "No source diagnostics.";
      target.className = "evidence-list muted";
      return;
    }
    target.className = "evidence-list";
    for (const item of items) {
      const card = document.createElement("article");
      card.className = `evidence-item ${item.severity}`;
      const heading = document.createElement("strong");
      const message = document.createElement("div");
      heading.textContent = `${item.severity.toUpperCase()} · ${item.code}${item.line ? ` · line ${item.line}` : ""}`;
      message.textContent = item.message;
      card.append(heading, message);
      target.append(card);
    }
  }

  function renderSemantic(items) {
    const target = byId("semantic-diff");
    target.replaceChildren();
    const changed = items.filter((item) => item.state !== "unchanged");
    if (!changed.length) {
      target.textContent = "No section-level changes.";
      target.className = "evidence-list muted";
      return;
    }
    target.className = "evidence-list";
    for (const item of changed) {
      const card = document.createElement("article");
      card.className = `evidence-item ${item.state}`;
      const heading = document.createElement("strong");
      const detail = document.createElement("div");
      heading.textContent = `${item.state.toUpperCase()} · ${item.heading_id}`;
      detail.textContent = item.title_after || item.title_before || "Untitled section";
      card.append(heading, detail);
      target.append(card);
    }
  }

  function previewDocument(html) {
    const policy = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'";
    previewFrame.srcdoc = `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${policy}"><style>body{font-family:system-ui,sans-serif;line-height:1.55;padding:18px;max-width:960px;margin:auto;color:#101828}pre,code{font-family:ui-monospace,monospace}pre{overflow:auto;padding:12px;background:#f7f8fa}table{border-collapse:collapse}th,td{border:1px solid #d0d5dd;padding:6px 8px}.admonition{border-left:4px solid #315efb;padding:8px 12px;background:#f5f7ff}</style></head><body>${html}</body></html>`;
  }

  function showResult(name) {
    document.querySelectorAll(".result-tab").forEach((button) => {
      button.classList.toggle("active", button.dataset.result === name);
    });
    document.querySelectorAll(".result-panel").forEach((panel) => {
      panel.classList.toggle("active", panel.id === `result-${name}`);
    });
  }

  async function previewChange() {
    if (!state.page) return;
    setStatus("Rendering preview…");
    const body = await api("/api/control-plane/authoring/preview", {
      method: "POST",
      body: {
        page_resource_id: state.page.resource_id,
        expected_revision: state.page.revision,
        scope: state.scope,
        heading_id: state.scope === "section" ? state.headingId : null,
        content: state.editor.getValue()
      }
    });
    state.preview = body;
    state.change = null;
    state.keys.change = null;
    state.keys.operation = null;
    previewDocument(body.rendered_html || "");
    byId("raw-diff").textContent = body.raw_diff || "No raw changes.";
    renderSemantic(body.semantic_diff || []);
    renderDiagnostics(body.diagnostics || []);
    const hasErrors = (body.diagnostics || []).some((item) => item.severity === "error");
    createButton.disabled = hasErrors;
    submitButton.disabled = true;
    showResult(hasErrors ? "diagnostics" : "preview");
    setStatus(hasErrors ? "Preview contains blocking diagnostics" : "Preview ready", hasErrors ? "error" : "ok");
  }

  async function createValidatedProposal() {
    if (!state.preview) return;
    const title = byId("change-title").value.trim();
    const purpose = byId("change-purpose").value.trim();
    if (!title || !purpose) {
      setStatus("Proposal title and purpose are required.", "warning");
      return;
    }
    if (!state.preview.workspace_key) {
      setStatus("Preview receipt is missing workspace authority.", "error");
      return;
    }
    setStatus("Creating governed proposal…");
    state.keys.change ||= randomKey("human-change");
    state.keys.operation ||= randomKey("human-operation");

    let change = state.change;
    if (!change) {
      change = await api("/api/control-plane/authoring/changes", {
        method: "POST",
        headers: { "Idempotency-Key": state.keys.change },
        body: {
          title,
          purpose,
          workspace_key: state.preview.workspace_key,
          base_state_identity: state.page.revision
        }
      });
      state.change = change;
    }

    change = await api(`/api/control-plane/authoring/changes/${change.change_id}/operations`, {
      method: "POST",
      headers: { "Idempotency-Key": state.keys.operation },
      body: state.preview.operation
    });
    change = await api(`/api/control-plane/authoring/changes/${change.change_id}/validate`, {
      method: "POST",
      body: null
    });
    state.change = change;
    byId("change-receipt").textContent = JSON.stringify(change, null, 2);
    submitButton.disabled = change.status !== "VALIDATED";
    showResult("receipt");
    setStatus(change.status === "VALIDATED" ? "Proposal validated" : `Proposal status: ${change.status}`, change.status === "VALIDATED" ? "ok" : "warning");
  }

  async function submitProposal() {
    if (!state.change) return;
    setStatus("Submitting for review…");
    const change = await api(`/api/control-plane/authoring/changes/${state.change.change_id}/submit`, {
      method: "POST",
      body: { note: byId("change-purpose").value.trim() }
    });
    state.change = change;
    byId("change-receipt").textContent = JSON.stringify(change, null, 2);
    submitButton.disabled = true;
    showResult("receipt");
    setStatus("Submitted for review", "ok");
  }

  function scopeChanged() {
    if (!state.page) return;
    state.scope = scopeSelect.value;
    if (state.scope === "document") {
      sectionSelect.disabled = true;
      state.headingId = "";
      setEditorSource(state.fullContent, `${state.page.path} · whole document · revision ${state.page.revision}`);
      return;
    }
    sectionSelect.disabled = false;
    if (sectionSelect.value) {
      sectionChanged();
    } else {
      setEditorSource("", "Select an explicit-ID section.");
      previewButton.disabled = true;
    }
  }

  function sectionChanged() {
    state.headingId = sectionSelect.value;
    if (!state.headingId || !state.page) {
      previewButton.disabled = true;
      return;
    }
    const source = sectionSource(state.page, state.headingId);
    setEditorSource(source, `${state.page.path} · section #${state.headingId} · revision ${state.page.revision}`);
  }

  function connect() {
    const token = tokenInput.value.trim();
    if (!token) {
      setStatus("Enter a named human token.", "warning");
      return;
    }
    state.token = token;
    tokenInput.value = "";
    setStatus("Connected", "ok");
    queryInput.focus();
  }

  function initialize() {
    if (!window.DocPlaneEditor) {
      setStatus("Editor bundle failed to load.", "error");
      return;
    }
    state.editor = window.DocPlaneEditor.mount(byId("markdown-editor"), {
      doc: "",
      onChange: editorChanged
    });

    byId("author-connect").addEventListener("click", connect);
    tokenInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") connect();
    });
    byId("search-pages").addEventListener("click", () => searchPages().catch((error) => setStatus(error.message, "error")));
    queryInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") searchPages().catch((error) => setStatus(error.message, "error"));
    });
    scopeSelect.addEventListener("change", scopeChanged);
    sectionSelect.addEventListener("change", sectionChanged);
    previewButton.addEventListener("click", () => previewChange().catch((error) => setStatus(error.message, "error")));
    createButton.addEventListener("click", () => createValidatedProposal().catch((error) => setStatus(error.message, "error")));
    submitButton.addEventListener("click", () => submitProposal().catch((error) => setStatus(error.message, "error")));
    document.querySelectorAll(".result-tab").forEach((button) => {
      button.addEventListener("click", () => showResult(button.dataset.result));
    });
  }

  window.addEventListener("DOMContentLoaded", initialize);
})();
