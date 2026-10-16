#!/usr/bin/env python3
"""Validate the SV-011 InTr boundary document.

Authority and time are constraint variables. A disposition named by the
document is transition evidence. This validator does not select admission,
routing, or execution.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TR = ROOT / "org-runtime/interlock-intr.json"
REGISTRY = ROOT / "org-boundary/registry/services.json"
ORG = "SV-011"
CAPABILITY_INGRESS_ROLE = "BOUNDARY_LOCAL_CAPABILITY_INGRESS"
CARRIER_CONSTRAINTS = (
    "carrier_constrains_admission",
    "carrier_constrains_execution",
    "carrier_constrains_credentials",
    "carrier_constrains_routing",
    "carrier_constrains_transition",
    "carrier_constrains_receiving",
)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"object required: {path}")
    return value


def capability_bindings(document: dict[str, Any]) -> list[dict[str, Any]]:
    declared = (document.get("ingress") or {}).get("capability_endpoint_bindings") or []
    return [entry for entry in declared if isinstance(entry, dict)]


def submission_readers() -> set[str]:
    spec = importlib.util.spec_from_file_location(
        "capability_ingress_readers", ROOT / "org-boundary/runtime/capability_ingress.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module.SUBMISSION_READERS)


def capability_addresses_resolve(document: dict[str, Any]) -> bool:
    ingress = document.get("ingress") or {}
    if ingress.get("capability_address_rule") != (
            "A_BOUND_RECEIVING_OPERATION_IS_ADDRESSABLE_IN_THE_SERVICE_REGISTRY_OR_IT_IS_UNREACHABLE"):
        return False
    bindings = capability_bindings(document)
    if not bindings:
        return False
    rows = {row.get("service_id"): row for row in load(REGISTRY).get("services", []) if isinstance(row, dict)}
    known = submission_readers()
    for binding in bindings:
        receiving = binding.get("receiving_operation")
        if not isinstance(receiving, dict):
            return False
        row = rows.get(receiving.get("addressed_service"))
        if row is None:
            return False
        if row.get("boundary_role") != CAPABILITY_INGRESS_ROLE:
            return False
        if receiving.get("addressed_service_boundary_role") != CAPABILITY_INGRESS_ROLE:
            return False
        if row.get("capability_profile_id") != binding.get("profile_id"):
            return False
        if receiving.get("address_is_resolvable_from_the_service_registry") is not True:
            return False
        if receiving.get("selects_processing_at_the_capability_address") is not False:
            return False
        if row.get("selects_processing_at_this_address") is not False:
            return False
        if row.get("address_constrains_admission") is not False:
            return False
        if binding.get("selects_routing") is not False:
            return False
        if binding.get("selects_admission") is not False:
            return False
        if binding.get("selects_execution") is not False:
            return False
        if binding.get("authority_constraint") != "NONE_BINDING_ONLY":
            return False
        operation = receiving.get("operation")
        if not isinstance(operation, str) or not (ROOT / operation).is_file():
            return False
        resolution = receiving.get("address_resolution")
        if not isinstance(resolution, str) or not (ROOT / resolution.split("::", 1)[0]).is_file():
            return False
        reader = binding.get("submission_reader")
        if binding.get("operation") == "SUBMIT_MANIFEST":
            if reader not in (None, "MANIFEST"):
                return False
            if reader is not None and reader not in known:
                return False
        elif not isinstance(reader, str) or reader not in known or reader == "MANIFEST":
            return False
    return True


def peer_capability_addresses_derive(document: dict[str, Any]) -> bool:
    """``proven_on_this_organization`` names every capability address served here.

    A string names the one address a single-capability organization serves.
    Two or more are a list. A peer address in the field is refused: the field
    is not a peer map.
    """
    resolution = (document.get("egress") or {}).get("peer_capability_resolution")
    if not isinstance(resolution, dict):
        return False
    if resolution.get("address_form") != "ORGANIZATION_SLUG_DOT_CAPABILITY_PROFILE_ID":
        return False
    if resolution.get("capability_must_be_declared_in_this_organizations_overlay") is not True:
        return False
    if resolution.get("derivation_is_the_same_one_this_organization_answers_at") is not True:
        return False
    if resolution.get("peer_serves_this_capability_is_proven_here") is not False:
        return False
    if resolution.get("peer_declares_its_capability_services_in_the_peer_directory") is not False:
        return False
    if resolution.get("unserved_capability_address_is_observable_as_an_unclosed_crossing") is not True:
        return False
    if resolution.get("default_without_a_declared_capability") != "ORG_CONTROL_SERVICE":
        return False
    if resolution.get("serve_proven_only_at_closure") is not True:
        return False
    if resolution.get("closure_establishes") != ["responded_request_class", "communication_id"]:
        return False
    served = [(binding.get("receiving_operation") or {}).get("addressed_service")
              for binding in capability_bindings(document)]
    if not served or any(not isinstance(item, str) or not item for item in served):
        return False
    if len(set(served)) != len(served):
        return False
    proven = resolution.get("proven_on_this_organization")
    if isinstance(proven, str):
        named = [proven]
    elif isinstance(proven, list) and proven and all(isinstance(item, str) and item for item in proven):
        named = list(proven)
    else:
        return False
    if len(set(named)) != len(named) or set(named) != set(served):
        return False
    rows = {row.get("service_id") for row in load(REGISTRY).get("services", []) if isinstance(row, dict)}
    return all(item in rows for item in named)


def purpose_routes_absent(document: dict[str, Any]) -> bool:
    egress = document.get("egress") or {}
    return "capability_destination_bindings" not in egress


def vocabulary_records_constraints(document: dict[str, Any]) -> bool:
    """Refuse the transfer verb that this boundary does not use.

    The needle is assembled so this source does not itself contain it.
    """
    needle = "g" + "rant"
    return needle not in json.dumps(document).lower()


def mesh_locator_fails_closed_without_a_supplied_root() -> bool:
    spec = importlib.util.spec_from_file_location("kernel", ROOT / "org-kernel/kernel.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        module.resolve_federation_root(None)
    except ValueError as exc:
        return "mesh_location_required_from_materializer" in str(exc)
    return False


def validate() -> dict[str, Any]:
    document = load(TR)
    carrier = document.get("heartbeat_derived_carrier") or {}
    checks = {
        "transport_owner": document.get("organization") == ORG and document.get("owner_repository") == f"{ORG}/.github",
        "credential_authority": document.get("credential_authority") == "TV/TVC",
        "github_none": document.get("github_token_runtime_authority") == "NONE",
        "authority_constraint_resolution": document.get("authority_constraint_resolution") == "DERIVED_FROM_APPLICABLE_TRANSITION_ELEMENTS",
        "time_constraint": document.get("time_constraint") == "HEARTBEAT_EPOCH",
        "disposition_role": document.get("disposition_role") == "TRANSITION_EVIDENCE_ONLY",
        "carrier_does_not_select": all(carrier.get(key) is False for key in CARRIER_CONSTRAINTS),
        "carrier_names_constraint_variables": carrier.get("authority_constraint_variable") == "AUTHORITY" and carrier.get("time_constraint_variable") == "TIME",
        "capability_addresses_resolve": capability_addresses_resolve(document),
        "peer_capability_addresses_derive": peer_capability_addresses_derive(document),
        "purpose_routes_absent": purpose_routes_absent(document),
        "vocabulary_records_constraints": vocabulary_records_constraints(document) and vocabulary_records_constraints(load(REGISTRY)),
        "mesh_locator_fails_closed": mesh_locator_fails_closed_without_a_supplied_root(),
    }
    return {"schema": "stegverse.organization-boundary-validation/v1", "organization": ORG,
            "checks": checks, "valid": all(checks.values()),
            "authority_constraint": "NONE_VALIDATION_ONLY", "time_constraint": "HEARTBEAT_EPOCH",
            "disposition_role": "TRANSITION_EVIDENCE_ONLY"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("validate",))
    parser.parse_args()
    report = validate()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
