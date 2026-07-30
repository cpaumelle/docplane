# Card-type harvest report — corpus pass

First execution of the [card-type harvest brief](CARD_TYPE_HARVEST.md),
run off-fabric against a current-state dump of the live corpus
(947 pages: 513 active, 434 archived). This pass mines what the corpus
*claims*; the on-fabric pass verifies claims against reality and finds what
is running but undocumented. Field names and counts only — no values left
the analysis session.

## Ratified-candidate kinds (≥ 3 instances filling the required fields)

### NODE — physical hosts (6 instances)

Six hardware reference pages (four Proxmox nodes, two edge hosts) with a
strongly consistent card:

| Field | Frequency | Disposition |
| --- | --- | --- |
| cpu | 6/6 | required |
| memory | 6/6 | required |
| os (+ kernel) | 5/6 | required |
| chassis model | 5/6 | required |
| motherboard, bios version, boot mode | 5/6 | required |
| storage inventory (per-drive: device, size, model, serial, role) | 5/6 | required, nested list |
| service tag / serial | 4/6 | optional |
| tpm / secureboot | 2/6 | optional |

Nodes also carry cluster roles (Ceph OSDs, monitor, CRUSH weights) —
proposed as `MEMBER_OF` links to a cluster entity plus role attributes, not
flat fields.

### VM / CT — virtual machines and containers (12 card-bearing pages; ~77 pages mention VM identifiers)

| Field | Frequency | Disposition |
| --- | --- | --- |
| vmid | 8/12 | required |
| ip | 7/12 | required |
| hostname | 6/12 | required |
| host node | 5/12 | required (`RUNS_ON` link) |
| storage | 5/12 | optional |
| memory / vcpu / os | 2–3/12 | optional |
| access (ssh path) | 2/12 | optional |
| passthrough hardware | 1/12 | optional |

### SITE — physical/cloud locations (9 instances) — **not in the original kind list**

Site pages (three countries, a town site, a cloud provider with two hosts)
carry a network-boundary card the original design missed:

| Field | Frequency | Disposition |
| --- | --- | --- |
| lan subnets | 3/9 | required |
| wan address / public ip | 2/9 | required |
| gateway, dhcp server | 2/9 | required |
| sd-wan tunnel, wireguard port, public keys (names only), vpn fqdn | 2/9 | optional |
| nat/masquerade, control-plane edge id | 2/9 | optional |

### SERVICE — products and running services (132 service-doc pages across ~15 product subtrees)

The dominant convention is a **directory per product** with a recurring
document suite: `index`, `operations`, `architecture`, `api-reference`,
`runbook`, plus product-specific references. The SERVICE card therefore
needs fewer flat fields and stronger links: `RUNS_ON` (vm/node),
`STORES_IN` (database), `EXPOSES` (routes), plus a `doc_suite` convention
tying the entity to its know pages via `DESCRIBES`/`OPERATES` links. The
on-fabric pass (compose files, systemd units) supplies the flat fields
(image, ports, env keys) the prose rarely states.

## Recorded but not yet ratified

- **DEVICE_MODEL — IoT device references (exactly 3 instances)**: model,
  protocol, LoRaWAN class, uplink/downlink FPorts, DevEUI/JoinEUI prefixes,
  battery, device profile. At the ratification threshold; fields are
  consistent. Distinct from deployed device *instances*, which the corpus
  tracks in fleet/inventory pages — instance-vs-model needs an owner
  decision when the kind is ratified.
- **NETWORK / IDENTITY_CLASS**: the fabric-v2 doctrine defines an
  identity-native address plan (trusted / constrained / contractor classes
  with dedicated `/16` ranges) alongside per-site LANs. Networks are few
  but load-bearing; the fabric-v2 docs are effectively the authored card.

## Observed relation vocabulary (mention counts, active corpus)

All six proposed link verbs are attested in prose; frequency supports the
vocabulary as designed:

| Relation | Mentions |
| --- | --- |
| MEMBER_OF (LAN/VLAN/cluster membership) | 209 |
| WIRED_TO (peer SSH, tunnels) | 77 |
| STORES_IN (service → database) | 47 |
| WATCHES (monitored by …) | 41 |
| RUNS_ON (guest → node) | 31 |
| EXPOSES (proxied routes) | 17 |

## Naming conventions (identity material)

- Proxmox nodes: `px<N>-<name>` (named after racing circuits); edge hosts
  `edge1` / `fr-edge-1`.
- VMID first digit tracks the host node (a guest renumbered 2513 → 5513
  when moving px2 → px5).
- Address plan encodes trust and place: per-site LANs (e.g. UK vs FR
  second octet), plus fabric-wide identity-class `/16` ranges. Subnet
  membership is derivable from IP — the card can compute `MEMBER_OF`.
- Lifecycle marker present on 511/513 active pages — the deprecated
  vocabulary is fully pervasive; Sprint 8's backfill has near-total
  coverage to migrate from.

## Findings that adjust the architecture

1. **Add SITE, VM and DEVICE_MODEL to the entity-kind list.** SITE was
   entirely missing; VM deserves first-class distinction from NODE. The
   decision document's kind list is updated accordingly, and kinds remain
   data (checklist entries), not schema.
2. **The invariants register already exists in embryo.** 43 active pages
   under `control-plane/invariants/` and `control-plane/topology-invariants/`
   already use a stable ID convention: `i-<topic>-<n>`
   (e.g. `i-auth-transport-1`, `i-wg-watchdog-1`). Sprint 8 should
   **adopt this existing convention** rather than introduce a new `INV-n`
   scheme — the register work is consolidation and enforcement-pointer
   audit, not greenfield.
3. **The runbook stub purge already happened.** All 162 pages under the
   legacy `runbooks/` tree are archived; the 35 active runbooks have a
   median length near 10,000 characters and none under 400. Sprint 6's
   cleanup step is already done; remaining runbook debt is coverage and
   linking (which alert → which runbook), not stubs.
4. **`knowledge_class` is NULL on 936/947 pages.** The Sprint 8 backfill is
   effectively a full-corpus classification pass; the pervasive lifecycle
   marker provides a mechanical first cut (`REFERENCE` 274, `OPERATION`
   131, work-states 45, `ARCHIVED` 61 on active pages).

## What only the on-fabric pass can settle

- Verify claimed cards against reality (the corpus asserts; the fabric
  proves) — starting with the six NODE and twelve VM cards.
- Running-but-undocumented services: compose files and systemd units not
  represented by any page.
- The flat SERVICE fields (image, ports, env keys) prose rarely states.
- Prometheus/Grafana rule inventory (pre-seeds the Sprint 6 importer).
- DNS zones and DHCP reservations against the address-plan conventions.
- Which archived pages describe still-running things (documented-as-dead
  but alive is as important as alive-but-undocumented).
