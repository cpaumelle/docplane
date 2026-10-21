"""Logging, read-only, snapshot-enforcing read tool for an interpreting agent (the agent launch itself stays manual).

* Read verbs only (the WORK reads); KNOW verbs exist only when the run config says KNOW access is SEARCHED. No write verb exists.
* One snapshot cutoff from the run config: records created after it are removed from every result and counted in the log.
* Every call is appended to a log whose first line pins the run config; a log written under another config is rejected.
* Bookkeeper fingerprints (sem-v1 of the redacted text actually returned) go to `fingerprints.json` beside the log, so an
  interpretation can only cite what this run read.
Credentials are never stored: the caller supplies a 0600 key file path (see sources.load_api_key).
"""
from __future__ import annotations

import datetime
import json
import os
import re

from . import runconfig
from .ledger import semantic_version
from .sources import READ_VERBS

KNOW_READS = frozenset({"search_docs", "read_doc", "read_doc_outline", "read_doc_section", "list_docs"})
NOT_COUNTED = ("MARK", "CONFIG", "register")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# Typed relationships the interpretation relies on. They are part of the evidence version, so a changed reference moves the
# fingerprint (and invalidates dependent reasoning) even when the body text is identical.
CAPTURE_REFS = ("references", "source_capture_id", "initiative_id", "attached_initiative_id", "promoted_initiative_id", "linked_capture_ids", "founding_capture_ids")
ACTIVITY_REFS = ("references", "source_capture_id")
INITIATIVE_REFS = ("founding_capture_ids",)


def relationships(record, keys):
    """Canonical text of the typed references a record carries: key, and each reference's type and id, order-independent."""
    out = []
    for key in keys:
        value = record.get(key)
        if value in (None, [], ""):
            continue
        items = value if isinstance(value, list) else [value]
        out.append(key + "=" + json.dumps(sorted(json.dumps(i, sort_keys=True, separators=(",", ":")) for i in items), separators=(",", ":")))
    return ";".join(out) or None


def _refs(record, keys):
    """State kwargs for semantic_version: present only when the record has typed references, so a record without any keeps its version."""
    text = relationships(record, keys)
    return {"refs": text} if text else {}


class ConfigConflict(Exception):
    pass


