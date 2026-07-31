import json

LANDINGS = {}

LANDINGS["operations/incidents/index.md"] = {
    "title": "Incidents",
    "nav_path": "Operations/Incidents/Index",
    "content": """# Incidents

**Lifecycle:** REFERENCE
<!-- lifecycle: REFERENCE -->

Closed-incident evidence and postmortems -- root-cause records for specific outage or
near-miss events, kept for reference during future troubleshooting. These are evidence
records of what happened and why, not step-by-step recovery procedures; see
`operations/runbooks/` for the executable procedures some of these incidents led to.

## Items

- [2026-06-01 — wg-stg-01 stuck conntrack incident](2026-06-01-wg-stg-01-stuck-conntrack.md)
- [Incident: wg-rw Full-Tunnel Clients Strand on Endpoint Re-Resolution](2026-06-03-wg-rw-endpoint-split-horizon.md)
- [UK wg-site Outage — Half-Completed Key Rotation + a Stale-Cache Replay (Postmortem)](2026-07-uk-wg-site-key-split.md)
- [Proxmox Cluster-Wide Fencing — Dead QDevice (2026-07-29)](px-cluster-qdevice-fencing-2026-07-29.md)
- [Transit apply-transit — syncconf-on-absent rolls back after reboot](transit-apply-syncconf-on-absent.md)
- [Incident: transit-dispatch.sh truncated to zero bytes](transit-dispatch-zero-byte-2026-05-08.md)
""",
}

LANDINGS["operations/investigations/index.md"] = {
    "title": "Investigations",
    "nav_path": "Operations/Investigations/Index",
    "content": """# Investigations

**Lifecycle:** REFERENCE
<!-- lifecycle: REFERENCE -->

Discovery and investigative findings into a suspected gap, anomaly, or open question --
distinct from a closed-incident postmortem (`operations/incidents/`) and from an
executable procedure (`operations/runbooks/`).

## Items

- [B5a Dinard Discovery — FR Has D-6/D-7 Governance Debt](b5a-fr-discovery-2026-05-29.md)
- [PX-FABRIC-MEMBERSHIP-1](px-fabric-membership-1.md)
- [PX-FABRIC-MEMBERSHIP-2](px-fabric-membership-2.md)
""",
}

LANDINGS["operations/platform/index.md"] = {
    "title": "Platform",
    "nav_path": "Operations/Platform/Index",
    "content": """# Platform

**Lifecycle:** REFERENCE
<!-- lifecycle: REFERENCE -->

Operational procedures and reference for the Proxmox/Ceph/VM substrate underlying
CharlieHub, plus the direct-SQL enforcement gate that protects it.

## Items

- [Ceph Operations](ceph-operations.md)
- [Control Plane Enforcement — Direct-SQL Gate](control-plane-enforcement.md)
- [Daily Tasks](daily-tasks.md)
- [Infrastructure Reconciliation](infrastructure-reconciliation.md)
- [VM & CT Management](vm-ct-management.md)
""",
}

LANDINGS["operations/security/index.md"] = {
    "title": "Security",
    "nav_path": "Operations/Security/Index",
    "content": """# Security

**Lifecycle:** REFERENCE
<!-- lifecycle: REFERENCE -->

Security procedures and reference for CharlieHub's operational surface -- break-glass
access, hardening baselines, key rotation, incident response, and the redaction
disposition register.

## Items

- [Break-Glass Procedure](break-glass-procedure.md)
- [Container Hardening](container-hardening.md)
- [DocPlane redaction remediation register](docplane-redaction-remediation-register.md)
- [Secret Exposure Response Runbook](secret-exposure-response-runbook.md)
- [Security Maintenance](security-maintenance.md)
- [SSH Observation Post](ssh-observation-post.md)
- [TLS Certificate Strategy](tls-cert-strategy.md)
- [WG Key-Rotation Split Recovery](wg-key-rotation-split-recovery.md)
""",
}

