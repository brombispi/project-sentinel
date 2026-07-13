# Imaging Safety

This document is the canonical authority for forensic imaging safety in Project Sentinel.

It defines acquisition states, mount safety, source identity, resumable imaging, canonical image immutability, and ARCHIVE enforcement for ddrescue operations.

For the general Recovery Operation lifecycle, see `RecoveryOperationStandard.md`. For imaging-specific safety rules, this document takes precedence.

---

## Purpose

Forensic imaging creates the canonical acquisition artifact for a Recovery Case.

Imaging must:

- protect the customer's original storage device
- never image while descendant filesystems are mounted
- never silently overwrite or replace a completed canonical image
- preserve ddrescue resume capability for interrupted acquisitions
- distinguish incomplete imaging from completed imaging awaiting fingerprint
- record source identity before the first ddrescue run

---

## Artifacts

| Artifact | Path | Role |
|----------|------|------|
| Canonical image | `images/source.img` | Acquisition output; immutable after fingerprint |
| ddrescue map | `images/source.map` | Resume state; paired with canonical image |
| Acquisition identity | `evidence/acquisition_source.json` | Source identity recorded before first ddrescue run |
| Integrity evidence | `evidence/source.sha256` | Marks canonical acquisition complete |

---

## Acquisition State Machine

Sentinel classifies acquisition state from artifact presence and ddrescue map completion.

**Do not use absence of `source.sha256` alone to offer ddrescue resume.**

### State table

| State | Conditions | Sentinel action |
|-------|------------|-----------------|
| **1. No acquisition** | No `source.img`, no `source.map` | Mount gate → offer **new imaging** → write `acquisition_source.json` before first ddrescue |
| **2. Incomplete ddrescue** | Both `source.img` and `source.map`; no `source.sha256`; map **not finished** | Identity + mount gates → offer **resume only** |
| **3. Imaging complete, fingerprint missing** | Both `source.img` and `source.map`; no `source.sha256`; map **finished** | **No ddrescue resume** → offer **fingerprint retry** |
| **4. Completed canonical acquisition** | Both img and map; `source.sha256` exists | Refuse new imaging, resume, and overwrite |
| **5. Inconsistent artifacts** | Exactly one of `source.img` or `source.map` | Stop; explain; no guess, delete, overwrite, or resume |

### Classification flow

```
Both source.img and source.map absent
  → State 1

Exactly one present
  → State 5

source.sha256 present
  → State 4

source.sha256 absent, both img and map present
  → inspect ddrescue map (see Map completion below)
      map unreadable     → fail closed (no resume, no overwrite)
      current_status ≠ '+' → State 2 (resume)
      current_status == '+' → State 3 (fingerprint retry)
```

---

## ddrescue Map Completion

### Official mechanisms

Prefer official ddrescue tools over custom map parsing.

| Mechanism | Purpose |
|-----------|---------|
| **Mapfile status line** | ddrescue process phase — primary classifier for states 2 vs 3 |
| **`ddrescuelog -t mapfile`** | Validate map readability; exit **2** = corrupt or invalid |
| **`ddrescuelog -D mapfile`** | All blocks successfully finished (`+`) — for logging only, not state routing |

### Status line characters (GNU ddrescue Mapfile structure)

| Character | Meaning | Acquisition class |
|-----------|---------|-------------------|
| `?` | Copying non-tried blocks | Incomplete — State 2 |
| `*` | Trimming | Incomplete — State 2 |
| `/` | Scraping | Incomplete — State 2 |
| `-` | Retrying bad sectors | Incomplete — State 2 |
| `+` | Finished | Imaging complete — State 3 |

**Rule:** Use mapfile `current_status == '+'` to mean ddrescue has **finished its run**. Do **not** use `-D` alone for resume vs fingerprint routing. `-D` requires every block to be successfully finished and returns 1 when bad-sector blocks remain.

### Unreadable map (fail closed)

When any of:

- `ddrescuelog -t mapfile` exits **2**
- mapfile status line missing and cannot be interpreted safely (for example pre-1.6 map without safe normalization)
- mapfile unreadable due to I/O error

Sentinel must:

