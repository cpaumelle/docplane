(() => {
  const authoring = {
    page: null,
    original: "",
    change: null,
    proposedContent: null,
    editor: null,
    diffTimer: null,
    diffSequence: 0,
  };

  const byId = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");

  function token() {
    return byId("token")?.value.trim() || "";
  }

  function editorValue() {
    return authoring.editor?.getValue() ?? byId("authoring-editor")?.value ?? "";
  }

  function setEditorValue(value) {
    const next = value || "";
    const textarea = byId("authoring-editor");
    if (textarea) textarea.value = next;
    authoring.editor?.setValue(next);
  }

  function requestHeaders(idempotencyKey = null) {
    const result = { Accept: "application/json" };
    if (token()) result.Authorization = `Bearer ${token()}`;
    if (idempotencyKey) result["Idempotency-Key"] = idempotencyKey;
    return result;
  }

  async function authoringRequest(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { ...requestHeaders(options.idempotencyKey), ...(options.headers || {}) },
    });
    let body = null;
    try { body = await response.json(); } catch (_) { body = null; }
    if (!response.ok) {
      const detail = body?.detail || body || {};
      throw new Error(detail.code || detail.upstream?.detail?.code || `HTTP_${response.status}`);
    }
    return body;
  }

  function setAuthoringStatus(message, kind = "") {
    const node = byId("authoring-status");
    if (!node) return;
    node.textContent = message;
    node.className = `status ${kind}`.trim();
  }

  function resetProposal(message = "not created") {
    authoring.change = null;
    authoring.proposedContent = null;
    const changeId = byId("authoring-change-id");
    if (changeId) changeId.textContent = message;
    byId("authoring-validate").disabled = true;
    byId("authoring-submit").disabled = true;
  }

  function renderSearchResults(results) {
    const node = byId("authoring-results");
    node.innerHTML = (results || []).map((item) => `
      <button class="plan-row authoring-result" data-resource-id="${escapeHtml(item.resource_id)}">
        <strong>${escapeHtml(item.title)}</strong>
        <span>${escapeHtml(item.path)} · revision ${escapeHtml(item.revision)}</span>
      </button>`).join("") || '<p class="muted">No matching pages.</p>';
    node.querySelectorAll(".authoring-result").forEach((button) => {
      button.addEventListener("click", () => loadPage(button.dataset.resourceId));
    });
  }

  async function searchPages() {
    const query = byId("authoring-query").value.trim();
    if (query.length < 2) {
      setAuthoringStatus("Enter at least two characters", "error");
      return;
    }
    try {
      const body = await authoringRequest(`/api/control-plane/authoring/search?q=${encodeURIComponent(query)}&limit=50`);
      renderSearchResults(body.results);
      setAuthoringStatus(`${body.count} page${body.count === 1 ? "" : "s"} found`, "ok");
    } catch (error) {
      setAuthoringStatus(`Search refused · ${error.message}`, "error");
    }
  }

  function renderOutline(outline) {
    const node = byId("authoring-outline");
    node.innerHTML = (outline || []).map((heading) => `
      <div class="outline-row level-${Number(heading.level || 1)}">
        <code>${escapeHtml(heading.heading_id || "implicit")}</code>
        <span>${escapeHtml(heading.title)}</span>
      </div>`).join("") || '<p class="muted">No headings detected.</p>';
  }

  async function loadPage(resourceId) {
    try {
      const page = await authoringRequest(`/api/control-plane/authoring/pages/${encodeURIComponent(resourceId)}`);
      authoring.page = page;
      authoring.original = page.content || "";
      resetProposal();
      byId("authoring-page-title").textContent = page.title;
      byId("authoring-page-meta").textContent = `${page.path} · revision ${page.revision} · v${page.version ?? "?"}`;
      setEditorValue(authoring.original);
      byId("authoring-purpose").value = `Correct or improve ${page.title}`;
      byId("authoring-diff").textContent = "No changes yet.";
      byId("authoring-validation").textContent = "Not validated.";
      renderOutline(page.outline);
      byId("authoring-propose").disabled = false;
      setAuthoringStatus("Exact edit context loaded", "ok");
      authoring.editor?.focus();
    } catch (error) {
      setAuthoringStatus(`Page load refused · ${error.message}`, "error");
    }
  }

  async function refreshDiff() {
    if (!authoring.page) return;
    const sequence = ++authoring.diffSequence;
    try {
      const body = await authoringRequest("/api/control-plane/authoring/diff", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          before: authoring.original,
          after: editorValue(),
          path: authoring.page.path,
        }),
      });
      if (sequence !== authoring.diffSequence) return;
      byId("authoring-diff").textContent = body.changed ? body.diff : "No changes.";
    } catch (error) {
      if (sequence === authoring.diffSequence) {
        setAuthoringStatus(`Diff failed · ${error.message}`, "error");
      }
    }
  }

  function scheduleDiff() {
    window.clearTimeout(authoring.diffTimer);
    authoring.diffTimer = window.setTimeout(refreshDiff, 180);
    if (authoring.change && editorValue() !== authoring.proposedContent) {
      resetProposal("stale — create a new proposal");
      setAuthoringStatus("Editor changed after proposal creation", "error");
    }
  }

  async function proposeChange() {
    if (!authoring.page) return;
    const content = editorValue();
    const purpose = byId("authoring-purpose").value.trim();
    if (content === authoring.original) {
      setAuthoringStatus("No content change to propose", "error");
      return;
    }
    if (!purpose) {
      setAuthoringStatus("A purpose is required", "error");
      return;
    }
    try {
      const change = await authoringRequest("/api/control-plane/authoring/changes", {
        method: "POST",
        idempotencyKey: `human-change-${crypto.randomUUID()}`,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: `Edit ${authoring.page.title}`,
          purpose,
          workspace_key: "reference",
        }),
      });
      const operation = await authoringRequest(`/api/control-plane/authoring/changes/${change.change_id}/operations`, {
        method: "POST",
        idempotencyKey: `human-operation-${crypto.randomUUID()}`,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          operation_type: "REPLACE_DOCUMENT",
          page_resource_id: authoring.page.resource_id,
          expected_revision: authoring.page.revision,
          payload: { content },
        }),
      });
      authoring.change = operation;
      authoring.proposedContent = content;
      byId("authoring-change-id").textContent = operation.change_id;
      byId("authoring-validate").disabled = false;
      byId("authoring-submit").disabled = true;
      setAuthoringStatus("Draft change created; validate before submission", "ok");
    } catch (error) {
      setAuthoringStatus(`Proposal refused · ${error.message}`, "error");
    }
  }

  async function changeAction(action) {
    if (!authoring.change) return;
    try {
      const body = await authoringRequest(`/api/control-plane/authoring/changes/${authoring.change.change_id}/${action}`, {
        method: "POST",
        idempotencyKey: `human-${action}-${crypto.randomUUID()}`,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(action === "submit" ? { note: byId("authoring-purpose").value.trim() } : {}),
      });
      authoring.change = body;
      byId("authoring-validation").textContent = JSON.stringify({
        status: body.status,
        validation_summary: body.validation_summary,
        preview_receipt: body.preview_receipt,
      }, null, 2);
      byId("authoring-submit").disabled = body.status !== "VALIDATED";
      byId("authoring-validate").disabled = body.status !== "DRAFT";
      setAuthoringStatus(`${action} complete · ${body.status}`, "ok");
    } catch (error) {
      setAuthoringStatus(`${action} refused · ${error.message}`, "error");
    }
  }

  function mountEditor() {
    const textarea = byId("authoring-editor");
    const host = byId("authoring-editor-mount");
    if (!textarea || !host || !window.DocPlaneEditor) return;
    authoring.editor = window.DocPlaneEditor.mount(host, {
      doc: textarea.value,
      ariaLabel: textarea.getAttribute("aria-label") || "Markdown source",
      onChange(value) {
        if (textarea.value === value) return;
        textarea.value = value;
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
      },
    });
    host.hidden = false;
    textarea.hidden = true;
  }

  function bind() {
    byId("authoring-search")?.addEventListener("click", searchPages);
    byId("authoring-query")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") searchPages();
    });
    byId("authoring-editor")?.addEventListener("input", scheduleDiff);
    byId("authoring-propose")?.addEventListener("click", proposeChange);
    byId("authoring-validate")?.addEventListener("click", () => changeAction("validate"));
    byId("authoring-submit")?.addEventListener("click", () => changeAction("submit"));
    mountEditor();
  }

  window.addEventListener("DOMContentLoaded", bind);
})();
