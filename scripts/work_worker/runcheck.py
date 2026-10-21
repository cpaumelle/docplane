"""One command's worth of validation for a run: contract, config consistency, read-log consistency, continuity.

It reads; it never writes into the directories of its inputs. Minted obligation ids are immutable once issued.
"""
from __future__ import annotations

import json
import os
import uuid

from . import evaluations, interpretation, runconfig

NAMESPACE = uuid.UUID("6f0a1d3e-52c4-4b8e-9a77-0d2c3b1e8a41")


def fresh_output_dir(out, *inputs):
    """An output directory must be new (or empty) and must not be, or contain, any input's directory: originals stay immutable."""
    out = os.path.abspath(out)
    if os.path.exists(out) and (not os.path.isdir(out) or os.listdir(out)):
        raise FileExistsError(f"{out} exists and is not empty; outputs are never written over existing files")
    for path in inputs:
        folder = os.path.dirname(os.path.abspath(path))
        if folder == out or folder.startswith(out + os.sep):
            raise FileExistsError(f"{out} contains an input ({path}); choose a new directory")
    os.makedirs(out, exist_ok=True)
    return out


def load_log(path):
    return evaluations.load_log(path)


def log_errors(config, log, fingerprints, doc):
    errors = []
    if not log or log[0].get("tool") != "CONFIG" or log[0].get("config_sha256") != config["config_sha256"]:
        return ["the read log does not start with this run config's header"]
    short = config["config_sha256"][:16]
    if any(row.get("cfg") != short for row in log):
        errors.append("the read log mixes run configurations")
    if any(row.get("tool") == "CONFIG" for row in log[1:]):
        errors.append("the read log has more than one config header")
    for rid_ in [r["record_id"] for r in doc["records"]]:
        want = fingerprints.get(rid_)
        have = next((r["source_fingerprint"] for r in doc["records"] if r["record_id"] == rid_), None)
        if want is None:
            errors.append(f"record {rid_} was not read in this run (no fingerprint was issued for it)")
        elif have["value"] != want["value"]:
            errors.append(f"record {rid_}: the cited fingerprint is not the one the read tool issued")
    seen_log = evaluations.summarise(log)
    for e in doc["intake"]:
        s = e["search"]
        own = {e["record_id"]}
        if s["captures_inspected"] > len(seen_log["captures_opened"] - own):
            errors.append(f"intake {e['record_id']}: claims {s['captures_inspected']} captures inspected but the log shows {len(seen_log['captures_opened'] - own)} other captures opened")
        if s["initiatives_inspected"] > len(seen_log["histories_opened"]):
            errors.append(f"intake {e['record_id']}: claims {s['initiatives_inspected']} initiatives inspected but the log shows {len(seen_log['histories_opened'])} histories opened")
    reads_ok = {r["record_id"] for r in doc["retrieval"]["reads"] if r["read_ok"]}
    seen = {i for row in log for i in (row.get("ids_returned") or [])}
    for missing in sorted(reads_ok - seen):
        errors.append(f"retrieval lists {missing} as read but the log never returned it")
    return errors


def check_run(config, doc, log, fingerprints, *, evaluation_list=None, previous=None, registry=None):
    errors = list(runconfig.verify(config))
    errors += interpretation.validate(doc)
    try:                                              # conflicts with the config are reported even when the contract also fails
        errors += runconfig.header_errors(config, doc)
    except (KeyError, TypeError):
        errors.append("the interpretation header is too malformed to compare with the run config")
    errors += log_errors(config, log, fingerprints, doc) if "records" in doc else []
    if evaluation_list is not None:
        errors += ["evaluations: " + e for e in evaluations.validate_evaluations(evaluation_list, log)]
    if previous is not None:
        errors += ["continuity: " + e for e in interpretation.continuity_errors(previous, doc, registry or {})]
    return errors


def mint(doc, existing=None):
    """Obligation ids: issued once per (record, local id), never changed. Refuses any attempt to alter an issued id."""
    registry = dict(existing or {})
    for rec in doc["records"]:
        for part in rec["parts"]:
            o = part["obligation"]
            if not o:
                continue
            new_id = str(uuid.uuid5(NAMESPACE, f"{rec['record_id']}:{o['local_id']}"))
            if o["local_id"] in registry and registry[o["local_id"]] != new_id:
                raise ValueError(f"obligation {o['local_id']} already has a different minted id; ids are immutable")
            if o["maps_to"] and o["local_id"] in registry and o["maps_to"] != registry[o["local_id"]]:
                raise ValueError(f"obligation {o['local_id']}: maps_to disagrees with the minted id")
            registry[o["local_id"]] = new_id
    return registry
