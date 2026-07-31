import json, sys
sys.path.insert(0, "/tmp/docplane-issue44-audit")
from docplane_api_repair import call

PHASE = sys.argv[1]
RUN = sys.argv[2] if len(sys.argv) > 2 else "r1"
do_publish = PHASE == "publish"

new_page = json.load(open("/tmp/docplane-lifecycle-policy-audit/roadmap-split-new-page.json"))["new_page_body"]
modified = json.load(open("/tmp/docplane-lifecycle-policy-audit/roadmap-split-modified-page.json"))["modified_body"]

RID_ROADMAP = "6d91f9ba-eba5-5bea-9b30-7afed9c603e6"
RID_INDEX = "fc49e984-f924-5a78-8906-9ad2699ef884"

title = "docplane#108: invariant-roadmap.md durable/planning split" + ("" if do_publish else " (rehearsal)")
purpose = ("Splits operations/invariant-roadmap.md's durable methodology (when to add an "
           "invariant, non-goals, design principles, maturity model) into a new canonical "
           "page control-plane/foundational/invariant-governance.md, content-conservation "
           "verified (4 blocks, exact span extraction, reconstruction-equality proof -- see "
           "programme/operations-reorg/session3/roadmap-split-manifest.md). The original page "
           "stays at its current path (status/backlog content is not provably dead, per #108's "
           "own text this must not be silently reduced), retitled and relifecycled to reflect "
           "what it now purely is: a status/backlog/history tracker, not a mixed doctrine page. "
           "Bundled: operations/index.md catalogue link text updated to match.")

status, d = call("POST", "/api/v1/changes", {"title": title, "purpose": purpose, "workspace_key": "reference"},
                  f"roadmap-{PHASE}-{RUN}-create")
if status != 201:
    print("FAILED create", status, d); sys.exit(1)
change_id = d["change_id"]
print("change_id:", change_id)

# 1. CREATE_PAGE the new canonical durable page.
op = {"operation_type": "CREATE_PAGE",
      "payload": {"path": "control-plane/foundational/invariant-governance.md",
                  "title": "Invariant Governance",
                  "nav_path": "Control Plane/Foundational/Invariant Governance",
                  "content": new_page,
                  "workspace_key": "reference"}}
status, result = call("POST", f"/api/v1/changes/{change_id}/operations", op, f"roadmap-{PHASE}-{RUN}-op1-create")
if status != 201:
    print("FAILED op1 create", status, result); sys.exit(1)
print("op1 (create invariant-governance.md) ok")

# 2. REPLACE_DOCUMENT the original page: new content, title, nav_path.
status, page = call("GET", f"/api/v1/pages/{RID_ROADMAP}")
if status != 200 or page["path"] != "operations/invariant-roadmap.md":
    print("PRECHECK FAILED (roadmap)", status, page.get("path")); sys.exit(1)
op = {"operation_type": "REPLACE_DOCUMENT", "page_resource_id": RID_ROADMAP,
      "expected_revision": page["revision"],
      "payload": {"content": modified,
                  "title": "CharlieHub Control Plane — Invariant Enforcement Status & Backlog",
                  "nav_path": "Operations/Invariant Enforcement Status & Backlog"}}
status, result = call("POST", f"/api/v1/changes/{change_id}/operations", op, f"roadmap-{PHASE}-{RUN}-op2-replace")
if status != 201:
    print("FAILED op2 replace", status, result); sys.exit(1)
print("op2 (replace invariant-roadmap.md) ok")

# 3. Bundled reference repair: operations/index.md catalogue link text.
status, page = call("GET", f"/api/v1/pages/{RID_INDEX}")
if status != 200 or page["path"] != "operations/index.md":
    print("PRECHECK FAILED (index)", status, page.get("path")); sys.exit(1)
old_line = "- [Invariant Roadmap](invariant-roadmap.md)"
new_line = "- [Invariant Enforcement Status & Backlog](invariant-roadmap.md)"
if old_line not in page["content"]:
    print("SUBSTRING NOT FOUND in operations/index.md"); sys.exit(1)
fixed = page["content"].replace(old_line, new_line)
op = {"operation_type": "REPLACE_DOCUMENT", "page_resource_id": RID_INDEX,
      "expected_revision": page["revision"], "payload": {"content": fixed}}
status, result = call("POST", f"/api/v1/changes/{change_id}/operations", op, f"roadmap-{PHASE}-{RUN}-op3-index")
if status != 201:
    print("FAILED op3 index", status, result); sys.exit(1)
print("op3 (index.md link text) ok")

status, d = call("POST", f"/api/v1/changes/{change_id}/validate", {}, f"roadmap-{PHASE}-{RUN}-validate")
print("validate:", status, d.get("status"), "errors=", d.get("validation_summary", {}).get("errors"),
      "accepted=", d.get("validation_summary", {}).get("operations_accepted"), "/",
      d.get("validation_summary", {}).get("operations_checked"))
if d.get("validation_summary", {}).get("errors"):
    print("VALIDATION ERRORS"); print(json.dumps(d, indent=2)); print("change_id:", change_id); sys.exit(1)

if do_publish:
    status, d = call("POST", f"/api/v1/changes/{change_id}/publish", {}, f"roadmap-{PHASE}-{RUN}-go")
    print("publish:", status, d.get("status"))
    if status in (200, 201):
        pr = d.get("publication_receipt", {})
        print("applied:", pr.get("operations_applied"), "committed:", pr.get("database_transaction"),
              "deploy:", pr.get("deployment", {}).get("status"))
else:
    status, d = call("POST", f"/api/v1/changes/{change_id}/abandon",
                     {"reason": "rehearsal - non-publishing, validated only"}, f"roadmap-{PHASE}-{RUN}-abandon")
    print("abandon:", status, d.get("status"))

print("change_id:", change_id)
