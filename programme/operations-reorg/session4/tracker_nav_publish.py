import json, sys
sys.path.insert(0, "/tmp/docplane-issue44-audit")
from docplane_api_repair import call

PHASE = sys.argv[1]
RUN = sys.argv[2] if len(sys.argv) > 2 else "r1"
do_publish = PHASE == "publish"

IDS = {
    "in-flight": ("cf80f510-2962-50cd-8dca-0a8f0804cf10", "operations/in-flight.md", "Operations/Tracker/In-Flight"),
    "completed": ("ea7fde8e-ec6f-555b-b855-e25844dc1f98", "operations/completed.md", "Operations/Tracker/Completed"),
    "engineering-backlog": ("a58cc074-0dac-5bcf-8fcc-2660b974cd68", "operations/engineering-backlog.md", "Operations/Tracker/Engineering Backlog"),
}

title = "docplane#109: Tracker nav_path normalization (5-item board/tracker set)" + ("" if do_publish else " (rehearsal)")
purpose = ("Normalises nav_path for in-flight.md, completed.md, engineering-backlog.md to "
           "Operations/Tracker/... matching the 2 already-correct siblings "
           "(soaks.md, pickup-history.md). Metadata only -- physical paths unchanged, all 5 "
           "pages remain intentional root-level siblings per #109's own finding. Verified "
           "no page/section conflict and no duplicate nav_path introduced (find_page_section_conflicts, "
           "live corpus simulation).")

status, d = call("POST", "/api/v1/changes", {"title": title, "purpose": purpose, "workspace_key": "reference"},
                  f"tracker-{PHASE}-{RUN}-create")
if status != 201:
    print("FAILED create", status, d); sys.exit(1)
change_id = d["change_id"]
print("change_id:", change_id)

for key, (rid, expected_path, target_nav) in IDS.items():
    status, page = call("GET", f"/api/v1/pages/{rid}")
    if status != 200 or page["path"] != expected_path:
        print(f"PRECHECK FAILED {key}", status, page.get("path")); sys.exit(1)
    op = {"operation_type": "REPARENT_NAV", "page_resource_id": rid,
          "expected_revision": page["revision"], "payload": {"to_nav_path": target_nav}}
    status, result = call("POST", f"/api/v1/changes/{change_id}/operations", op, f"tracker-{PHASE}-{RUN}-op-{key}")
    if status != 201:
        print(f"FAILED op {key}", status, result); sys.exit(1)
    print(f"op ({key} -> {target_nav}) ok")

status, d = call("POST", f"/api/v1/changes/{change_id}/validate", {}, f"tracker-{PHASE}-{RUN}-validate")
print("validate:", status, d.get("status"), "errors=", d.get("validation_summary", {}).get("errors"),
      "accepted=", d.get("validation_summary", {}).get("operations_accepted"), "/",
      d.get("validation_summary", {}).get("operations_checked"))
if d.get("validation_summary", {}).get("errors"):
    print("VALIDATION ERRORS"); print(json.dumps(d, indent=2)); print("change_id:", change_id); sys.exit(1)

if do_publish:
    status, d = call("POST", f"/api/v1/changes/{change_id}/publish", {}, f"tracker-{PHASE}-{RUN}-go")
    print("publish:", status, d.get("status"))
    if status in (200, 201):
        pr = d.get("publication_receipt", {})
        print("applied:", pr.get("operations_applied"), "committed:", pr.get("database_transaction"),
              "deploy:", pr.get("deployment", {}).get("status"))
else:
    status, d = call("POST", f"/api/v1/changes/{change_id}/abandon",
                     {"reason": "rehearsal - non-publishing, validated only"}, f"tracker-{PHASE}-{RUN}-abandon")
    print("abandon:", status, d.get("status"))

print("change_id:", change_id)
