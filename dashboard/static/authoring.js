(() => {
  const $ = (id) => document.getElementById(id);
  let page = null;
  let original = "";
  let change = null;
  let editor = null;

  function auth() { const token = sessionStorage.getItem("docplane-token") || ""; return { Authorization:`Bearer ${token}` }; }
  function key(prefix) { return `${prefix}-${crypto.randomUUID()}`; }
  function value() { return editor ? editor.getValue() : $("authoring-editor").value; }
  function setValue(next) { if (editor) editor.setValue(next || ""); else $("authoring-editor").value = next || ""; }
  async function api(path, options={}) {
    const response = await fetch(path, { ...options, headers:{...auth(),...(options.headers||{})} });
    const payload = await response.json().catch(() => ({raw:response.statusText}));
    if (!response.ok) throw new Error(payload.detail?.upstream?.message || payload.detail?.code || payload.detail || payload.raw || `HTTP ${response.status}`);
    return payload;
  }
  function escape(value) { const node=document.createElement("span"); node.textContent=String(value??""); return node.innerHTML; }
  function status(text) { $("authoring-status").textContent=text; }
  function enable() {
    const changed = page && value() !== original;
    $("authoring-propose").disabled = !changed || !$("authoring-purpose").value.trim();
    $("authoring-validate").disabled = !change || change.status === "PUBLISHED";
    $("authoring-publish").disabled = !change || change.status === "PUBLISHED";
  }
  async function diff() {
    if (!page) return;
    const result = await api("/api/control-plane/authoring/diff", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({before:original,after:value(),path:page.path})});
    $("authoring-diff").textContent = result.changed ? result.diff : "No changes yet.";
    enable();
  }
  async function search() {
    const query=$("authoring-query").value.trim(); if (!query) return;
    const data=await api(`/api/control-plane/authoring/search?q=${encodeURIComponent(query)}&limit=30`);
    $("authoring-results").innerHTML=(data.results||[]).length ? data.results.map((item)=>`<button class="authoring-result" data-id="${escape(item.resource_id)}"><strong>${escape(item.title)}</strong><span>${escape(item.path)}</span></button>`).join("") : `<p class="muted">No matches.</p>`;
    document.querySelectorAll(".authoring-result").forEach((button)=>button.addEventListener("click",()=>selectPage(button.dataset.id)));
  }
  async function selectPage(id) {
    page=await api(`/api/control-plane/authoring/pages/${id}`); original=page.content; change=null; setValue(original);
    $("authoring-page-title").textContent=page.title;
    $("authoring-page-meta").textContent=`${page.path} · ${page.workspace_key} · v${page.version} · ${page.revision}`;
    $("authoring-outline").innerHTML=(page.outline||[]).map((item)=>`<div>${" ".repeat(Math.max(0,item.level-1)*2)}${escape(item.title)} <code>${escape(item.heading_id)}</code></div>`).join("");
    $("authoring-change-id").textContent="not created"; $("authoring-validation").textContent="Not validated."; $("authoring-purpose").value=""; status("Editing revision-bound source");
    await Promise.all([diff(), loadHistory()]); enable();
  }
  async function loadHistory() {
    if (!page) return;
    const data=await api(`/api/control-plane/authoring/pages/${page.resource_id}/history`);
    $("authoring-history").innerHTML=(data.versions||[]).length ? data.versions.map((item)=>`<div><strong>v${escape(item.version)}</strong> ${escape(item.revision)}<br><span class="muted">${escape(item.updated_by)} · ${escape(item.archived_at)}</span></div>`).join("") : `<p class="muted">No prior versions.</p>`;
  }
  async function propose() {
    const purpose=$("authoring-purpose").value.trim();
    change=await api("/api/control-plane/authoring/changes",{method:"POST",headers:{"Content-Type":"application/json","Idempotency-Key":key("change")},body:JSON.stringify({title:`Update ${page.title}`,purpose,workspace_key:page.workspace_key})});
    change=await api(`/api/control-plane/authoring/changes/${change.change_id}/operations`,{method:"POST",headers:{"Content-Type":"application/json","Idempotency-Key":key("replace")},body:JSON.stringify({operation_type:"REPLACE_DOCUMENT",page_resource_id:page.resource_id,expected_revision:page.revision,payload:{content:value(),title:page.title,nav_path:page.nav_path}})});
    $("authoring-change-id").textContent=change.change_id; $("authoring-validation").textContent=JSON.stringify(change,null,2); status("Change created"); enable();
  }
  async function validate() {
    change=await api(`/api/control-plane/authoring/changes/${change.change_id}/validate`,{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});
    $("authoring-validation").textContent=JSON.stringify(change.validation_summary||change,null,2); status(change.validation_summary?.passed ? "Ready to publish" : "Validation failed"); enable();
  }
  async function publish() {
    change=await api(`/api/control-plane/authoring/changes/${change.change_id}/publish`,{method:"POST",headers:{"Content-Type":"application/json","Idempotency-Key":key("publish")},body:"{}"});
    $("authoring-validation").textContent=JSON.stringify(change.publication_receipt||change,null,2); status(change.publication_receipt?.deployment?.status === "FAILED" ? "Database published; site deployment needs retry" : "Published");
    page=await api(`/api/control-plane/authoring/pages/${page.resource_id}`); original=page.content; setValue(original); await Promise.all([diff(),loadHistory()]); enable();
    document.dispatchEvent(new CustomEvent("docplane:published"));
  }
  function mountEditor() {
    if (!window.DocPlaneEditor || editor) return;
    const host=$("authoring-editor-mount"); host.hidden=false; $("authoring-editor").hidden=true;
    editor=window.DocPlaneEditor.mount(host,{doc:$("authoring-editor").value,ariaLabel:"Markdown source",onChange:()=>diff().catch(()=>{})});
  }
  document.addEventListener("DOMContentLoaded",mountEditor,{once:true});
  window.addEventListener("load",mountEditor,{once:true});
  $("authoring-search").addEventListener("click",()=>search().catch((error)=>status(error.message)));
  $("authoring-editor").addEventListener("input",()=>diff().catch(()=>{}));
  $("authoring-purpose").addEventListener("input",enable);
  $("authoring-propose").addEventListener("click",()=>propose().catch((error)=>status(error.message)));
  $("authoring-validate").addEventListener("click",()=>validate().catch((error)=>status(error.message)));
  $("authoring-publish").addEventListener("click",()=>publish().catch((error)=>status(error.message)));
})();
