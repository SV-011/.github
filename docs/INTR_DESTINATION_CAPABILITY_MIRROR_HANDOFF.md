# InTr Destination Capability Mirror Handoff

**Repository:** `SV-011/.github`
**Mirrors:** [StegVerse-org/.github#175](https://github.com/StegVerse-org/.github/pull/175), [StegVerse-Labs/.github#3200](https://github.com/StegVerse-Labs/.github/pull/3200)
**Coordinating issue:** [StegVerse-Labs/.github#2897](https://github.com/StegVerse-Labs/.github/issues/2897)
**Capability:** `intr-destination` / `InTr:Destination` / `DIAGNOSTIC_CROSSING`
**Address:** `sv-011.intr-destination` → `ORGANIZATION_INTR_DESTINATION_INGRESS`
**Operation:** `resident-runtime/intr_destination_ingress.py`
**Boundary document:** `org-runtime/interlock-intr.json` → `ingress.capability_endpoint_bindings`, `egress.peer_capability_resolution`
**Status:** ADVERTISED_AND_SERVED_HERE_PEER_INSTALLATION_UNPROVEN

## Why this capability exists

A peer could be addressed at a purpose route or at `org-control`. Neither is a
universal destination. A diagnostic that only asks whether an organization is
reachable as an InTr destination had to borrow one of those, and a borrowed
purpose route reads as that purpose.

`intr-destination` is the mesh primitive those routes sit on. An organization
can advertise it. A crossing that uses it is a purpose-neutral diagnostic: an
`ecosystem.communication` whose body schema is
`stegverse.org-intr-destination-diagnostic/v1`. It acknowledges. It does not
select admission, routing, execution, or a processor.

Authority and time are constraint variables. A disposition is transition
evidence.

## What is declared here

The overlay names the binding. One binding, owned here:

```text
profile_id     intr-destination
profile_name   InTr:Destination
operation      DIAGNOSTIC_CROSSING
reader         ECOSYSTEM_COMMUNICATION
address        sv-011.intr-destination
role           BOUNDARY_LOCAL_CAPABILITY_INGRESS
operation_id   ORGANIZATION_INTR_DESTINATION_INGRESS
authority      constraint variable, NONE_BINDING_ONLY
time           constraint variable, HEARTBEAT_EPOCH
disposition    transition evidence only
```

`address_constrains_admission` and `selects_processing_at_this_address` are
false on the registry row. `selects_routing`, `selects_admission`, and
`selects_execution` are false on the binding. An address cannot name a
capability the overlay did not bind: `capability_ingress.receive` refuses a
row the overlay does not name, and it refuses a module whose `OPERATION_ID`
is not the one the binding publishes.

The profile name is local. The pinned SDK subsystem baseline does not declare
it, and `StegVerse-Labs/StegOS` `specs/universal-intr-connector-profiles.v1.json`
was not readable when this binding was added. This repository therefore does
not claim the canonical connector registry already named `InTr:Destination`.
The declaration owner is `SV-011/.github`.

`SUBMIT_MANIFEST` still uses the manifest reader when a binding names that
operation. This organization does not bind a manifest capability. The
diagnostic names `ECOSYSTEM_COMMUNICATION`, so a communication is not refused
for lacking a manifest and a manifest is not accepted as a diagnostic. The
validator refuses a non-manifest operation that tries to inherit the manifest
reader.

This repository had no mesh locator and no kernel dispatch. The locator is
`org-kernel/kernel.py::resolve_federation_root`. A missing root fails closed.
The kernel dispatches `BOUNDARY_LOCAL_CAPABILITY_INGRESS` through
`capability_ingress.receive`. The receiving operation records both ledger
levels. The kernel does not write a second receipt for the same frame.

Purpose-route destination bindings are absent. This change does not add them.

The canonical organization-boundary byte-copies stay byte-identical:

- `org-boundary/runtime/intr_transport.py`
- `org-boundary/runtime/process_boundary.py`
- `resident-runtime/org_boundary_runtime.py`

## What a peer address is

`resolve_destination(peer, capability="intr-destination")` derives
`organization_slug(peer) + "." + "intr-destination"`. Peer capability services
are not written into `federation.json`. The directory names organizations and
control services so an undeclared peer is refused. It does not list
`intr-destination`.

`peer_serves_this_capability_is_proven_here` stays false at resolution. An
unserved address does not close.

`proven_on_this_organization` names the one capability address this
organization serves, as a string:

```text
sv-011.intr-destination
```

A string names the one address a single-capability organization serves. A
peer address in that field is refused. The field is not a peer map.
`stegverse-org.intr-destination` is derived when this organization emits
outbound. It is not listed as proven here.

## What the crossing shows

| | |
| --- | --- |
| This organization advertises and serves the address | yes — a frame at `sv-011.intr-destination` is consumed and receipted at both ledger levels |
| The acknowledgement selects admission, routing, or execution | no |
| A peer address is derived, not listed in the directory | yes — outbound emission addresses `stegverse-org.intr-destination` |
| The peer installed the receiving operation | **not shown at resolution** |
| Something at the addressed service ran this packet | yes, **at closure**, when the far-side terminal receipt recomputes |
| What closure establishes | the responded request class and the `communication_id` |
| Which organization that boundary belongs to | **no** |
| The far side persisted the chain on its own ledger | **no** |
| No answer is a close | no — `close` returns `PENDING` and writes nothing |

The unit test's far side is this repository's `kernel.receipt` and
`build_control_response`. That is a fabricated answer in a temporary mesh. It
is not a live peer, and it is not an authentic production receipt.

A live round-trip with StegVerse-org needs a resident runtime on both sides.
This repository does not claim that observation.

## Verified

`tests.test_intr_destination_capability` covers the declaration, the overlay
fail-closed miswires, derived resolution, the refused undeclared capability,
receipt of a diagnostic at this address, emit `ALLOW`, close `PENDING` with no
answer, and close `ALLOW` when the fabricated far side answers. Closure
assertions name the responded request class and the `communication_id`.

`org-runtime/runtime_boundary.py validate` includes `capability_addresses_resolve`
and `peer_capability_addresses_derive`.

Both ledger roots in the unittest and in CI are redirected. The mesh is a
temporary directory.

## What other organizations do next

StegVerse-org/.github#175 is the template. StegVerse-Labs/.github#3200 is the
first mirror. Other federation organizations mirror the same binding in their
own `.github` repositories. This change does not install the capability for
them, and it does not claim they already serve it.

Authority and time remain constraint variables. Nothing recorded here selects
admission, routing, or execution.
