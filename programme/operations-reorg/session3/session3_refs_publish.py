import sys
sys.path.insert(0, "/tmp/docplane-issue44-audit")
from docplane_api_repair import call

PHASE = sys.argv[1]
RUN = sys.argv[2] if len(sys.argv) > 2 else "r1"
do_publish = PHASE == "publish"

RID = "85246812-6ddc-580f-927a-f2a5472ce7b3"
PATH = "operations/platform/control-plane-enforcement.md"
OLD = "control-plane/authority-model.md"

title = "docplane#108: control-plane-enforcement.md broken-link fix (post-move)" + ("" if do_publish else " (rehearsal)")
purpose = ("Fixes the pre-existing broken citation (control-plane/authority-model.md, real "
           "path control-plane/foundational/authority-model.md) identified in #108, now that "
           "the page has moved to operations/platform/. Relative depth computed for the new "
           "path: operations/platform/ -> control-plane/foundational/ is ../../control-plane/"
           "foundational/authority-model.md, but the citation is prose (backtick-quoted path "
           "text, not a markdown link), so it is corrected as a plain path reference matching "
           "house style for prose citations elsewhere in this page (e.g. 'WS-A-secure.md "
           "ticket A9' is also prose, not a link) -- full corpus path, not relative.")

status, d = call("POST", "/api/v1/changes", {"title": title, "purpose": purpose, "workspace_key": "reference"},
                  f"s3refs-{PHASE}-{RUN}-create")
if status != 201:
    print("FAILED create", status, d); sys.exit(1)
change_id = d["change_id"]
print("change_id:", change_id)

status, page = call("GET", f"/api/v1/pages/{RID}")
if status != 200 or page["path"] != PATH:
    print("PRECHECK FAILED", status, page.get("path")); sys.exit(1)
content = page["content"]
if OLD not in content:
    print("SUBSTRING NOT FOUND"); sys.exit(1)
fixed = content.replace(OLD, "control-plane/foundational/authority-model.md")
assert fixed != content

op = {"operation_type": "REPLACE_DOCUMENT", "page_resource_id": RID,
      "expected_revision": page["revision"], "payload": {"content": fixed}}
status, result = call("POST", f"/api/v1/changes/{change_id}/operations", op, f"s3refs-{PHASE}-{RUN}-op1")
if status != 201:
    print("FAILED op", status, result); sys.exit(1)
print("repair ok:", PATH)

status, d = call("POST", f"/api/v1/changes/{change_id}/validate", {}, f"s3refs-{PHASE}-{RUN}-validate")
print("validate:", status, d.get("status"), "errors=", d.get("validation_summary", {}).get("errors"),
      "accepted=", d.get("validation_summary", {}).get("operations_accepted"), "/",
      d.get("validation_summary", {}).get("operations_checked"))
if d.get("validation_summary", {}).get("errors"):
    print("VALIDATION ERRORS"); print("change_id:", change_id); sys.exit(1)

if do_publish:
    status, d = call("POST", f"/api/v1/changes/{change_id}/publish", {}, f"s3refs-{PHASE}-{RUN}-go")
    print("publish:", status, d.get("status"))
    if status in (200, 201):
        pr = d.get("publication_receipt", {})
        print("applied:", pr.get("operations_applied"), "committed:", pr.get("database_transaction"),
              "deploy:", pr.get("deployment", {}).get("status"))
else:
    status, d = call("POST", f"/api/v1/changes/{change_id}/abandon",
                     {"reason": "rehearsal - non-publishing, validated only"}, f"s3refs-{PHASE}-{RUN}-abandon")
    print("abandon:", status, d.get("status"))

print("change_id:", change_id)