- not offer resume
- not delete or overwrite img or map
- not invoke ddrescue
- explain that map state is unreadable
- leave status `READY_FOR_IMAGING`
- log ECHO **ERROR**

---

## Canonical Image Immutability

Forensic imaging creates `images/source.img`.

**The canonical forensic image is immutable after `evidence/source.sha256` exists. No Recovery Operation may modify or replace it.**

Rules:

- State **4**: refuse new imaging, ddrescue resume, and overwrite.
- State **3**: imaging is complete; only fingerprint retry is permitted — not ddrescue.
- State **2**: resume continues the **same** img and map paths; this is not a new acquisition.
- State **1**: first creation only when neither img nor map exists.
- Later recovery operations use the canonical image read-only or operate on a disposable working copy (see TestDisk design in `Backlog.md`).

Fingerprinting reads the canonical image only. It does not alter the image.

---

## Resumable Imaging

### When resume is offered

Resume is offered **only in State 2** after:

1. acquisition state classification confirms incomplete map
2. source identity validation passes (see below)
3. mounted-descendant gate passes
4. explicit operator approval

### ddrescue invocation

Current approved command shape:

```bash
ddrescue -f -n <source_device> <case>/images/source.img <case>/images/source.map
```

| Flag | Role |
|------|------|
| `-n` | Copying phase first; resume continues from map |
| `-f` | Ignored when outfile is a regular file; retained for consistency |

Resume uses the **same** source device, `source.img`, and `source.map`. Do not create new acquisition files on resume.

### When resume is refused

- State **3** — imaging complete; fingerprint missing
- State **4** — canonical acquisition complete
- State **5** — inconsistent artifacts
- Unreadable map
- Identity mismatch or insufficient identity
- Any mounted descendant remains after unmount attempt
- Missing `acquisition_source.json` when img and map already exist

---

## Fingerprint Retry Behaviour

When State **3** is detected:

- **Do not** offer ddrescue resume.
- **Do not** invoke ddrescue.
- Offer fingerprint recording via existing `verify_forensic_image()` workflow.

Operator presentation (example):

```
Imaging is complete. SHA-256 fingerprint has not been recorded.
Sentinel will record the fingerprint now. ddrescue will not run again.

Proceed with fingerprinting? [y/N]
```

On approval, run fingerprint operation. On success, `evidence/source.sha256` is written and acquisition moves to State **4**.

If fingerprint fails, remain in State **3** on next workflow entry. Still do not offer ddrescue resume.

State **3** fingerprint retry uses a dedicated operator confirmation. It is not silently chained when re-entering the workflow after a prior fingerprint failure.

---

## acquisition_source.json

### Location

`evidence/acquisition_source.json`

Stored alongside integrity evidence, not in `case.json`.

### When written

Created **once**, immediately before the **first** ddrescue run (State 1 → new imaging), after operator approval and before subprocess start.

Not updated on resume. Serves as the immutable acquisition identity anchor.

### Contents

```json
{
  "serial": "WD-WMC...",
  "model": "WDC WD500...",
  "size_bytes": 500107862016,
  "path": "/dev/sdb",
  "timestamp": "2026-07-13T10:00:00"
}
```

| Field | Role |
|-------|------|
| `serial` | Primary identity when present and trustworthy |
| `model` | Supporting comparison |
| `size_bytes` | **Mandatory** exact match (`blockdev --getsize64` or equivalent) |
| `path` | Reference only — never sufficient identity alone |
| `timestamp` | Audit |

### Why not case.json

`case.json` stores human-readable rounded size (for example `14.6G`). **Never authorize resume using rounded size text.**

---

## Source Identity Validation

Applied before **resume** (State 2).

### Match rules

| Condition | Result |
|-----------|--------|
| `acquisition_source.json` missing when img+map exist | Refuse resume |
| Serial present on both sides, equal | Pass serial check |
| Serial present, mismatch | Refuse resume |
| `size_bytes` mismatch | Refuse resume |
| Model mismatch with matching serial and size | Refuse resume |
| Path differs, serial and size match | Allow with **WARNING** (device reorder) |
| Serial `Unknown` or empty on both record and current | Refuse automatic resume |
| Serial missing or unstable on one side only | Refuse automatic resume |

