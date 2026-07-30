# Card-type harvest brief

Executable brief for the on-fabric survey that shapes the model domain's
card types. This is the entry work of Sprint 2 in the
[implementation plan](DOMAIN_MODEL_IMPLEMENTATION_PLAN.md), extracted here
so it can start immediately: it is read-only, needs no schema or code from
any sprint, and its output informs everything downstream — entity kinds and
checklists (Sprint 2), which relations the link vocabulary needs, what the
monitoring importer will find (Sprint 6), and which prose pages should
ripple on which entities (Sprint 7).

## Principle

Card types are **harvested, not invented**. A kind earns an enforced
checklist only when several real instances on the fabric fill it; a thing
with one instance stays a loosely-validated card until it has siblings.
Frequency decides; taste does not.

## Method

An agent with read access to the fabric sweeps the sources below. Rules:

1. **Read-only.** The harvest mutates nothing — no config, no corpus.
2. **No secret values.** Record field *names* and shapes, never values, for
   anything credential-adjacent (`POSTGRES_PASSWORD: <required env>` — the
   name is the finding). Output passes the canonical redaction transform
   before it is published anywhere.
3. **Count everything.** For each candidate kind: instances found, and for
   each field, in how many instances it appears. The frequency table is the
   deliverable, not an opinion about which fields matter.
4. **Record relationships as observed verbs.** Every time one thing points
   at another (depends_on, proxied-by, stored-in, watched-by, runs-on,
   member-of-LAN), note it — this harvests the link vocabulary alongside
   the card types.
5. **Note naming conventions and aliases.** Conventions are identity
   material (e.g. node names like `px5-lemans`; VMID prefixes tracking the
   host node, as in the 2513 → 5513 renumbering). Capture them explicitly:
   they become `entity_key` rules.

## Sources to sweep

| Source | What it yields |
| --- | --- |
| `docker-compose*.yml` across hosts | SERVICE candidates: image/build, ports, env keys, volumes, healthchecks; `depends_on` → dependency wires |
| systemd units | SERVICE candidates outside Docker: exec, user, restart policy, wants/after wires |
| Proxmox (`qm list`, VM/CT configs) | NODE and VM/CT candidates: VMID, host node, cores/RAM, NICs, MACs, passthrough hardware, onboot |
| Reverse-proxy configs (nginx/Caddy/Traefik) | ROUTE candidates: hostname, upstream, TLS; route → service wires |
| DNS zones / DHCP reservations | NETWORK and address candidates: LANs, VLANs, reservations, naming conventions |
| Prometheus/Grafana config | MONITORING_RULE candidates and rule → target wires (pre-seeds Sprint 6's importer) |
| SSH configs and `authorized_keys` restrictions | ACCESS-shaped facts: aliases, source-IP restrictions, peer-SSH wires |
| Database instances | DATABASE/SCHEMA candidates: engine, version, owning service (pre-seeds the `tbls` exemplar) |
| The DocPlane corpus itself | What prose repeatedly states about machines — the quick-reference tables in operations pages are hand-made cards already; their recurring rows are field votes |

## Output format

One harvest report (published as an `EVIDENCE` page, or attached to the
tracking initiative) containing, per candidate kind:

```text
kind: VM
instances_found: 14
fields:
  vmid:        14/14   # proposed required
  host_node:   14/14   # proposed required
  ip:          13/14   # proposed required
  lan:         13/14
  mac:         11/14
  onboot:      11/14
  passthrough:  2/14   # optional
identity_proposal: entity_key = vm name; vmid + host node as attributes
naming_conventions: VMID first digit tracks host node (2513 on px2 → 5513 on px5)
relations_observed: RUNS_ON node (14), MEMBER_OF lan (13), peer-SSH wire (2)
open_questions: is a stopped legacy shell (browan-fw-dev-legacy) a card or a state?
```

Plus two cross-kind sections: the observed relation verbs with counts
(candidate link vocabulary), and collisions/ambiguities (same name, two
things — like the duplicate `browan-fw-dev` that survived a migration).

## Ratification rule

A kind's checklist is proposed for enforcement only when **at least three
real instances** fill every proposed-required field. Fields present in fewer
than half the instances are optional. Kinds with one or two instances are
recorded but stay unratified.

## Seed findings (from the DocPlane repository alone)

Recorded here so the on-fabric sweep starts from evidence, not zero. Marked
as seed — counts restart when the real sweep runs:

- **SERVICE** (from `docker-compose.yml`, 6 instances): image-or-build,
  restart policy, env keys, ports, volumes, healthcheck; `depends_on` with
  conditions as dependency wires; one-shot init services suggest a
  `lifecycle: oneshot` attribute rather than a separate kind.
- **VM** (from the browan-fw-dev correction note, 2 instances): VMID, host
  node, IP, LAN, SSH access path, MAC, onboot, passthrough hardware,
  peer-SSH source-IP restrictions. The note also demonstrates the ripple
  case Sprint 7 exists for: one VM move staled five documented facts and a
  neighbouring page's peer table.
- **Relations observed**: `DEPENDS_ON` (compose), `RUNS_ON` (VM → node),
  `MEMBER_OF` (VM → LAN), peer-SSH wires, and route → upstream wires
  implied by the front-proxy config.

## Where results land

Until the model schema exists, the harvest output is ordinary DocPlane
content: an initiative (or issue) tracks the work, and the report publishes
as evidence. Sprint 2 then turns ratified checklists into
`GET /api/v1/model/contracts` entries and backfills the first cards from
the harvest data itself — the survey doubles as the initial population
census.
