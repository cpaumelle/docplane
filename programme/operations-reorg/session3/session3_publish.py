import json, sys
sys.path.insert(0, "/tmp/docplane-issue44-audit")
from docplane_api_repair import call

PHASE = sys.argv[1]
RUN = sys.argv[2] if len(sys.argv) > 2 else "r1"
do_publish = PHASE == "publish"

IDS = {
    "enf": "85246812-6ddc-580f-927a-f2a5472ce7b3",       # operations/control-plane-enforcement.md
    "g1a_evidence": "f12d2647-a224-560e-ad75-35ca3e225fe5",
    "g1a_exec": "548e4db4-b844-5b19-b4ac-97206b60e659",
    "fshard": "13a9ddae-1252-563b-a02f-ff6a48ee686d",
}

title = ("docplane#108/#109: control-plane-enforcement.md move + nav_path corrections") + ("" if do_publish else " (rehearsal)")
purpose = ("#108: control-plane-enforcement.md has no supersession evidence (re-checked) -- "
           "move to operations/platform/ as ordinary orphaned-but-current reference material, "
           "matching the #104-era disposition table. Content link fix (broken "
           "control-plane/authority-model.md -> control-plane/foundational/authority-model.md) "
           "follows in a separate change once this move's new revision is known (two-phase "
           "pattern from docplane#104). #109: nav_path corrections -- w4-1-5c-g1a-evidence.md "
           "and -execution-plan.md to Archive/Evidence/... matching their 2 sibling pages; "
           "filesystem-hardening.md 'Security & Access' -> 'Security' matching the other 7 "
           "operations/security/ pages. Metadata only, no physical moves for these 3.")

status, d = call("POST", "/api/v1/changes", {"title": title, "purpose": purpose, "workspace_key": "reference"},
                  f"s3-{PHASE}-{RUN}-create")
if status != 201:
    print("FAILED create", status, d); sys.exit(1)
change_id = d["change_id"]
print("change_id:", change_id)

def get_page(rid):
    status, page = call("GET", f"/api/v1/pages/{rid}")
    if status != 200:
        print("FETCH FAILED", rid, status, page); sys.exit(1)
    return page

# 1. MOVE_PAGE control-plane-enforcement.md -> operations/platform/
p = get_page(IDS["enf"])
assert p["path"] == "operations/control-plane-enforcement.md", p["path"]
op = {"operation_type": "MOVE_PAGE", "page_resource_id": IDS["enf"],
      "expected_revision": p["revision"],
      "payload": {"to_path": "operations/platform/control-plane-enforcement.md",
                  "to_nav_path": "Operations/Platform/Control Plane Enforcement",
                  "create_redirect": True}}
status, result = call("POST", f"/api/v1/changes/{change_id}/operations", op, f"s3-{PHASE}-{RUN}-op1-move-enf")
if status != 201:
    print("FAILED op1", status, result); sys.exit(1)
print("op1 (move enf) ok")

# 2/3. REPARENT_NAV for the two w4-1-5c-g1a-* pages
for key, target_nav, expected_path in [
    ("g1a_evidence", "Archive/Evidence/W4-1.5C G1a Evidence", "operations/w4-1-5c-g1a-evidence.md"),
    ("g1a_exec", "Archive/Evidence/W4-1.5C G1a Execution Plan", "operations/w4-1-5c-g1a-execution-plan.md"),
]:
    p = get_page(IDS[key])
    assert p["path"] == expected_path, p["path"]
    op = {"operation_type": "REPARENT_NAV", "page_resource_id": IDS[key],
          "expected_revision": p["revision"], "payload": {"to_nav_path": target_nav}}
    status, result = call("POST", f"/api/v1/changes/{change_id}/operations", op, f"s3-{PHASE}-{RUN}-op-{key}")
    if status != 201:
        print(f"FAILED op {key}", status, result); sys.exit(1)
    print(f"op ({key} nav fix) ok")

# 4. REPARENT_NAV filesystem-hardening.md
p = get_page(IDS["fshard"])
assert p["path"] == "operations/filesystem-hardening.md", p["path"]
op = {"operation_type": "REPARENT_NAV", "page_resource_id": IDS["fshard"],
      "expected_revision": p["revision"], "payload": {"to_nav_path": "Operations/Security/Filesystem Hardening"}}
status, result = call("POST", f"/api/v1/changes/{change_id}/operations", op, f"s3-{PHASE}-{RUN}-op-fshard")
if status != 201:
    print("FAILED op fshard", status, result); sys.exit(1)
print("op (filesystem-hardening nav fix) ok")

status, d = call("POST", f"/api/v1/changes/{change_id}/validate", {}, f"s3-{PHASE}-{RUN}-validate")
print("validate:", status, d.get("status"), "errors=", d.get("validation_summary", {}).get("errors"),
      "accepted=", d.get("validation_summary", {}).get("operations_accepted"), "/",
      d.get("validation_summary", {}).get("operations_checked"))
if d.get("validation_summary", {}).get("errors"):
    print("VALIDATION ERRORS"); print(json.dumps(d, indent=2)); print("change_id:", change_id); sys.exit(1)

if do_publish:
    status, d = call("POST", f"/api/v1/changes/{change_id}/publish", {}, f"s3-{PHASE}-{RUN}-go")
    print("publish:", status, d.get("status"))
    if status in (200, 201):
        pr = d.get("publication_receipt", {})
        print("applied:", pr.get("operations_applied"), "committed:", pr.get("database_transaction"),
              "deploy:", pr.get("deployment", {}).get("status"))
else:
    status, d = call("POST", f"/api/v1/changes/{change_id}/abandon",
                     {"reason": "rehearsal - non-publishing, validated only"}, f"s3-{PHASE}-{RUN}-abandon")
    print("abandon:", status, d.get("status"))

print("change_id:", change_id)
