"""Six-field non-ALLOW records (package-defined: stegverse.sv011.entity-disposition/v0)."""
from __future__ import annotations

from typing import Any

DISPOSITION_SCHEMA = "stegverse.sv011.entity-disposition/v0"
OWNING_GOAL = "StegVerse-org/.github#167 (SV-011 lane B); SV-011/.github#68"
RETRY = "python3 -m sv011_entity run --package <package_dir> --record <record_dir> --request <request.json>"
SIX = ("failure_code", "failed_predicate", "required_evidence_or_repair", "retry_entrypoint",
       "owning_existing_goal", "next_attempt")


def non_allow(disposition: str, code: str, predicate: str, repair: str, next_attempt: str,
              retry: str = RETRY, **extra: Any) -> dict[str, Any]:
    if disposition not in ("DENY", "FAIL_CLOSED"):
        raise ValueError(disposition)
    return {"schema": DISPOSITION_SCHEMA, "disposition": disposition, "failure_code": code,
            "failed_predicate": predicate, "required_evidence_or_repair": repair,
            "retry_entrypoint": retry, "owning_existing_goal": OWNING_GOAL,
            "next_attempt": next_attempt, **extra}


def allow(**extra: Any) -> dict[str, Any]:
    return {"schema": DISPOSITION_SCHEMA, "disposition": "ALLOW", **extra}