### Higher-risk override (missing serial only)

When serial is unavailable but `size_bytes` and `model` match:

- do not resume automatically
- optional separate operator confirmation with explicit high-risk warning
- log ECHO **OPERATOR** and **WARNING**

### Path rule

`/dev` path is never sufficient identity. Path may change across sessions; serial and `size_bytes` are authoritative.

---

## Mounted-Descendant Safety Gate

Applies to **new imaging** (State 1) and **resume** (State 2).

### Discovery

Use `lsblk -J` with `MOUNTPOINTS` where available; fall back to `MOUNTPOINT`.

```bash
lsblk -J -o NAME,PATH,TYPE,MOUNTPOINT,MOUNTPOINTS,FSTYPE,OPTIONS,PKNAME /dev/sdX
```

Supplement with:

```bash
findmnt -rn -S <PATH> -o TARGET,SOURCE,OPTIONS
```

Return a **deduplicated** list (by mount target) of:

- `device_path`
- `mount_target`
- `options` (`ro` / `rw`)
- `type` (`part`, `crypt`, `lvm`, etc.)
- `filesystem`

Include:

- all mounted descendants of the source disk
- read-only and read-write mounts
- multiple mount points per block device
- mapper and LUKS descendants

Exclude:

- Recovery Engine disk and its mounts
- approved destination Recovery Storage mount and subpaths

### Operator flow when mounts found

1. List every mounted path.
2. Explain that the operating system may write to mounted filesystems.
3. Ask whether Sentinel should unmount them: `[y/N]`
4. If approved, unmount deepest targets first.
5. Re-observe immediately.
6. If any descendant remains mounted, stop imaging and explain why.

### Unmount rules (MVP)

- Unmount filesystem mount targets only: `umount <target>`
- Unmount deepest mount target first
- **Do not** close LUKS mappings (`cryptsetup close`) in MVP
- **Never** unmount Recovery Engine
- **Never** unmount destination Recovery Storage

### ARCHIVE final guard

Immediately before ddrescue, ARCHIVE independently re-checks mounted descendants. If any remain, refuse imaging, log ECHO **ERROR**, return failure without invoking ddrescue.

---

## Module Ownership

| Responsibility | Owner |
|----------------|-------|
| Low-level storage queries | `storage_query.py` — pure `lsblk`, `findmnt`, `blockdev`, `ddrescuelog` |
| Observation wrappers | ARGUS |
| Acquisition state classification | Shared helper using artifact paths and map status |
| Presentation and operator approval | SENTINEL |
| Unmount execution | ARCHIVE |
| Pre-ddrescue enforcement | ARCHIVE |
| Fingerprint execution | ARCHIVE (`verify_forensic_image`) |
| ECHO logging | ECHO (called by owning modules) |

### storage_query.py

A small shared module of pure functions with no ECHO calls and no decisions.

Used by:

- ARGUS — observation API for SENTINEL
- ARCHIVE — independent pre-ddrescue mount guard
- acquisition state helper — map status and `size_bytes`

This avoids duplicating lsblk logic and avoids making ARCHIVE an observation module.

---

## ARCHIVE Enforcement

Before every ddrescue invocation, ARCHIVE must independently verify:

1. **Acquisition state** permits the requested action (new, resume, or refuse)
2. **No canonical overwrite** — States 3, 4, 5 and unreadable map block ddrescue
3. **No mounted descendants** on source disk
4. **Resume identity** — `acquisition_source.json` validation for State 2
5. **Tool availability** — `ddrescue` installed

ARCHIVE returns a failure result and logs ECHO **ERROR** when any guard fails. ddrescue must not start.

On State 1 new imaging, ARCHIVE writes `acquisition_source.json` before the first ddrescue subprocess.

On resume (State 2), ARCHIVE logs imaging **resumed** distinctly from imaging **started**.

---

## RecoveryStatus Behaviour

No new statuses.