LANDINGS["operations/runbooks/index.md"] = {
    "title": "Runbooks",
    "nav_path": "Operations/Runbooks/Index",
    "content": """# Runbooks

**Lifecycle:** REFERENCE
<!-- lifecycle: REFERENCE -->

Executable operator procedures for CharlieHub infrastructure -- grouped below by
reader purpose for navigability; the grouping is organizational only, not a new
classification scheme.

## Alert-response runbooks

Tied to a specific alerting rule or recurring failure signature.

- [EdgeWANUnreachable](edge-wan-unreachable.md)
- [VpnMetricsCounterResetChurn](vpn-metrics-counter-reset-churn.md)
- [VpnPeerHighChurn](vpn-peer-high-churn.md)
- [VpnPeerHighDataVolume](vpn-peer-high-data-volume.md)
- [VpnUserHighDataVolume](vpn-user-high-data-volume.md)
- [VpnUserHighUploadVolume](vpn-user-high-upload-volume.md)
- [WG Leaked-Key Rotation](wg-leaked-key-rotation.md)
- [WG site-tunnel stuck conntrack recovery](wg-site-tunnel-stuck-conntrack.md)

## Migration & cutover runbooks

- [Adding a New Site Subnet to Fabric Routing](add-site-subnet-fabric-routing.md)
- [Andorra Pi4 Gateway Migration](andorra-pi4-gateway-migration.md)
- [Runbook — Authelia managed-rules split cutover](authelia-managed-rules-split-cutover.md)
- [CCM Interface Realization — Step 6 Runbook](ccm-interface-realization-step6-runbook.md)
- [CCM-LAN subnet_id soak and Phase C](ccm-lan-subnet-id-soak.md)
- [CP-NET-1 Phase 3 — Host Resolver Migration (Linux)](cp-net-1-phase3-host-resolver.md)
- [Edge Trust Flatten Runbook](edge-trust-flatten.md)
- [Gateway Hardware Replacement](gateway-hardware-replacement.md)
- [Geo-Consumer Onboarding](geo-consumer-onboarding.md)
- [GEO Exit Provisioning](geo-exit-provisioning.md)
- [Interface Mothball & Reactivation](mothball-reactivation.md)
- [Pi4 Gateway Bootstrap Recipe](pi4-gateway-bootstrap-recipe.md)
- [Stage 5 Execution Runbook — vps3 edge cert rollout](stage-5-vps3-cert-rollout.md)
- [TM-LOCALHOST-REGISTRY-1 Phase H Runbook](tm-localhost-registry-phase-h.md)
- [UniFi Express — L3 Adoption (Remote Site, No L2 Adjacency)](unifi-express-l3-adoption.md)
- [UniFi OS Server Upgrade (VM1199)](uos-server-upgrade.md)

## Platform maintenance

- [Deployment Checklist](deployment-checklist.md)
- [DNS Re-push](dns-repush.md)
- [Firewall Rules Persistence (iptables / hub2)](iptables-persistence.md)
- [Hub2 Reboot](hub2-reboot.md)
- [Phase-8 Certification Runbook](phase-8-certification.md)
- [Phase-8 Certification — Defect Log](phase-8-defects.md)
- [Projection Drift Runbook](projection-drift.md)
- [px Rolling Reboot](px-rolling-reboot.md)
- [Runbook: ch-transit-core dispatcher break-glass recovery](transit-core-break-glass.md)
- [Schema Migrations](schema-migrations.md)
- [Troubleshooting](troubleshooting.md)

## Planning notes

Not yet executable procedures -- design/planning material housed here pending
completion.

- [Authelia Realm/Session API (Planning)](authelia-realm-api.md)
- [Part B — hub2 Pull-to-Deploy (Planning)](part-b-hub2-pull-to-deploy.md)
""",
}

json.dump(LANDINGS, open("/tmp/docplane-lifecycle-policy-audit/landings.json", "w"), indent=1)
for path, d in LANDINGS.items():
    print(path, len(d["content"]))
