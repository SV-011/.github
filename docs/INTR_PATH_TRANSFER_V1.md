# InTr mesh path transfer v1

`resident-runtime/intr_path_transfer.py` checks every declared InTr mesh path by running a real transfer
transition on main and reading back the path's own sealed receipt.

**Declared paths.** Every ordered pair of distinct organizations whose destination documents conform to
`data/intr-protocol/v1/protocol.json` in the pinned mesh snapshot, for the one declared capability,
`intr-destination`. With 8 conformant organizations that is 56 paths (`<SOURCE>-><DESTINATION>:intr-destination`).

**Transition.** A row of `.stegverse/intr-path-transfer/transfers.json` (one per path from this organization)
lands on main. The push's main-merge transfer is ledgered by `organization-ledger-merge-transfer.yml`, and then
`.github/workflows/intr-path-transfer.yml` runs (`workflow_run` on that workflow's completion for a push; no
dispatch, schedule or cross-organization call). For each row not yet ledgered it appends one repository receipt
and one organization receipt (operation `INTR_MESH_PATH_TRANSFER`) through the existing two-level append, at the
ledger head's supplied epoch. The repository receipt's evidence (`stegverse.intr-path-transfer-evidence/v1`)
carries the protocol-conformant InTr message, the derived destination address, the transfer commit, and the seal
of that commit's main-merge transfer organization receipt. A ledgered row is answered from the ledger.

**Verification** (`python resident-runtime/intr_path_transfer.py verify`), two halves:

| half | ALLOW when | absent / unsealed | contradicted |
|---|---|---|---|
| source | sealed path organization receipt, linked to its sealed repository receipt, linked to the transfer commit's sealed main-merge transfer receipt | FAIL_CLOSED `INTR_PATH_RECEIPT_NOT_LEDGERED` | DENY `INTR_PATH_RECEIPT_LINK_UNVERIFIED` |
| destination | the destination ledger's sealed `ORGANIZATION_INTR_DESTINATION_INGRESS` receipt for the same path_id and communication_id, linked to its repository receipt, both carrying the source organization and repository receipt seals, read back from the destination ledger | FAIL_CLOSED `INTR_PATH_CROSS_ORG_READ_CREDENTIAL_NOT_ISSUED` (no readback; class B) or `INTR_PATH_DESTINATION_RECEIPT_NOT_LEDGERED` | DENY `INTR_PATH_DESTINATION_RECEIPT_LINK_UNVERIFIED` |

A path is ALLOW (VERIFIED) only when both halves verify. Every non-ALLOW carries the six fields.

**Destination carrier** (`resident-runtime/intr_path_ingress.py`). The destination organization's `.github`
carries `.stegverse/intr-path-transfer/inbound.json` (`stegverse.intr-path-inbound/v1`): per row, the path_id,
the communication_id and copies of the source's sealed organization and repository path receipts. When a row
lands on the destination's main, `.github/workflows/intr-path-ingress.yml` runs after that push's main-merge
transfer is ledgered (workflow_run, no dispatch). It checks the carried pair (sealed, linked, naming this path,
this destination and its kernel-derived address, a message the destination ingress accepts), then appends
through `resident-runtime/intr_destination_ingress.py` `_append` its own `ORGANIZATION_INTR_DESTINATION_INGRESS`
receipt (`stegverse.intr-path-ingress-evidence/v1`, transition class `INTR_MESH_PATH_INGRESS`) whose
boundary_evidence and repository evidence carry both source seals. A ledgered row is answered from the ledger.

| check | refusal |
|---|---|
| carried pair absent or unsealed | FAIL_CLOSED `INTR_PATH_SOURCE_RECEIPT_NOT_CARRIED` (nothing appended) |
| carried pair names another path, destination, address or message, or the receipts do not link | DENY `INTR_PATH_CARRIED_RECEIPT_CONTRADICTED` (nothing appended) |
| carried pair's inclusion in the source ledger | FAIL_CLOSED `INTR_PATH_CROSS_ORG_READ_CREDENTIAL_NOT_ISSUED`, recorded in the evidence as `source_inclusion` |

**End to end** (`python resident-runtime/intr_path_ingress.py end-to-end --source-readback S --destination-readback D`)
takes a readback of each ledger (`stegverse.intr-path-ledger-readback/v1`: tip, head seal, chain, the pair). It is
ALLOW only when the source pair is sealed and included in the source chain, the destination pair is sealed and
included in the destination chain, and the destination pair carries the source seals. Without either readback it
is FAIL_CLOSED `INTR_PATH_CROSS_ORG_READ_CREDENTIAL_NOT_ISSUED` (six fields; retry: StegVerse-Labs/TVC
least-privilege issuance of contents:read on the other organization's ledger ref; owning goal #162 under TV/TVC
custody).

**Class B.** In CI the other organization's ledger is not readable: `GITHUB_TOKEN` is scoped to the repository and
the repositories are internal. No token is minted and no `FEDERATION_*` token is used. Paths whose source is
another organization also need this transition installed in that organization's `.github`.

**Real state** (`tests/fixtures/intr-path-transfer/expected-real-state.json`, ledger tip `0db6e0bd`): 56/56
FAIL_CLOSED `INTR_PATH_RECEIPT_NOT_LEDGERED`. No path receipt exists yet. Path receipts appear only once this
transition lands on main. After that, the 7 paths from StegVerse-org verify their source half, and every path
stays FAIL_CLOSED at the destination half (`INTR_PATH_CROSS_ORG_READ_CREDENTIAL_NOT_ISSUED`, class B). Receipts in
the tests and the PR dry-run are written to temporary directories or a scratch ref. They are not ledgered and
not authentic.
