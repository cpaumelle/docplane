import { basicSetup } from "codemirror";
import { markdown } from "@codemirror/lang-markdown";
import { EditorView, keymap } from "@codemirror/view";

function mount() {
  const textarea = document.getElementById("authoring-editor");
  if (!textarea || textarea.dataset.codemirrorMounted === "true") return;

  textarea.dataset.codemirrorMounted = "true";
  const host = document.createElement("div");
  host.className = "codemirror-host";
  textarea.before(host);
  textarea.hidden = true;

  let internalWrite = false;
  const view = new EditorView({
    parent: host,
    doc: textarea.value,
    extensions: [
      basicSetup,
      markdown(),
      EditorView.lineWrapping,
      EditorView.updateListener.of((update) => {
        if (!update.docChanged) return;
        internalWrite = true;
        textarea.value = update.state.doc.toString();
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
        internalWrite = false;
      }),
      EditorView.theme({
        "&": { minHeight: "38rem", fontSize: "0.9rem" },
        ".cm-scroller": {
          minHeight: "38rem",
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
          lineHeight: "1.5"
        },
        ".cm-content": { padding: "0.8rem" },
        "&.cm-focused": { outline: "2px solid var(--accent, #3867d6)" }
      })
    ]
  });

  const syncExternalValue = () => {
    if (internalWrite) return;
    const current = view.state.doc.toString();
    if (textarea.value === current) return;
    view.dispatch({ changes: { from: 0, to: current.length, insert: textarea.value } });
  };

  const observer = new MutationObserver(syncExternalValue);
  observer.observe(textarea, { attributes: true, childList: true, subtree: true });
  window.setInterval(syncExternalValue, 250);

  window.DocPlaneSourceEditor = {
    view,
    focus: () => view.focus(),
    getValue: () => view.state.doc.toString(),
    setValue: (value) => {
      textarea.value = String(value ?? "");
      syncExternalValue();
    }
  };
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mount, { once: true });
} else {
  mount();
}
