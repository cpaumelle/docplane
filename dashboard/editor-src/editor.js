import { basicSetup, EditorView } from "codemirror";
import { markdown } from "@codemirror/lang-markdown";

const docPlaneTheme = EditorView.theme({
  "&": {
    height: "100%",
    minHeight: "34rem",
    backgroundColor: "#0b0f14",
    color: "#e7edf5",
    border: "1px solid #273342",
    borderRadius: "9px"
  },
  ".cm-content": {
    caretColor: "#7cc4ff",
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    lineHeight: "1.55",
    padding: "14px 0"
  },
  ".cm-cursor, .cm-dropCursor": {
    borderLeftColor: "#7cc4ff"
  },
  ".cm-scroller": {
    overflow: "auto"
  },
  ".cm-gutters": {
    backgroundColor: "#121821",
    color: "#76869a",
    borderRight: "1px solid #273342"
  },
  ".cm-activeLine, .cm-activeLineGutter": {
    backgroundColor: "#17202b"
  },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground, ::selection": {
    backgroundColor: "#29445d"
  },
  "&.cm-focused": {
    outline: "2px solid #7cc4ff",
    outlineOffset: "-2px"
  }
}, { dark: true });

function mount(element, options = {}) {
  if (!element) {
    throw new Error("DocPlane editor mount element is required");
  }

  const onChange = typeof options.onChange === "function" ? options.onChange : () => {};
  let suppressChange = false;
  const view = new EditorView({
    doc: options.doc || "",
    extensions: [
      basicSetup,
      markdown(),
      docPlaneTheme,
      EditorView.lineWrapping,
      EditorView.contentAttributes.of({
        "aria-label": options.ariaLabel || "Markdown source"
      }),
      EditorView.updateListener.of((update) => {
        if (update.docChanged && !suppressChange) {
          onChange(update.state.doc.toString());
        }
      })
    ],
    parent: element
  });

  return {
    getValue() {
      return view.state.doc.toString();
    },
    setValue(value) {
      const next = value || "";
      if (next === view.state.doc.toString()) return;
      suppressChange = true;
      try {
        view.dispatch({
          changes: { from: 0, to: view.state.doc.length, insert: next }
        });
      } finally {
        suppressChange = false;
      }
    },
    focus() {
      view.focus();
    },
    destroy() {
      view.destroy();
    }
  };
}

window.DocPlaneEditor = Object.freeze({ mount });
