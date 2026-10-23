"""Load the entity's governance only from the declared package manifest.

Each declared component is checked against its git blob sha1 and then *used*: the predicates
decide ALLOW, the disposition schema fixes the disposition set, and the transition contract
fixes the receipt shape. If any one is undeclared, absent, altered or unusable, loading
returns FAIL_CLOSED and the entity does not run.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .dispositions import non_allow

MANIFEST_NAME = "package-manifest.v0.json"
MANIFEST_SCHEMA = "stegverse.sv011.entity-package-manifest/v0"
REQUIRED_ROLES = ("admissibility_predicates", "disposition_vocabulary", "transition_receipt_rule")
REQUIRED_PREDICATES = tuple(f"GTG-A{i}" for i in range(1, 9))
REQUIRED_DISPOSITIONS = ("ALLOW", "DENY", "FAIL_CLOSED")
LOAD_RETRY = "python3 -m sv011_entity load --package <package_dir>"


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _fc(code: str, predicate: str, repair: str) -> dict[str, Any]:
    return non_allow("FAIL_CLOSED", code, predicate, repair,
                     "restore the declared component from its repo@commit and reload; the entity stays stopped until then",
                     retry=LOAD_RETRY)


def _use_predicates(data: bytes) -> Any:
    # execute exactly the verified bytes; no import cache, nothing written beside the component
    namespace: dict[str, Any] = {"__name__": "sv011_entity_declared_predicates"}
    exec(compile(data, "declared:admissibility_predicates", "exec"), namespace)
    table = namespace.get("PREDICATES")
    if not isinstance(table, dict) or any(not callable(table.get(k)) for k in REQUIRED_PREDICATES):
        raise ValueError("PREDICATES lacks GTG-A1..A8")
    return {k: table[k] for k in REQUIRED_PREDICATES}


def _use_dispositions(data: bytes) -> Any:
    schema = json.loads(data.decode("utf-8"))
    enum = schema["properties"]["disposition"]["enum"]
    flags = [k for k in schema["required"] if k.startswith("execution_")
             and schema["properties"].get(k, {}).get("const", None) is False]
    if any(d not in enum for d in REQUIRED_DISPOSITIONS) or len(flags) != 1:
        raise ValueError("disposition vocabulary incomplete")
    return {"dispositions": tuple(enum), "execution_flag_key": flags[0]}


def _use_receipt_rule(data: bytes) -> Any:
    contract = json.loads(data.decode("utf-8"))
    fields = contract["required_fields"]
    needed = {"transition_id", "transition_class", "predecessor_state_sha256",
              "successor_state_sha256", "evidence", "previous_receipt_sha256"}
    if contract.get("append_only_receipts") is not True or not needed <= set(fields):
        raise ValueError("transition contract lacks append-only chained receipts")
    return {"receipt_schema": contract["receipt_schema"], "required_fields": tuple(fields),
            "replay_rule": contract["replay_rule"]}


USERS = {"admissibility_predicates": _use_predicates,
         "disposition_vocabulary": _use_dispositions,
         "transition_receipt_rule": _use_receipt_rule}


def load(package_dir: str | Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (governance, None) or (None, FAIL_CLOSED record)."""
    root = Path(package_dir)
    mpath = root / MANIFEST_NAME
    if not mpath.is_file():
        return None, _fc("SV011_ENTITY_MANIFEST_ABSENT", f"{MANIFEST_NAME} exists in the package", "supply the declared package manifest")
    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except ValueError:
        return None, _fc("SV011_ENTITY_MANIFEST_UNREADABLE", "package manifest is JSON", "fix the manifest")
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        return None, _fc("SV011_ENTITY_MANIFEST_SCHEMA_MISMATCH", f"schema == {MANIFEST_SCHEMA}", "use the v0 manifest")
    for key in ("entity_id", "components", "declared_actions", "declared_transition_classes"):
        if not manifest.get(key):
            return None, _fc("SV011_ENTITY_MANIFEST_FIELD_MISSING", f"manifest declares {key}", f"declare {key}")
    roles = [c.get("role") for c in manifest["components"] if isinstance(c, dict)]
    if len(roles) != len(manifest["components"]) or len(roles) != len(set(roles)):
        return None, _fc("SV011_ENTITY_COMPONENT_ROLES_INVALID", "one object per component, roles unique", "fix components")
    extra = sorted(set(roles) - set(REQUIRED_ROLES))
    if extra:
        return None, _fc("SV011_ENTITY_COMPONENT_UNUSED", f"every declared component is used by the bootstrap; unused: {extra}",
                         "remove components the bootstrap does not use (minimality)")
    governance: dict[str, Any] = {"manifest": manifest, "manifest_sha256": "sha256:" + hashlib.sha256(mpath.read_bytes()).hexdigest()}
    for role in REQUIRED_ROLES:
        comp = next((c for c in manifest["components"] if c.get("role") == role), None)
        if comp is None:
            return None, _fc("SV011_ENTITY_COMPONENT_UNDECLARED", f"manifest declares a {role} component",
                             f"declare the {role} component by repo@commit:path and git_blob_sha1")
        local = root / str(comp.get("local_path", ""))
        if not comp.get("local_path") or not local.is_file():
            return None, _fc("SV011_ENTITY_COMPONENT_ABSENT", f"{role} file present at {comp.get('local_path')}",
                             f"restore {comp.get('repo')}@{comp.get('commit')}:{comp.get('path')}")
        data = local.read_bytes()
        if git_blob_sha1(data) != comp.get("git_blob_sha1"):
            return None, _fc("SV011_ENTITY_COMPONENT_DIGEST_MISMATCH", f"{role} bytes match git_blob_sha1 {comp.get('git_blob_sha1')}",
                             f"restore exact bytes of {comp.get('repo')}@{comp.get('commit')}:{comp.get('path')}")
        try:
            governance[role] = USERS[role](data)
        except Exception as exc:  # component present but unusable -> stop, never run ungoverned
            return None, _fc("SV011_ENTITY_COMPONENT_UNUSABLE", f"{role} supplies its governance property ({type(exc).__name__})",
                             f"restore a usable {role} component")
    return governance, None
