# browan-fw-dev moved px2 → px5: VMID, node, IP and peer-SSH restrictions all stale

**Status:** PENDING
**Legacy page(s):** `services/microshare-dev/browan-fw-dev.md`
**Raised:** 2026-07-29 by claude-code

## What is wrong

The VM was moved from px2-monza to px5-lemans and re-numbered. Every identifier on the page now
points at the wrong machine:

| Field | Page says | Actual |
|---|---|---|
| VMID | 2513 | **5513** |
| Host node | px2-monza | **px5-lemans** |
| IP | `10.44.1.13` (UK LAN) | **`10.35.1.113`** (FR LAN) |
| SSH from fabric | `ssh root@10.44.1.13` | `ssh root@10.35.1.113` |
| SSH from Mac | `ssh 2513` | alias stale |

Two consequences beyond the identifiers:

1. **The VM1124 peer-SSH section is now non-functional as documented.** Both keys are
   source-IP restricted; the direction into VM1124 is pinned to `from="10.44.1.13"`, which the
   VM no longer has. The page presents this as working.
2. **A second VM with the same name existed on px2 until 2026-07-29.** Anyone following the page
   would have found `browan-fw-dev` on px2 (VMID 2513) and reasonably assumed it was the live
   machine. It is a stopped legacy shell.

The site change is not cosmetic: the VM has crossed from the UK LAN to the FR LAN, so anything
assuming UK-local reachability (`10.44.x`) or UK latency no longer holds.

## What it should say

Replace the quick-reference table values with VMID **5513**, host node **px5-lemans**, IP
**`10.35.1.113`**, and `ssh root@10.35.1.113` for fabric access. Update the Access section
accordingly.

Add to the Operational notes / Known issues section:

> **2026-07-29 — moved to px5-lemans, re-numbered 5513.** The VM crossed from the UK LAN to the
> FR LAN (`10.44.1.13` → `10.35.1.113`). The ST-LINKv2 was physically moved to px5 and `usb0`
> passthrough re-applied, per the warning in the USB passthrough section — verified working.
> The predecessor on px2 (VMID 2513) was renamed **`browan-fw-dev-legacy`**, had `onboot` set to
> `0`, and its MAC rotated to `BC:24:11:28:9B:42` so a px2 reboot cannot bring a duplicate MAC
> onto the fabric. It remains stopped and is not a fallback: its guest netplan matches the
> interface by the *old* MAC, so it would boot with no working network.

Amend the VM1124 peer-SSH section: the `from="10.44.1.13"` restriction on the key installed in
VM1124's `authorized_keys` must be updated to `from="10.35.1.113"` before that direction works
again. Flag as **broken until amended** rather than silently correcting the IP — the change has
to be made on VM1124, not in the docs.

Retain the USB passthrough warning verbatim — it correctly predicted this migration's
requirement and was followed.

Add one operational note to the ST-LINK verification section:

> The first `st-info --probe` after an idle period may fail with `LIBUSB_ERROR_TIMEOUT` /
> `LIBUSB_ERROR_PIPE` while the device re-enumerates. Re-run it; the second call returns the
> normal `unknown device` output. This is not a fault.

## Evidence

All commands run 2026-07-29.

Live VM on px5:

```
$ ssh px5 'qm config 5513 | grep -E "^(name|net0|onboot|cores)"; qm status 5513'
cores: 2
name: browan-fw-dev
net0: virtio=BC:24:11:91:34:90,bridge=vmbr0
onboot: 1
status: running

$ ssh px5 'qm agent 5513 network-get-interfaces'   # (parsed)
  enp6s18: 10.35.1.113/24
  docker0: 172.17.0.1/16
```

No VMID 5553 exists on any node; `browan-fw-dev` resolves only to 5513 (px5) and the renamed
2513 (px2). Identity confirmed by MAC `BC:24:11:91:34:90`, carried over from the original VM.

Legacy VM on px2, after the 2026-07-29 changes:

