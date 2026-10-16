#!/usr/bin/env python3
"""Resolve a bound capability address to the receiving operation the overlay names.

The overlay names which capability arrives at which operation. Authority
is a constraint variable: this module records the operation the overlay
named. It does not select admission, routing, or execution. A
disposition the receiving operation later records is transition evidence.

A registry row under ``BOUNDARY_LOCAL_CAPABILITY_INGRESS`` is refused unless
the overlay names that row. An address cannot declare a capability the overlay
did not bind. The module the overlay names must identify itself with the
``operation_id`` the binding publishes.

No processor is selected at this address. Time is a constraint variable carried
on the packet; this resolver does not read a clock and does not treat a tick
as admission.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Mapping

ROLE = "BOUNDARY_LOCAL_CAPABILITY_INGRESS"

BOUNDARY_PATH = "org-runtime/interlock-intr.json"
BINDINGS_FIELD = "capability_endpoint_bindings"
ADDRESSED_SERVICE_FIELD = "addressed_service"
OPERATION_FIELD = "operation"
MANIFEST_FIELD = "manifest"
MANIFEST_DIGEST_FIELD = "manifest_sha256"
SUBMISSION_READER_FIELD = "submission_reader"

MANIFEST_READER = "MANIFEST"
COMMUNICATION_READER = "ECOSYSTEM_COMMUNICATION"
SUBMISSION_READERS = (MANIFEST_READER, COMMUNICATION_READER)
COMMUNICATION_CLASS = "ecosystem.communication"
COMMUNICATION_ID_FIELD = "communication_id"
BODY_FIELD = "body"

PROCESSOR_SELECTION = "NO_PROCESSOR_SELECTED_AT_THE_CAPABILITY_ADDRESS"
ROUTE_ADMISSIBILITY = "NOT_RESOLVED_AT_BOUNDARY_ROUTE_OWNER_IS_SDK"


def canon(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _refuse(predicate: str, detail: str) -> SystemExit:
    return SystemExit(predicate + ":" + detail)


def boundary(root: Path) -> dict[str, Any]:
    path = Path(root) / BOUNDARY_PATH
    if not path.is_file():
        raise _refuse("ORGANIZATION_DECLARES_ITS_CAPABILITY_BINDINGS", str(path))
    return json.loads(path.read_text(encoding="utf-8"))


def bindings(root: Path) -> list[dict[str, Any]]:
    declared = (boundary(root).get("ingress") or {}).get(BINDINGS_FIELD)
    if not isinstance(declared, list):
        raise _refuse("CAPABILITY_BINDINGS_ARE_DECLARED_AS_A_LIST", BINDINGS_FIELD)
    return [entry for entry in declared if isinstance(entry, dict)]


def binding_for_service(root: Path, service_id: Any) -> dict[str, Any]:
    """The one overlay binding that names this service as its address."""
    matches = [entry for entry in bindings(root)
               if (entry.get("receiving_operation") or {}).get(ADDRESSED_SERVICE_FIELD) == service_id]
    if not matches:
        raise _refuse("ADDRESS_SERVES_A_CAPABILITY_THE_OVERLAY_BINDS_HERE", str(service_id))
    if len(matches) > 1:
        raise _refuse("ADDRESS_SERVES_EXACTLY_ONE_BOUND_CAPABILITY", str(service_id))
    return matches[0]


def resolve(root: Path, service: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve the receiving operation the overlay binds to this address."""
    binding = binding_for_service(root, service.get("service_id"))
    receiving = binding.get("receiving_operation") or {}
    operation_id = receiving.get("operation_id")
    relative = receiving.get(OPERATION_FIELD)
    if not operation_id or not relative:
        raise _refuse("BOUND_RECEIVING_OPERATION_DECLARES_ITS_ID_AND_MODULE",
                      str(binding.get("profile_id")))
    path = (Path(root) / str(relative)).resolve()
    try:
        path.relative_to(Path(root).resolve())
    except ValueError:
        raise _refuse("RECEIVING_OPERATION_IS_OWNED_INSIDE_THIS_ORGANIZATION", str(relative)) from None
    if not path.is_file():
        raise _refuse("RECEIVING_OPERATION_MODULE_EXISTS", str(relative))
    return {
        "capability_profile_id": binding.get("profile_id"),
        "capability_profile_name": binding.get("profile_name"),
        "capability_operation": binding.get("operation"),
        "payload_schema": binding.get("payload_schema"),
        "receiving_operation_id": operation_id,
        "receiving_operation_module": str(relative),
        "receiving_operation_path": path,
        "addressed_service": service.get("service_id"),
        "resolution_source": "ORGANIZATION_CAPABILITY_OVERLAY",
        "resolved_from_the_addressed_row_alone": False,
        "authority_constraint": "NONE_RESOLUTION_ONLY",
        "time_constraint": "HEARTBEAT_EPOCH",
        "disposition_is_transition_evidence": True,
    }


