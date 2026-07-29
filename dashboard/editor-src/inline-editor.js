import Editor from "@toast-ui/editor";
import "@toast-ui/editor/dist/toastui-editor.css";
import "./inline-editor.css";

const TOKEN_KEY = "docplane-token";
const RETURN_SCROLL_KEY = "docplane-inline-return-scroll";

function randomKey(prefix) {
  return `${prefix}-${crypto.randomUUID()}`;
}

function normalizeMarkdown(value) {
  // Line-ending normalization is safe. Trailing spaces are significant Markdown
  // (hard line breaks), so never trim or collapse them for equality decisions.
  return String(value || "").replace(/\r\n/g, "\n");
}

function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = String(value ?? "");
  return node.innerHTML;
}

function errorMessage(payload, status) {
  const detail = payload?.detail || payload || {};
  const upstream = detail.upstream || detail;
  return upstream.message || upstream.remedy || upstream.code || detail.code || `HTTP ${status}`;
}

async function api(path, options = {}) {
  const token = sessionStorage.getItem(TOKEN_KEY) || "";
  const response = await fetch(path, {
    ...options,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {})
    }
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(errorMessage(payload, response.status));
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function dialogShell(className, title) {
  const dialog = document.createElement("dialog");
  dialog.className = `dp-inline-dialog ${className}`;
  dialog.innerHTML = `
    <form method="dialog" class="dp-inline-dialog__panel">
      <header><h2>${escapeHtml(title)}</h2></header>
      <div class="dp-inline-dialog__body"></div>
      <footer class="dp-inline-dialog__actions"></footer>
    </form>`;
  document.body.appendChild(dialog);
  dialog.addEventListener("close", () => dialog.remove(), { once: true });
  return dialog;
}

async function requestToken() {
  const existing = sessionStorage.getItem(TOKEN_KEY);
  if (existing) return existing;

  return new Promise((resolve, reject) => {
    const dialog = dialogShell("dp-inline-auth", "Sign in to edit this page");
    const body = dialog.querySelector(".dp-inline-dialog__body");
    const actions = dialog.querySelector(".dp-inline-dialog__actions");
    body.innerHTML = `
      <p>Reading remains open across the fabric. Editing requires your individual DocPlane contributor token.</p>
      <label class="dp-inline-field">Contributor token
        <input type="password" autocomplete="off" placeholder="dp_…" autofocus>
      </label>
      <p class="dp-inline-dialog__status" role="status"></p>`;
    actions.innerHTML = `<button value="cancel">Cancel</button><button type="button" class="primary">Continue</button>`;
    const input = body.querySelector("input");
    const status = body.querySelector(".dp-inline-dialog__status");
    const submit = actions.querySelector(".primary");

    async function verify() {
      const token = input.value.trim();
      if (!token) return;
      submit.disabled = true;
      status.textContent = "Checking contributor authority…";
      sessionStorage.setItem(TOKEN_KEY, token);
      try {
        await api("/api/control-plane/capabilities");
        dialog.close("ok");
        resolve(token);
      } catch (error) {
        sessionStorage.removeItem(TOKEN_KEY);
        submit.disabled = false;
        status.textContent = error.message;
        input.focus();
      }
    }

    submit.addEventListener("click", verify);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        verify();
      }
    });
    dialog.addEventListener("cancel", () => reject(new Error("Editing cancelled")), { once: true });
    dialog.addEventListener("close", () => {
      if (dialog.returnValue !== "ok") reject(new Error("Editing cancelled"));
    }, { once: true });
    dialog.showModal();
  });
}

async function resolvePage(candidates) {
  let lastNotFound = null;
  for (const candidate of candidates) {
    try {
      const resolved = await api(`/api/control-plane/authoring/lookup?path=${encodeURIComponent(candidate)}`);
      return api(`/api/control-plane/authoring/pages/${resolved.resource_id}`);
    } catch (error) {
      if (error.status === 404) {
        lastNotFound = error;
        continue;
      }
      throw error;
    }
  }
  throw lastNotFound || new Error("No editable DocPlane page matches this rendered page.");
}

