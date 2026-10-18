#!/usr/bin/env python3
"""The six fields every non-ALLOW result in this organization carries.

Source: ``StegVerse-org/.github@c483d441``
``docs/ORGANIZATION_ROLE_RUNTIME_REALITY_DEPLOYMENT.md``, Conformance standard,
item 5: "A non-ALLOW result carries ``failure_code``, ``failed_predicate``,
``required_evidence_or_repair``, ``retry_entrypoint``, ``owning_existing_goal``
and ``next_attempt``; a bare ``BLOCKED``, ``UNKNOWN`` or ``NOT_OBSERVED`` is not
a finding."

This module builds and checks those fields. It appends nothing, opens no
ledger, and selects no admission, routing or execution. A disposition is
evidence that a state transition occurred; Authority and Time stay constraint
variables.
"""
from __future__ import annotations

from typing import Any, Mapping

SIX_FIELDS = (
    "failure_code",
    "failed_predicate",
    "required_evidence_or_repair",
    "retry_entrypoint",
    "owning_existing_goal",
    "next_attempt",
)
NON_ALLOW_DISPOSITIONS = ("DENY", "FAIL_CLOSED")
TRANSITION_DISPOSITIONS = ("ALLOW",) + NON_ALLOW_DISPOSITIONS

# Existing goals, quoted from the registries the handoff reconciles. No new
# goal id is declared here.
# StegVerse-org/.github@c483d44 orchestration/task-registry.json (issue #68).
LEDGER_GOAL = "SV011-TT-CAPABILITY-MAP-OBSERVATION-001"
# StegVerse-Labs/.github@3deb699 control/organization-task-registry.json.
BOUNDARY_GOAL = "ORG-FED-SV-011-001"


def missing(document: Any) -> list[str]:
    """The six fields that are absent or empty in ``document``."""
    if not isinstance(document, Mapping):
        return list(SIX_FIELDS)
    return [name for name in SIX_FIELDS
            if not isinstance(document.get(name), str) or not document.get(name).strip()]


def fields(*, failure_code: str, failed_predicate: str, required_evidence_or_repair: str,
           retry_entrypoint: str, owning_existing_goal: str, next_attempt: str) -> dict[str, str]:
    """Return the six fields, refusing an empty one."""
    built = {
        "failure_code": failure_code,
        "failed_predicate": failed_predicate,
        "required_evidence_or_repair": required_evidence_or_repair,
        "retry_entrypoint": retry_entrypoint,
        "owning_existing_goal": owning_existing_goal,
        "next_attempt": next_attempt,
    }
    absent = missing(built)
    if absent:
        raise ValueError("NON_ALLOW_SIX_FIELDS_PRESENT:" + ",".join(absent))
    return built


def result(disposition: str, **six: str) -> dict[str, str]:
    """A disposition outside ALLOW together with its six fields."""
    if disposition not in NON_ALLOW_DISPOSITIONS:
        raise ValueError("NON_ALLOW_DISPOSITION:" + repr(disposition))
    return {"disposition": disposition, **fields(**six)}