def load_operation(resolved: Mapping[str, Any]):
    """Load the bound operation and refuse one that is not the operation bound."""
    path = resolved["receiving_operation_path"]
    spec = importlib.util.spec_from_file_location("bound_receiving_operation", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise _refuse("RECEIVING_OPERATION_IS_LOADABLE_HERE",
                      type(exc).__name__ + ": " + str(exc)) from None
    declared = getattr(module, "OPERATION_ID", None)
    if declared != resolved["receiving_operation_id"]:
        raise _refuse("MODULE_IDENTIFIES_ITSELF_AS_THE_BOUND_OPERATION",
                      str(declared) + " is not " + str(resolved["receiving_operation_id"]))
    return module


def submission_reader(binding: Mapping[str, Any]) -> Any:
    """The reader the overlay names.

    ``SUBMIT_MANIFEST`` may omit the field and is read as a manifest. Any other
    operation must name its reader. Inheriting the manifest reader would treat
    a communication as a manifest and refuse it for the wrong reason.
    """
    declared = binding.get(SUBMISSION_READER_FIELD)
    if declared is None and binding.get("operation") == "SUBMIT_MANIFEST":
        return MANIFEST_READER
    return declared


def declared_communication(binding: Mapping[str, Any], payload: Any) -> dict[str, Any]:
    """The ecosystem communication this payload submits, or a refusal."""
    if not isinstance(payload, Mapping):
        raise _refuse("SUBMISSION_IS_AN_ECOSYSTEM_COMMUNICATION", "payload is not an object")
    if payload.get("message_class") != COMMUNICATION_CLASS:
        raise _refuse("SUBMISSION_DECLARES_A_RESPONDED_REQUEST_CLASS",
                      str(payload.get("message_class")))
    identifier = payload.get(COMMUNICATION_ID_FIELD)
    if not isinstance(identifier, str) or not identifier.strip():
        raise _refuse("SUBMISSION_CARRIES_THE_COMMUNICATION_ID_CLOSURE_CORRELATES_ON",
                      repr(identifier))
    body = payload.get(BODY_FIELD)
    schema = binding.get("payload_schema")
    if not isinstance(body, Mapping) or body.get("schema") != schema:
        raise _refuse("SUBMISSION_BODY_DECLARES_THE_BOUND_PAYLOAD_SCHEMA",
                      str(body.get("schema") if isinstance(body, Mapping) else body))
    return dict(payload)


def submission_for(binding: Mapping[str, Any], payload: Any) -> dict[str, Any]:
    """Read the submission the way the binding's reader says, or refuse."""
    reader = submission_reader(binding)
    if reader == MANIFEST_READER:
        return declared_manifest(payload)
    if reader == COMMUNICATION_READER:
        return declared_communication(binding, payload)
    raise _refuse("SUBMISSION_READER_IS_ONE_THIS_RESOLVER_IMPLEMENTS", str(reader))


def declared_manifest(payload: Any) -> dict[str, Any]:
    """The manifest this payload submits, or a refusal."""
    if not isinstance(payload, Mapping):
        raise _refuse("SUBMISSION_CARRIES_A_MANIFEST", "payload is not an object")
    manifest = payload.get(MANIFEST_FIELD)
    if not isinstance(manifest, Mapping):
        raise _refuse("SUBMISSION_CARRIES_A_MANIFEST",
                      "payload declares no " + MANIFEST_FIELD)
    carried = payload.get(MANIFEST_DIGEST_FIELD)
    if carried is not None:
        computed = "sha256:" + hashlib.sha256(canon(dict(manifest))).hexdigest()
        if str(carried) != computed:
            raise _refuse("CARRIED_MANIFEST_DIGEST_MATCHES_THE_MANIFEST", str(carried))
    return dict(manifest)


def receive(root: Path, service: Mapping[str, Any], packet: Mapping[str, Any], *,
            registry: Mapping[str, Any], mesh_root: Path | None = None) -> dict[str, Any]:
    """Hand an addressed submission to the receiving operation the overlay binds.

    The operation records its own disposition at both ledger levels. A refusal
    is returned as the operation stated it.
    """
    resolved = resolve(root, service)
    binding = binding_for_service(root, service.get("service_id"))
    submission = submission_for(binding, packet.get("payload"))
    module = load_operation(resolved)
    result = module.receive(submission, registry=registry, standing=packet.get("standing"),
                            packet_id=str(packet.get("packet_id") or resolved["receiving_operation_id"]),
                            mesh_root=mesh_root)
    return {
        "capability_received": True,
        "capability_profile_id": resolved["capability_profile_id"],
        "capability_operation": resolved["capability_operation"],
        "receiving_operation_id": resolved["receiving_operation_id"],
        "receiving_operation_module": resolved["receiving_operation_module"],
        "capability_resolution_source": resolved["resolution_source"],
        "processor_selection_at_this_address": PROCESSOR_SELECTION,
        "declared_capability_processed_at_this_address": False,
        "declared_capability_processed_by": resolved["receiving_operation_id"],
        "route_admissibility": ROUTE_ADMISSIBILITY,
        "address_constrains_admission": False,
        "authority_constraint": "NONE_RESOLUTION_ONLY",
        "time_constraint": "HEARTBEAT_EPOCH",
        "disposition_is_transition_evidence": True,
        "receiving_operation_result": result,
    }
