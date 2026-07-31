import json, re, sys
sys.path.insert(0, "/tmp/docplane-issue44-audit")
from docplane_api_repair import call

PHASE = sys.argv[1]
RUN = sys.argv[2] if len(sys.argv) > 2 else "r1"
do_publish = PHASE == "publish"

landings = json.load(open("/tmp/docplane-lifecycle-policy-audit/landings.json"))
meta = json.load(open("/tmp/docplane-lifecycle-policy-audit/corpus-meta.json"))
bypath = {p["path"]: p for p in meta}

LINK_RE = re.compile(r"\]\(([a-z0-9_.\-]+\.md)\)")

# --- verify: every linked basename actually exists as an active sibling page ---
ok = True
for index_path, d in landings.items():
    prefix = index_path.rsplit("/", 1)[0] + "/"
    linked = set(LINK_RE.findall(d["content"]))
    expected_members = {
        p.rsplit("/", 1)[1] for p in bypath
        if p.startswith(prefix) and p.count("/") == prefix.count("/") and bypath[p]["status"] == "active"
    }
    missing_from_page = expected_members - linked
    extra_in_page = linked - expected_members
    if missing_from_page or extra_in_page:
        ok = False
        print(f"MISMATCH {index_path}: missing={missing_from_page} extra={extra_in_page}")
    else:
        print(f"OK {index_path}: {len(linked)} links match {len(expected_members)} fresh members exactly")
if not ok:
    print("ABORT: member-list mismatch"); sys.exit(1)

title = "docplane#109: 5 section landing pages (incidents/investigations/platform/runbooks/security)" + ("" if do_publish else " (rehearsal)")
purpose = ("Creates the 5 missing index.md landing pages identified in #109, each >=3 "
           "direct members, generator's missing_index threshold. Member lists generated "
           "from a fresh corpus snapshot (verified exact match, not typed from memory). "
           "House style matches the two index.md pages already created in docplane#104 "
           "(operations/follow-ups/, operations/network-fabric/edge-transit/).")

status, d = call("POST", "/api/v1/changes", {"title": title, "purpose": purpose, "workspace_key": "reference"},
                  f"landings-{PHASE}-{RUN}-create")
if status != 201:
    print("FAILED create", status, d); sys.exit(1)
change_id = d["change_id"]
print("change_id:", change_id)

i = 0
for index_path, page in landings.items():
    i += 1
    op = {"operation_type": "CREATE_PAGE",
          "payload": {"path": index_path, "title": page["title"], "nav_path": page["nav_path"],
                      "content": page["content"], "workspace_key": "reference"}}
    status, result = call("POST", f"/api/v1/changes/{change_id}/operations", op, f"landings-{PHASE}-{RUN}-op{i}")
    if status != 201:
        print(f"FAILED op{i} ({index_path})", status, result); sys.exit(1)
    print(f"op{i} (create {index_path}) ok")

status, d = call("POST", f"/api/v1/changes/{change_id}/validate", {}, f"landings-{PHASE}-{RUN}-validate")
print("validate:", status, d.get("status"), "errors=", d.get("validation_summary", {}).get("errors"),
      "accepted=", d.get("validation_summary", {}).get("operations_accepted"), "/",
      d.get("validation_summary", {}).get("operations_checked"))
if d.get("validation_summary", {}).get("errors"):
    print("VALIDATION ERRORS"); print(json.dumps(d, indent=2)); print("change_id:", change_id); sys.exit(1)

if do_publish:
    status, d = call("POST", f"/api/v1/changes/{change_id}/publish", {}, f"landings-{PHASE}-{RUN}-go")
    print("publish:", status, d.get("status"))
    if status in (200, 201):
        pr = d.get("publication_receipt", {})
        print("applied:", pr.get("operations_applied"), "committed:", pr.get("database_transaction"),
              "deploy:", pr.get("deployment", {}).get("status"))
else:
    status, d = call("POST", f"/api/v1/changes/{change_id}/abandon",
                     {"reason": "rehearsal - non-publishing, validated only"}, f"landings-{PHASE}-{RUN}-abandon")
    print("abandon:", status, d.get("status"))

print("change_id:", change_id)