function createToolbar(page) {
  const toolbar = document.createElement("div");
  toolbar.className = "dp-inline-toolbar";
  toolbar.innerHTML = `
    <div class="dp-inline-toolbar__identity">
      <strong>Editing</strong>
      <span>${escapeHtml(page.title)}</span>
      <small>v${Number(page.version) || "?"}</small>
    </div>
    <div class="dp-inline-toolbar__status" role="status">Visual editing mode</div>
    <div class="dp-inline-toolbar__actions">
      <button type="button" data-action="visual" class="active">Visual</button>
      <button type="button" data-action="source">Source</button>
      <button type="button" data-action="cancel">Cancel</button>
      <button type="button" data-action="publish" class="primary">Publish</button>
    </div>`;
  return toolbar;
}

async function publishDialog(page, before, after) {
  const diff = await api("/api/control-plane/authoring/diff", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ before, after, path: page.path })
  });

  return new Promise((resolve) => {
    const dialog = dialogShell("dp-inline-publish", "Review and publish");
    const body = dialog.querySelector(".dp-inline-dialog__body");
    const actions = dialog.querySelector(".dp-inline-dialog__actions");
    body.innerHTML = `
      <p><strong>${Number(diff.line_count) || 0}</strong> changed diff lines. Publication creates a normal audited DocPlane change and certified release.</p>
      <label class="dp-inline-field">Purpose
        <textarea rows="3" placeholder="Why this change is useful or correct"></textarea>
      </label>
      <details ${diff.line_count <= 80 ? "open" : ""}>
        <summary>Source diff</summary>
        <pre class="dp-inline-diff"></pre>
      </details>
      <p class="dp-inline-dialog__status" role="status"></p>`;
    body.querySelector(".dp-inline-diff").textContent = diff.changed ? diff.diff : "No source changes.";
    actions.innerHTML = `<button value="cancel">Cancel</button><button type="button" class="primary" disabled>Publish</button>`;
    const purpose = body.querySelector("textarea");
    const publish = actions.querySelector(".primary");
    purpose.addEventListener("input", () => { publish.disabled = !purpose.value.trim() || !diff.changed; });
    publish.addEventListener("click", () => {
      const value = purpose.value.trim();
      dialog.close("publish");
      resolve(value);
    });
    dialog.addEventListener("close", () => {
      if (dialog.returnValue !== "publish") resolve(null);
    }, { once: true });
    dialog.showModal();
    purpose.focus();
  });
}

function staleDetail(error) {
  const detail = error.payload?.detail?.upstream || error.payload?.detail || {};
  const validation = detail.validation || {};
  const stale = (validation.errors || []).find((item) => item.code === "PAGE_REVISION_STALE");
  return stale || detail;
}

