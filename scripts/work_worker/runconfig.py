"""Pinned run configuration: one explicit snapshot cutoff and one identity for a whole run.

The config is created once from a spec, then every tool (read, check, render) is given it and rejects anything that
conflicts: a log written under another config, an interpretation whose header disagrees, a changed pinned file.
It holds no credential and no record content: credentials are passed to the read tool as a file path at run time.
"""
from __future__ import annotations

import hashlib
import json
import os
import re

from . import evaluations, interpretation, ledger

CUTOFF = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
KNOW = ("SEARCHED", "NOT_SEARCHED_NO_CREDENTIAL", "NOT_SEARCHED_OTHER")
SPEC_KEYS = {"scope_id", "snapshot_cutoff", "instance", "know_access", "incoming_record_ids", "pinned_files", "prompt_files", "model_configuration",
             "read_budget", "authoritative_registers", "initiative_ids", "know_reason"}


def sha256_file(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _canonical(body):
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def _package_hashes():
    here = os.path.dirname(os.path.abspath(__file__))
    return {name: sha256_file(os.path.join(here, name)) for name in ("interpretation.schema.json", "interpretation.py", "ledger.py", "evaluations.py")}


def create(spec):
    """Build a config from a spec (no hashes in the spec). Raises ValueError on anything inconsistent."""
    if set(spec) != SPEC_KEYS:
        raise ValueError("spec takes exactly: " + ", ".join(sorted(SPEC_KEYS)))
    if not CUTOFF.match(spec["snapshot_cutoff"]):
        raise ValueError("snapshot_cutoff must be one UTC timestamp like 2026-10-21T08:50:42Z")
    if spec["know_access"] not in KNOW:
        raise ValueError("know_access must be one of " + ", ".join(KNOW))
    if not isinstance(spec["know_reason"], str) or (spec["know_access"] != "SEARCHED" and len(spec["know_reason"].strip()) < 5):
        raise ValueError("unavailable KNOW access needs a reason (know_reason)")
    inst = spec["instance"]
    if set(inst) != {"label", "url", "expected_identity"} or not isinstance(inst["expected_identity"], dict):
        raise ValueError("instance takes label, url and expected_identity (named hashes or versions the operator verified)")
    incoming = [ledger.record_id(x) for x in spec["incoming_record_ids"]]
    if len(set(incoming)) != len(incoming) or not incoming:
        raise ValueError("incoming_record_ids must be unique and non-empty")
    init_ids = [ledger.record_id(x) for x in spec["initiative_ids"]]
    pinned, labels = [], set()
    for item in spec["pinned_files"]:
        if set(item) != {"label", "path"} or item["label"] in labels:
            raise ValueError("pinned_files take a unique label and a path")
        labels.add(item["label"])
        pinned.append({"label": item["label"], "path": os.path.abspath(item["path"]), "sha256": sha256_file(item["path"])})
    regs = []
    for r in spec["authoritative_registers"]:
        if set(r) != {"register_id", "description", "pinned_file_label"} or r["pinned_file_label"] not in labels:
            raise ValueError("a register names a pinned file label that exists")
        regs.append(r)
    prompts = [{"path": os.path.abspath(p), "sha256": sha256_file(p)} for p in spec["prompt_files"]]
    bundle = hashlib.sha256(b"".join(open(p["path"], "rb").read() for p in prompts)).hexdigest()
    body = {"scope_id": spec["scope_id"], "snapshot_cutoff": spec["snapshot_cutoff"], "instance": inst, "know_access": spec["know_access"], "know_reason": spec["know_reason"].strip(),
            "incoming_record_ids": incoming, "initiative_ids": init_ids, "pinned_files": pinned, "authoritative_registers": regs,
            "prompts": prompts, "prompt_bundle_sha256": bundle, "model_configuration": str(spec["model_configuration"])[:300], "read_budget": int(spec["read_budget"]),
            "contract": _package_hashes()}
    body["config_sha256"] = hashlib.sha256(_canonical(body)).hexdigest()
    return body


def load(path):
    with open(path) as handle:
        return json.load(handle)


def verify(config):
    """Errors if the config was edited, or a pinned/prompt file or the contract code changed since it was made."""
    errors = []
    body = {k: v for k, v in config.items() if k != "config_sha256"}
    if hashlib.sha256(_canonical(body)).hexdigest() != config.get("config_sha256"):
        errors.append("config was edited after it was created (self hash differs)")
    for item in config.get("pinned_files", []) + config.get("prompts", []):
        if not os.path.exists(item["path"]) or sha256_file(item["path"]) != item["sha256"]:
            errors.append(f"pinned file changed or missing: {item.get('label') or item['path']}")
    if config.get("contract") != _package_hashes():
        errors.append("the contract code (schema, validator, ledger, evaluations) differs from the one the config pinned")
    return errors


def pinned_path(config, label):
    for item in config["pinned_files"]:
        if item["label"] == label:
            return item["path"]
    raise KeyError(label)


def header_errors(config, doc):
    """An interpretation's run header must agree with the config on every pinned fact."""
    errors = []
    scope, run = doc["run"]["scope"], doc["run"]
    if scope["scope_id"] != config["scope_id"]:
        errors.append("scope_id conflicts with the run config")
    if scope["collected_at"] != config["snapshot_cutoff"]:
        errors.append("collected_at conflicts with the run config's single snapshot cutoff")
    if sorted(scope["incoming_record_ids"]) != sorted(config["incoming_record_ids"]):
        errors.append("incoming_record_ids conflict with the run config")
    if sorted(scope["initiative_ids"]) != sorted(config["initiative_ids"]):
        errors.append("initiative_ids conflict with the run config")
    want = sorted((r["register_id"], r["description"]) for r in config["authoritative_registers"])
    if sorted((r["register_id"], r["description"]) for r in scope["authoritative_registers"]) != want:
        errors.append("authoritative registers conflict with the run config")
    if run["prompt_hash"] != config["prompt_bundle_sha256"]:
        errors.append("prompt_hash conflicts with the pinned prompt bundle")
    if config["know_access"] != "SEARCHED" and not any("know" in x["what"].lower() and len(x["why"].strip()) >= 5 for x in doc["briefing"]["omitted"]):
        errors.append("KNOW is unavailable in this run config, so the briefing must list it as omitted with a reason")
    for e in doc["intake"]:
        if e["search"]["know_access"] != config["know_access"]:
            errors.append(f"intake {e['record_id']}: know_access conflicts with the run config ({config['know_access']})")
    return errors
