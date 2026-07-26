import { basicSetup, EditorView } from "codemirror";
import { markdown } from "@codemirror/lang-markdown";

const docPlaneTheme = EditorView.theme({
  "&": {
    height: "100%",
    fontSize: "14px",
    backgroundColor: "#ffffff"
  },
  ".cm-content": {
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
    lineHeight: "1.55",
    padding: "14px 0"
  },
  ".cm-scroller": {
    overflow: "auto"
  },
  ".cm-gutters": {
    backgroundColor: "#f7f8fa",
    borderRight: "1px solid #dfe3e8"
  },
  "&.cm-focused": {
    outline: "2px solid #315efb",
    outlineOffset: "-2px"
  }
});

function mount(element, options = {}) {
  if (!element) {
    throw new Error("DocPlane editor mount element is required");
  }
  const onChange = typeof options.onChange === "function" ? options.onChange : () => {};
  const view = new EditorView({
    doc: options.doc || "",
    extensions: [
      basicSetup,
      markdown(),
      docPlaneTheme,
      EditorView.lineWrapping,
      EditorView.updateListener.of((update) => {
        if (update.docChanged) {
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
      view.dispatch({
        changes: { from: 0, to: view.state.doc.length, insert: next }
      });
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