class ReadTool:
    def __init__(self, config, log_path, call):
        """`call(verb, args) -> dict` performs one raw governed read (injected so tests and the CLI share this class)."""
        errors = runconfig.verify(config)
        if errors:
            raise ConfigConflict("run config is not intact: " + "; ".join(errors))
        self.config, self.log_path, self._call = config, log_path, call
        self.cfg = config["config_sha256"][:16]
        self.fp_path = os.path.join(os.path.dirname(os.path.abspath(log_path)), "fingerprints.json")
        if os.path.exists(log_path) and os.path.getsize(log_path):
            with open(log_path) as handle:
                first = json.loads(handle.readline())
            if first.get("tool") != "CONFIG" or first.get("config_sha256") != config["config_sha256"]:
                raise ConfigConflict("the log was written under a different run config; start a new log")
        else:
            self._log({"tool": "CONFIG", "config_sha256": config["config_sha256"], "snapshot_cutoff": config["snapshot_cutoff"], "know_access": config["know_access"]})

    # -- logging -----------------------------------------------------------------
    def _log(self, row):
        row = dict({"ts": _now(), "cfg": self.cfg}, **row)
        with open(self.log_path, "a") as handle:
            handle.write(json.dumps(row) + "\n")

    def _count(self):
        with open(self.log_path) as handle:
            return sum(1 for line in handle if json.loads(line).get("tool") not in NOT_COUNTED and not json.loads(line).get("refused"))

    def _after(self, stamp):
        return bool(stamp) and str(stamp)[:19] > self.config["snapshot_cutoff"][:19]

    def _fingerprints(self, update):
        if not update:
            return
        data = {}
        if os.path.exists(self.fp_path):
            with open(self.fp_path) as handle:
                data = json.load(handle)
        data.update(update)
        with open(self.fp_path, "w") as handle:
            json.dump(data, handle)
        os.chmod(self.fp_path, 0o600)

    # -- the snapshot cut ---------------------------------------------------------
    def _filter(self, verb, res):
        ids, removed, fps = [], 0, {}
        if verb == "docplane_work_capture_get" and "error" not in res:
            if self._after(res.get("created_at")):
                return {"error": "AFTER_SNAPSHOT", "code": "AFTER_SNAPSHOT"}, [], 1, {}
            ids = [res["capture_id"]]
            if (res.get("body") or "").strip() and not res.get("body_withheld"):
                text = res["body"] + "\n" + (res.get("origin_text") or "")
                fps[res["capture_id"]] = {"projection": "sem-v1", "value": semantic_version(text, kind=res.get("kind"), status=res.get("status"), **_refs(res, CAPTURE_REFS)).split(":")[1]}
        elif verb == "docplane_work_captures":
            keep = [c for c in res.get("captures", []) if not self._after(c.get("created_at"))]
            removed = len(res.get("captures", [])) - len(keep)
            res, ids = dict(res, captures=keep, count=len(keep), filtered_after_snapshot=removed), [c["capture_id"] for c in keep]
        elif verb == "docplane_work_get" and "error" not in res:
            keep = [a for a in res.get("activities", []) if not self._after(a.get("created_at"))]
            removed = len(res.get("activities", [])) - len(keep)
            res = dict(res, activities=keep, returned_activity_count=len(keep), filtered_after_snapshot=removed,
                       note="activities after the snapshot are removed; initiative state is the instance's current state (revisions cannot be dated)")
            ids = [res["initiative_id"]] + [a["activity_id"] for a in keep]
            fps[res["initiative_id"]] = {"projection": "sem-v1", "value": semantic_version("initiative state", work_state=res.get("work_state"), version=res.get("version"), **_refs(res, INITIATIVE_REFS)).split(":")[1]}
            for a in keep:
                if (a.get("body") or "").strip() and not a.get("body_withheld"):
                    fps[a["activity_id"]] = {"projection": "sem-v1", "value": semantic_version(a["body"], activity_type=a["activity_type"], **_refs(a, ACTIVITY_REFS)).split(":")[1]}
        elif verb == "docplane_work_initiatives":
            ids = [i["initiative_id"] for i in res.get("initiatives", [])]
            for i in res.get("initiatives", []):
                fps[i["initiative_id"]] = {"projection": "sem-v1", "value": semantic_version("initiative summary", key=i["initiative_key"], work_state=i["work_state"]).split(":")[1]}
        elif verb in KNOW_READS and "error" not in res and self._after(res.get("last_updated") or res.get("updated_at")):
            return {"error": "AFTER_SNAPSHOT", "code": "AFTER_SNAPSHOT"}, [], 1, {}
        return res, ids, removed, fps

    # -- public verbs -------------------------------------------------------------
    def call(self, verb, args):
        if verb in KNOW_READS:
            if self.config["know_access"] != "SEARCHED":
                self._log({"tool": verb, "refused": True, "reason": "KNOW access is " + self.config["know_access"]})
                raise PermissionError(f"KNOW is not available in this run ({self.config['know_access']}: {self.config['know_reason']}); conclusions depending on KNOW cannot be drawn")
        elif verb not in READ_VERBS:
            self._log({"tool": verb, "refused": True, "reason": "not a read verb"})
            raise PermissionError("not a read verb: " + verb)
        if self._count() >= self.config["read_budget"]:
            self._log({"tool": verb, "refused": True, "reason": "read budget exhausted"})
            raise PermissionError("read budget exhausted")
        try:
            res = self._call(verb, args)
            if isinstance(res, dict) and set(res) == {"result"}:
                res = res["result"]
            ok = not (isinstance(res, dict) and "error" in res)
        except PermissionError:
            raise
        except Exception as exc:                      # transport failure: keep only the class name
            res, ok = {"error": type(exc).__name__}, False
        removed, ids, fps = 0, [], {}
        if ok:
            res, ids, removed, fps = self._filter(verb, res)
        self._fingerprints(fps)
        short = {k: (v if len(str(v)) < 120 else str(v)[:120]) for k, v in args.items()}
        self._log({"tool": verb, "args": short, "ok": ok and "error" not in res, "ids_returned": ids, "filtered_after_snapshot": removed})
        return res

    def register(self, label, grep=None, lines=None, maximum=60):
        path = runconfig.pinned_path(self.config, label)
        if runconfig.sha256_file(path) != next(p["sha256"] for p in self.config["pinned_files"] if p["label"] == label):
            raise ConfigConflict(f"pinned file {label} changed since the config was created")
        with open(path) as handle:
            text = handle.read().splitlines()
        if grep:
            rx = re.compile(grep, re.I)
            out = [f"{i + 1}: {line}" for i, line in enumerate(text) if rx.search(line)]
        else:
            a, b = (int(x) for x in (lines or "1:40").split(":"))
            out = [f"{i}: {text[i - 1]}" for i in range(a, min(b, len(text)) + 1)]
        out = out[:maximum]
        self._log({"tool": "register", "args": {"label": label, "grep": grep, "lines": lines}, "ok": True, "lines_returned": len(out)})
        return "\n".join(out)

    def mark(self, label):
        self._log({"tool": "MARK", "label": str(label)[:60]})
