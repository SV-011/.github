# Organization Role conformance: SV-011/.github

Measured 2026-10-17 against `StegVerse-org/.github@c483d441`
`docs/ORGANIZATION_ROLE_RUNTIME_REALITY_DEPLOYMENT.md` (Conformance standard and
Exemption path). This file records how this repository meets that standard. It
does not deploy the Organization Role declaration for SV-011; that declaration
is StegVerse-org's, and an SV-011 declaration is an owner decision.

## The six fields

Standard item 5: "A non-ALLOW result carries `failure_code`, `failed_predicate`,
`required_evidence_or_repair`, `retry_entrypoint`, `owning_existing_goal` and
`next_attempt`; a bare `BLOCKED`, `UNKNOWN` or `NOT_OBSERVED` is not a finding."

`resident-runtime/non_allow.py` builds and checks them. Owning goals are
existing registry tasks only: `SV011-TT-CAPABILITY-MAP-OBSERVATION-001`
(StegVerse-org/.github#68) for the ledger intake and readback, and
`ORG-FED-SV-011-001` (StegVerse-Labs registry) for the intr-destination boundary.

| Surface | Non-ALLOW outcome | Six fields |
| --- | --- | --- |
| `organization_ledger_transition.py` | refused request: `FAIL_CLOSED` `ORGANIZATION_TRANSITION_REQUEST_REFUSED`, nothing appended | top level |
| `organization_ledger_readback.py` | refused readback (no disposition; observation only) | top level |
| `organization_ledger_readback.py` | basis not established (`BASIS_ABSENT`, `BASIS_UNKNOWN`, `BASIS_STALE`, `BASIS_CONTRADICTORY`) | `current_state_finding` |
| `intr_destination_ingress.py` | `DENY` / `FAIL_CLOSED` diagnostic | result (receipt bytes unchanged) |
| `organization_egress_boundary.py` | emit `DENY`, closure `DENY`, no answer yet `FAIL_CLOSED` `FAR_SIDE_ANSWER_NOT_MATERIALIZED` | result and new records |
| `organization-ledger-transition.yml` | every `FAIL_CLOSED` line it prints | inline JSON |

An applied request records `disposition: ALLOW` in both receipts' evidence as
evidence that the append occurred. A disposition does not satisfy the Authority
or Time constraint; both stay constraint variables.

## Receivers

An absent far-side answer is not a transition and writes nothing. It is
answered `FAIL_CLOSED` with the six fields, with
`receiver_unavailable_disposition: DURABLE_QUEUE_OR_EVENT_EPHEMERAL_MATERIALIZATION`
and `always_on_receiver_required: false` (also declared in
`org-runtime/interlock-intr.json`). Nothing waits on or polls a receiver, host or
external machine.

## Exemptions

`data/organization-role-exemption-register.json` holds the surfaces that do not
yet meet the standard, each as an actionable `FAIL_CLOSED` record with a review
date. The register confers no authority and cannot satisfy a terminal
predicate. As registered on 2026-10-17:

- N1: the dispatch-driven self-closure in `organization-ledger-transition.yml`.
- N8: ledger transport by the workflow token; no TV/TVC ledger operation exists.
- N11: the transition request names no manifested capability and route.

`tests/test_organization_role_conformance.py` checks the six fields on every
non-ALLOW path above and checks the register's shape.
