"""Executable reference predicates for GTG-A1 through GTG-A8.

These predicates are conservative reference semantics for testing. They do not
create execution authority, certification, or universal admissibility.
"""

from __future__ import annotations

from typing import Any


def a1_transition_participation(case: dict[str, Any]) -> bool:
    """A1: a governed case must name a current state and candidate transition."""
    return bool(case.get("state_ref") and case.get("candidate_transition_ref"))


def a2_viable_successor_necessity(case: dict[str, Any]) -> bool:
    """A2: when continuation is necessary, at least one viable successor is represented."""
    necessary = any(
        item.get("status") in {"NECESSARY", "REQUIRED"}
        or item.get("result") in {"NECESSARY", "REQUIRED"}
        for item in case.get("necessity", [])
    )
    if not necessary:
        return True
    return any(
        item.get("status") not in {"IMPOSSIBLE", "DESTROYED"}
        and item.get("result") not in {"IMPOSSIBLE", "DESTROYED"}
        for item in case.get("viability", [])
    )


def a3_positive_standing_necessity(case: dict[str, Any], disposition: dict[str, Any]) -> bool:
    """A3: continuation dispositions require established standing and positive posture."""
    positive = disposition.get("disposition") in {
        "ALLOW", "RECOVER", "CONDITIONAL", "TRANSFORM"
    }
    if not positive:
        return True
    standing = disposition.get("standing_results", [])
    return bool(standing) and any(item.get("status") == "ESTABLISHED" for item in standing)


def a4_relational_determination(disposition: dict[str, Any]) -> bool:
    """A4: every result must retain a proof path and declared scope/horizon."""
    return bool(
        disposition.get("scope")
        and disposition.get("horizon")
        and disposition.get("proof_path")
    )


def a5_functional_relevance(disposition: dict[str, Any]) -> bool:
    """A5: a disposition must produce a governed consequence or explicit unresolved state."""
    result = disposition.get("disposition")
    if result == "UNRESOLVED":
        return bool(disposition.get("required_conditions") or disposition.get("recovery_conditions"))
    return result in {
        "ALLOW", "DENY", "FAIL_CLOSED", "RECOVER", "ESCALATE", "DEFER",
        "CONDITIONAL", "TRANSFORM", "SUSPEND", "TERMINATE"
    }


def a6_non_maximization(case: dict[str, Any], disposition: dict[str, Any]) -> bool:
    """A6: ALLOW cannot ignore coupled viability or above-threshold uncertainty."""
    if disposition.get("disposition") != "ALLOW":
        return True
    if disposition.get("uncertainty_status") != "WITHIN_THRESHOLD":
        return False
    threatened = any(
        item.get("status") == "THREATENED" or item.get("result") == "THREATENED"
        for item in case.get("viability", [])
    )
    return not threatened


def a7_purpose_inversion_prohibition(disposition: dict[str, Any]) -> bool:
    """A7: a positive result cannot carry an incompatible purpose result."""
    if disposition.get("disposition") in {"ALLOW", "RECOVER", "CONDITIONAL"}:
        return disposition.get("purpose_result", {}).get("status") not in {
            "INCOMPATIBLE", "INVERTED"
        }
    return True


def a8_model_reality_separation(disposition: dict[str, Any]) -> bool:
    """A8: governance output must preserve the non-authority boundary."""
    return disposition.get("authority_effect") is False


PREDICATES = {
    "GTG-A1": a1_transition_participation,
    "GTG-A2": a2_viable_successor_necessity,
    "GTG-A3": a3_positive_standing_necessity,
    "GTG-A4": a4_relational_determination,
    "GTG-A5": a5_functional_relevance,
    "GTG-A6": a6_non_maximization,
    "GTG-A7": a7_purpose_inversion_prohibition,
    "GTG-A8": a8_model_reality_separation,
}