```
$ ssh px2 'qm config 2513 | grep -E "^(name|net0|onboot)"; qm status 2513'
name: browan-fw-dev-legacy
net0: virtio=BC:24:11:28:9B:42,bridge=vmbr0
onboot: 0
status: stopped
```

ST-LINK passthrough followed the VM and works:

```
$ ssh px5 'qm config 5513 | grep -i "^usb"'
usb0: host=0483:3748

$ ssh px5 'lsusb | grep 0483'
Bus 003 Device 004: ID 0483:3748 STMicroelectronics ST-LINK/V2

$ ssh px2 'lsusb | grep 0483'          # no longer on px2
  (no match)

# in the guest — first probe failed, retry succeeded
$ st-info --probe
[!] send_recv read reply failed: LIBUSB_ERROR_TIMEOUT
$ st-info --probe
  sram:       0
  chipid:     0x0000
  descr:      unknown device        # matches the documented no-board-attached output
```

`dmesg` in the guest shows repeated `usb 5-1: New USB device found, idVendor=0483,
idProduct=3748` re-enumeration events, consistent with the transient first-probe failure.

## Confidence

High on every identifier (VMID, node, IP, MAC, onboot, USB) — each is a direct command output
above.

Lower on two points, stated honestly:

- **VM1124 peer-SSH.** I inferred the breakage from the documented `from="10.44.1.13"`
  restriction plus the confirmed IP change. I did **not** test the SSH path in either direction,
  and did not inspect VM1124's `authorized_keys`. The fix belongs on VM1124 and should be
  verified there before the page is marked correct.
- **Transient ST-LINK probe failure.** Observed once and cleared on retry. I did not determine
  the root cause; the re-enumeration in `dmesg` is a plausible explanation, not a proven one.
  Recorded as an operational note rather than a diagnosis.

## Also worth folding in (lower priority)

The **Firmware source reference** contents table no longer matches
`/opt/microshare-pir-sdk-20260723/`. The tree has accumulated in-flight firmware working files
not in the original vendor drop — `config.c.atomicity-v22`, `config.c.orig-preatomicity`,
`config_atomicity_test.c`, `lmic.c.cw-clamp-v24`, `lmic.c.orig-preclamp`,
`lmic.c.txparam-ceiling-v23`, `lmic.h.cw-clamp-v24`, `tabs.c.cw-clamp-v24`,
`testmode.c.cw-clamp-v24`, plus `v10_tbms100-multiregion.md` and `v11_tbms100-multiregion.md`.

Two things follow. First, the page's "do not edit in place — copy out before modifying"
instruction is being worked around rather than followed, and the `.orig-pre*` files are the only
rollback for that work. Second, **the tree is not a git repository** (`git` is installed on the
VM; the directory simply has no `.git`), so none of this is version-controlled.

A new `docs/` subdirectory was added on 2026-07-29 containing `tbms12m-spec-v0.7.md` — a
fidelity-verified Markdown transcription of the v0.7 specification PDF, with its cover note. The
contents table currently lists only `TBMS12M Specifications v0.6.pdf`; the v0.7 transcription is
the machine-readable spec of record and should be referenced.

## How to apply on DocPlane

Single page, `services/microshare-dev/browan-fw-dev.md`:

1. Quick-reference table — VMID, hostname/node, IP, SSH lines.
2. Access section — replace both `10.44.1.13` occurrences and the `ssh 2513` alias.
3. VM1124 peer-SSH subsection — mark the `from="10.44.1.13"` direction broken pending an
   `authorized_keys` amendment on VM1124.
4. Operational notes — add the 2026-07-29 move entry (text above).
5. USB passthrough — keep the warning; add the transient-probe note to the verification block.
6. Firmware source reference — refresh the contents table, note the absence of version control,
   and reference `docs/tbms12m-spec-v0.7.md`.

Nothing here depends on legacy docs-api content, so this entry is self-contained: it does not
need a pre-decommission export of the source page.