| Event | RecoveryStatus |
|-------|----------------|
| New imaging approved | `IMAGING` |
| Resume approved | `IMAGING` |
| Fingerprint retry approved (State 3) | Remains `READY_FOR_IMAGING` during fingerprint; advances to `READY_FOR_RECOVERY` on success (existing flow) |
| Declined (imaging, resume, unmount, fingerprint) | `READY_FOR_IMAGING` |
| Identity mismatch | `READY_FOR_IMAGING` |
| Inconsistent or unreadable artifacts | `READY_FOR_IMAGING` |
| Mount or unmount failure | `READY_FOR_IMAGING` |
| Canonical refusal (State 4) | `READY_FOR_IMAGING` |
| Imaging or resume completes | Existing transition toward fingerprint and `READY_FOR_RECOVERY` |
| Imaging fails mid-run | `IMAGING` or existing failure behaviour |

Pre-imaging workflow entry status: `READY_FOR_IMAGING`.

---

## ECHO Events

| Event | Module | Level |
|-------|--------|-------|
| Acquisition state classified (1–5) | SENTINEL or ARCHIVE | INFO |
| Map status: incomplete / finished / unreadable | ARCHIVE | INFO / ERROR |
| `acquisition_source.json` written | ARCHIVE | INFO |
| Mounted descendants observed | ARGUS | INFO |
| Unmount approved / declined | SENTINEL | OPERATOR |
| Unmount per target started / ok / failed | ARCHIVE | INFO / ERROR |
| Pre-ddrescue mount verification passed / failed | ARCHIVE | INFO / ERROR |
| Resume refused: identity mismatch | ARCHIVE | ERROR |
| Resume refused: imaging already complete | SENTINEL or ARCHIVE | INFO |
| Resume refused: unreadable map | ARCHIVE | ERROR |
| Resume refused: canonical complete | ARCHIVE | ERROR |
| New forensic imaging started | ARCHIVE | INFO |
| Forensic imaging resumed | ARCHIVE | INFO |
| Forensic imaging completed / failed | ARCHIVE | INFO / ERROR |
| Fingerprint retry offered | SENTINEL | INFO |
| Fingerprint retry approved / declined | SENTINEL | OPERATOR |
| Fingerprint recorded / failed | ARCHIVE | INFO / ERROR |

---

## Operator Flows

### State 1 — New imaging

1. Classify acquisition state.
2. Mounted-descendant gate.
3. Present new imaging (`source`, output paths).
4. `[y/N]` approval.
5. Write `acquisition_source.json`.
6. `IMAGING` → ARCHIVE ddrescue.
7. On success, offer or run fingerprint (existing chain).

### State 2 — Resume

1. Classify acquisition state.
2. Show existing `source.img` and `source.map`.
3. Display identity comparison against `acquisition_source.json`.
4. Mounted-descendant gate.
5. Explain ddrescue will continue using existing map.
6. `[y/N]` resume approval.
7. `IMAGING` → ARCHIVE ddrescue (same paths).

### State 3 — Fingerprint missing

1. Classify acquisition state.
2. Explain imaging is complete; ddrescue will not run again.
3. `[y/N]` fingerprint approval.
4. Run `verify_forensic_image()`.

### State 4 — Canonical complete

Refuse imaging and resume. Explain fingerprint exists and acquisition artifact is preserved.

### State 5 — Inconsistent

Explain which artifact is missing. Require manual investigation. No automated action.

---

## Minimum Implementation Files

| File | Role |
|------|------|
| `Source/modules/storage_query.py` | Mount discovery, `size_bytes`, map status, `ddrescuelog` invocation |
| `Source/modules/argus.py` | Observation wrappers for SENTINEL |
| `Source/modules/archive.py` | Acquisition state, guards, unmount, `acquisition_source.json`, ddrescue new/resume |
| `Source/bin/sentinel` | State-based operator flows |

---

## Relationship to Other Documentation

- **`RecoveryOperationStandard.md`** — general Recovery Operation lifecycle. Imaging-specific safety defer to this document.
- **`RecoveryOperation.md`** — Recovery Operation concept. Imaging behaviour defined here.
- **`ARCHIVE.md`** — ARCHIVE mission and preservation role. Imaging enforcement defined here.
- **`Backlog.md`** — planned TestDisk integration references canonical image rules defined here.