async function start(options = {}) {
  const article = document.querySelector(options.articleSelector || ".md-content__inner");
  if (!article || article.dataset.docplaneEditing === "true") return;

  const pencil = options.trigger;
  if (pencil) pencil.disabled = true;
  try {
    await requestToken();
    const page = await resolvePage(options.candidates || []);
    const originalMarkdown = page.content || "";
    const originalScroll = window.scrollY;
    const originalNodes = document.createDocumentFragment();
    while (article.firstChild) originalNodes.appendChild(article.firstChild);

    article.dataset.docplaneEditing = "true";
    article.classList.add("dp-inline-editing");
    const toolbar = createToolbar(page);
    const notice = document.createElement("div");
    notice.className = "dp-inline-notice";
    notice.hidden = true;
    const mount = document.createElement("div");
    mount.className = "dp-inline-editor";
    article.append(toolbar, notice, mount);

    const editor = new Editor({
      el: mount,
      height: "auto",
      minHeight: "34rem",
      initialEditType: "wysiwyg",
      initialValue: originalMarkdown,
      previewStyle: "tab",
      hideModeSwitch: true,
      usageStatistics: false,
      toolbarItems: [
        ["heading", "bold", "italic", "strike"],
        ["hr", "quote"],
        ["ul", "ol", "task"],
        ["table", "link"],
        ["code", "codeblock"]
      ]
    });

    const initialRoundTrip = editor.getMarkdown();
    if (normalizeMarkdown(initialRoundTrip) !== normalizeMarkdown(originalMarkdown)) {
      notice.hidden = false;
      notice.textContent = "This page contains Markdown that the visual editor may normalise. Review the Source view and the publication diff before publishing.";
    }

    const status = toolbar.querySelector(".dp-inline-toolbar__status");
    const visual = toolbar.querySelector('[data-action="visual"]');
    const source = toolbar.querySelector('[data-action="source"]');
    const cancel = toolbar.querySelector('[data-action="cancel"]');
    const publish = toolbar.querySelector('[data-action="publish"]');

    function mode(next) {
      editor.changeMode(next, true);
      visual.classList.toggle("active", next === "wysiwyg");
      source.classList.toggle("active", next === "markdown");
      status.textContent = next === "wysiwyg" ? "Visual editing mode" : "Markdown source mode";
      editor.focus();
    }

    function restore() {
      editor.destroy();
      article.replaceChildren(originalNodes);
      article.classList.remove("dp-inline-editing");
      delete article.dataset.docplaneEditing;
      window.scrollTo({ top: originalScroll });
      if (pencil) pencil.disabled = false;
    }

    visual.addEventListener("click", () => mode("wysiwyg"));
    source.addEventListener("click", () => mode("markdown"));
    cancel.addEventListener("click", () => {
      const changed = normalizeMarkdown(editor.getMarkdown()) !== normalizeMarkdown(originalMarkdown);
      if (!changed || window.confirm("Discard your unpublished changes?")) restore();
    });

    publish.addEventListener("click", async () => {
      const content = editor.getMarkdown();
      if (normalizeMarkdown(content) === normalizeMarkdown(originalMarkdown)) {
        status.textContent = "No changes to publish";
        return;
      }
      publish.disabled = true;
      try {
        const purpose = await publishDialog(page, originalMarkdown, content);
        if (!purpose) return;
        status.textContent = "Publishing and certifying…";
        const change = await api(`/api/control-plane/authoring/pages/${page.resource_id}/replace`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": randomKey("inline-replace")
          },
          body: JSON.stringify({ expected_revision: page.revision, content, purpose })
        });
        const deployment = change.publication_receipt?.deployment;
        if (deployment?.status === "FAILED") {
          status.textContent = "Database published; rendered-site deployment requires retry";
          return;
        }
        status.textContent = "Published";
        sessionStorage.setItem(RETURN_SCROLL_KEY, String(originalScroll));
        window.location.reload();
      } catch (error) {
        if (error.status === 409) {
          const stale = staleDetail(error);
          status.textContent = `This page changed while you were editing (current v${stale.current_version || "?"}). Your work is still here; switch to Source, copy it, then reload and rebase.`;
          notice.hidden = false;
          notice.textContent = stale.remedy || "Reload the current page and reapply your changes to its latest revision.";
        } else {
          status.textContent = error.message;
        }
      } finally {
        publish.disabled = false;
      }
    });

    window.scrollTo({ top: Math.max(0, article.getBoundingClientRect().top + window.scrollY - 80), behavior: "smooth" });
    editor.focus();
  } catch (error) {
    if (error.message !== "Editing cancelled") window.alert(error.message);
    if (pencil) pencil.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const saved = sessionStorage.getItem(RETURN_SCROLL_KEY);
  if (saved !== null) {
    sessionStorage.removeItem(RETURN_SCROLL_KEY);
    requestAnimationFrame(() => window.scrollTo({ top: Number(saved) || 0 }));
  }
});

window.DocPlaneInlineEditor = Object.freeze({ start });
