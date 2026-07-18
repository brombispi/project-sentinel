# TestDisk Integration

Version: 0.2
Status: Design (analysis only — no implementation authorised). Universal
execution model in §7A; **validated on real MiniBerry hardware** as the
reference implementation in §11.
Author role: Cursor (implementation assistant)
Scope: Determine where and how TestDisk fits into the existing Sentinel
recovery architecture as a second image-based Recovery Operation, alongside
PhotoRec.

This is a design document only. It modifies no source, tests, configuration, or
existing documentation, and authorises no implementation. It is subordinate to
and consistent with the Sentinel Constitution (`SentinelLaws.md`,
`EngineeringValues.md`, `ArchitecturePrinciples.md`) and the existing recovery
documents (`RecoveryOperationStandard.md`, `ImagingSafety.md`,
`RecoveryOperationRecord.md`, `RecoveryOperationReporting.md`, `ARCHIVE.md`,
`RecoveryCase.md`, `Backlog.md`).

---

## 0. What was inspected

- `Source/modules/recovery_tools.py` — the tool registry already lists
  `testdisk` (`type: "filesystem"`, `installed` resolved live via
  `shutil.which`).
- `Source/modules/archive.py` — `execute_photorec_recovery(session)` (the
  sibling operation), `summarize_recovered_artifacts(...)`,
  `_count_recovered_artifacts(...)`, `create_recovery_folder(...)`, imaging and
  fingerprint execution, and the ARCHIVE safety guards.
- `Source/bin/sentinel` — `_run_recovery_method_selection(...)` (currently
  offers PhotoRec + cancel, prints a TestDisk "not yet available" note), the
  `RECOVERING` transition, and the interrupted-recovery resume branch.
- `Source/modules/session_manager.py` — `append_running_recovery_operation`,
  `complete_recovery_operation`, `resolve_interrupted_recovery_operation`,
  `update_status`.
- `Source/core/status.py` — `RecoveryStatus`, `RecoveryOperationType`
  (`PHOTOREC` only today), `RecoveryOperationState`.
- `Source/modules/oracle.py` — `recommend_recovery_method()` (returns
  `photorec` / `LOW`).
- `Source/modules/manifest.py`, `Source/modules/case_loader.py`,
  `Source/core/session.py` — persistence/hydration of `recovery_operations`.
- `Source/modules/hermes.py` — read-only reporting of `recovery_operations`
  ("Recovery Attempt Recorded") and recovered-artifact counts.
- `RecoveryOperationStandard.md`, `ImagingSafety.md`,
  `RecoveryOperationRecord.md`, `RecoveryOperationReporting.md`, `Backlog.md`
  (the "Recovery Operations → TestDisk integration" entry).

**Key finding up front:** the architecture was **deliberately shaped to receive
TestDisk**. `RecoveryOperationStandard.md` already names it in the lifecycle,
approval, status, artifact, and comparison tables; `ImagingSafety.md` already
carves out its working-copy rule; `Backlog.md` already fixes its paths and
launch command; `recovery_tools.py` already registers it; and the SENTINEL
recovery menu already prints a "TestDisk planned" note. TestDisk integration is
therefore an **additive extension of an anticipated slot**, not a new
architectural pattern.

---

## 1. Where TestDisk naturally belongs

TestDisk belongs in exactly the place PhotoRec occupies today: a **Recovery
Operation** — one objective, one execution path, one outcome
(`RecoveryOperationStandard.md`) — that:

- is **offered by SENTINEL** only after the workflow reaches
  `READY_FOR_RECOVERY` (imaging complete, fingerprint recorded — acquisition
  State 4 in `ImagingSafety.md`);
- is **executed by ARCHIVE** against an image, never the original device;
- runs in the existing `RECOVERING` status;
- appends one record to the authoritative append-only `recovery_operations`
  history in `case.json`.

It is a **sibling of PhotoRec**, selected in the same
`_run_recovery_method_selection(...)` menu, not a new workflow stage. It fits
`AP-006` (Workflow Before Features): it integrates into the existing recovery
step rather than adding one.

**The one property that makes TestDisk different from PhotoRec — and drives the
entire design:** PhotoRec is a read-only *carver* that writes recovered files to
an output directory and never modifies its input. TestDisk is a
**filesystem-aware tool that writes to the filesystem it operates on**
(rewrite/rebuild partition tables, fix boot sectors, undelete in place). Pointed
at `images/source.img`, TestDisk could **modify the canonical forensic image**,
violating `SL-002` and `ImagingSafety.md` ("The canonical forensic image is
immutable after `evidence/source.sha256` exists. No Recovery Operation may
modify or replace it."). Therefore TestDisk must operate on a **disposable
working copy**, never on the canonical image. `ImagingSafety.md` and `Backlog.md`
already prescribe this: `working/testdisk.img` is "the only image TestDisk may
open."

### Proposed artifact placement (already specified by `Backlog.md` / `RecoveryOperationStandard.md`)

| Artifact | Path | Owner / on-disk permissions | Notes |
|---|---|---|---|
| Canonical image (input to the copy) | `images/source.img` | **`root:root`, mode `0400`** (validated: denies the dropped user read **and** write) | Fingerprinted, immutable; **never** opened by TestDisk |
| Disposable working copy | `working/testdisk.img` | ARCHIVE prep step; **`chown` to the dedicated recovery uid:gid**, mode `0600` | The **only** image TestDisk opens; recreated per approved run |
| Recovered customer data | `recovered/testdisk/` | ARCHIVE creates the directory; **`chown` to the recovery uid:gid**, mode `0700` | Disjoint from PhotoRec's `recovered/recup.*`; TestDisk writes here |
| TestDisk log | `evidence/testdisk.log` (or `evidence/`) | ARCHIVE (via `/log` + cwd `evidence/`); **`chown` the log target to the recovery uid:gid** so the dropped process can append | Tool log, not authoritative structured fact |

**Ownership rule (validated on MiniBerry, §7A):** `chown` is applied **only** to
`working/testdisk.img`, `recovered/testdisk/`, and the `evidence/testdisk.log`
target (or `evidence/`). Everything else — and in particular
`images/source.img` — stays `root:root` and is never handed to the recovery
user. TestDisk runs under a privilege drop (§7A), not as the Sentinel account.

Launch shape (from `Backlog.md`, TestDisk 7.1): working directory =
`evidence/`; command `testdisk /log <case>/working/testdisk.img` (no
`/logname`). The copy destination `recovered/testdisk/` is technician guidance
shown at presentation, not the subprocess cwd.

---

## 2. Which existing modules interact with it

No new module is required. TestDisk reuses the existing subsystem division
(`AP-004`, one responsibility each):

| Module | Interaction | New vs existing |
|---|---|---|
| `recovery_tools.py` | Already registers `testdisk`; availability via `is_tool_installed("testdisk")` | Existing — no change needed |
| **ORACLE** (`oracle.py`) | `recommend_recovery_method()` recommends a method before selection; `Backlog.md` asks that it recommend **TestDisk first (LOW) when installed, PhotoRec fallback** | Small additive change (recommendation logic) |
| **SENTINEL** (`bin/sentinel`) | Presents the operation; adds a TestDisk menu option; obtains the replace-confirmation and the `[y/N]`; transitions `RECOVERING`; delegates to ARCHIVE; persists the record; displays result | Extend `_run_recovery_method_selection(...)`; replace the "planned" note with a real option |
| **ARCHIVE** (`archive.py`) | Prepares `working/testdisk.img`; pre-checks (tool, canonical image present, refuse original device, working dir); launches TestDisk interactively; returns the result dict; ECHO start/end | New `execute_testdisk_recovery(session)` + working-copy prep; `create_recovery_folder` must also create `working/` |
| **ECHO** (`echo.py`) | Append-only audit of start/completion/failure, method selection, replace/decline | Existing logging API |
| **session_manager.py** | `append_running_recovery_operation`, `complete_recovery_operation`, `resolve_interrupted_recovery_operation`, `update_status` | **Reused unchanged** — already tool-agnostic (takes an `operation_type`) |
| **core/status.py** | `RecoveryOperationType` gains a `TESTDISK` member | One enum member (sanctioned by `RecoveryOperationRecord.md` §3.2: add "only when such an operation is actually implemented") |
| **manifest.py / case_loader.py / core/session.py** | Persist/hydrate `recovery_operations` | **No change** — the list is tool-agnostic and already round-trips |
| **SUMMARY** (`summary.py`) | End-of-session per-operation line (performed / declined / cancelled / failed) | Add a TestDisk summary mapping using the existing states |
| **HERMES** (`hermes.py`) | Read-only: "Recovery Attempt Recorded" already counts **any** `recovery_operations` entry regardless of type; recovered-artifact counts | Mostly unchanged (see §3 and the recovered-artifact gap in §8) |
| **JANUS / AEGIS / ARGUS** | Not involved during execution; must already have approved upstream | No change |

---

## 3. ARCHIVE responsibilities vs HERMES responsibilities

This follows `AP-003` (facts immutable, owned by their producer), `AP-004` (one
responsibility per subsystem), and the Result Contract / Artifact Ownership of
`RecoveryOperationStandard.md`.

### ARCHIVE (performs; owns execution facts)

- **Prerequisite checks before starting** (mirroring `execute_photorec_recovery`):
  TestDisk installed; `images/source.img` present; **refuse to run against the
  original device path** (the PhotoRec `PHOTOREC_REFUSED_ORIGINAL` guard has a
  TestDisk equivalent); resolve all paths under `<session.recovery_path>`;
  run the **fail-closed capability checks** (confined recovery identity exists
  and is not in a device-access/privileged group; drop mechanism and execution
  mode usable — §7A); and a **conservative free-space precheck** (point below).
- **Conservative free-space prerequisite.** Before copying, `stat` the canonical
  image and require free space on the `working/` filesystem of **at least
  `source.img` `st_size`** (a full-size copy; sparse/reflink can only use less,
  never more). Compare against `os.statvfs(working_dir)` available bytes with a
  small safety margin. If insufficient, fail **before** `RECOVERING` with a
  clear message and **no** `recovery_operations` record.
- **Prepare the disposable working copy** `working/testdisk.img` from the
  canonical image with **atomic, failure-safe completion**:
  1. **create** `working/testdisk.img.tmp` with **restricted permissions
     (`0600`) before any image bytes are written**, so the working copy is never
     even transiently readable by other users,
  2. copy canonical → `working/testdisk.img.tmp` (mechanism TBD per §8B),
  3. **verify** the copy (size equals `st_size`; mechanism-appropriate integrity
     check),
  4. **`fsync` the temp file before the rename** so its bytes are durable,
  5. **`rename`** `…​.tmp` → `working/testdisk.img` (atomic on the same
     filesystem),
  6. **`fsync` the containing directory after the rename** so the new directory
     entry is durable,
  7. **`chown`** the finished file to the recovery uid:gid (mode `0600`),
  8. on any failure **unlink the `.tmp`** (and, after the rename, the finished
     file) and leave no partial `working/testdisk.img`.
  A pre-existing stray `working/testdisk.img.tmp` from an interrupted prior run
  is removed before step 1. A working copy is recreated only after SENTINEL
  obtained replace confirmation.
- **Copy completes before the workflow advances.** The verified, renamed working
  copy must exist **before** SENTINEL transitions to `RECOVERING` and **before**
  the `RUNNING` `recovery_operations` record is appended (§4/§6). If prep fails,
  status stays `READY_FOR_RECOVERY` and no record is written.
- **Enforce canonical-image immutability**: TestDisk is only ever pointed at
  `working/testdisk.img`; ARCHIVE never passes `images/source.img` to TestDisk
  and never lets TestDisk write into `images/`. The canonical image stays
  `root:root 0400` and is never `chown`ed to the recovery user.
- **Execute TestDisk under the confined execution identity, never under an
  identity that can reach the device or evidence (§7A).** Unless the Sentinel
  runtime account is provably confined, ARCHIVE launches TestDisk through the
  configured privilege-drop mechanism and execution mode (§7A), dropping to the
  configured recovery identity with supplementary groups cleared. The target
  argument is always `working/testdisk.img`, never `images/source.img`. Run
  interactively (like PhotoRec: `subprocess.run(...)` without capturing stdio so
  the operator drives the TUI), cwd `evidence/`. See §11 for the validated
  reference command.
- **Collect artifacts** observable after execution and **return the result
  dict** (`success`, `status`, `message`, `artifacts`, plus any operation
  fields).
- **ECHO** start (INFO, after prerequisites pass), completion (INFO), failure
  (ERROR).
- **Report execution success separately from recovery outcome** — a TestDisk
  session that "ended normally" (exit 0) means the session ended, **not** that
  customer data was recovered (`RecoveryOperationStandard.md`, "Execution
  success versus recovery outcome").

ARCHIVE does **not**: choose the method, assess safety, prompt the technician,
write `case.json`, or generate reports.

### HERMES (reports; read-only; owns no facts)

- Reads the authoritative `recovery_operations` list through the manifest owner
  API (`read_case_manifest`) and renders it. Because "Recovery Attempt
  Recorded" already means *"the list is non-empty, any state counts"*
  (`RecoveryOperationReporting.md` §4), a `TESTDISK` entry is reported correctly
  **with no HERMES change**.
- Renders recovered-artifact counts **observationally** from the ARCHIVE summary
  API — never inferring "a recovery operation happened" from artifacts
  (`RecoveryOperationReporting.md` §3, Occurrences 2 & 5).
- Must **not** decide, execute, write the list, name the tool in the Customer
  Report, or narrate recovery beyond what the record supports. Per-tool /
  per-operation narration (a full Technician "Recovery execution" section) is
  **deferred**, exactly as `RecoveryOperationReporting.md` §6–§7 already defers
  it; TestDisk does not force that decision.

**Boundary summary:** ARCHIVE owns *what happened during execution*; SENTINEL
owns *workflow, approval, and persistence into `case.json`*; HERMES owns
*presentation of already-recorded facts*. TestDisk changes none of these
ownerships.

---

## 4. Which case facts must be persisted

Persistence reuses the M2 record unchanged (`RecoveryOperationRecord.md`). The
**only** durable structured fact is one append-only entry:

```json
{
  "type": "TESTDISK",
  "state": "RUNNING | COMPLETED | FAILED | INTERRUPTED",
  "started_at": "ISO-8601",
  "finished_at": "ISO-8601 | null"
}
```

- Appended at the moment SENTINEL transitions to `RECOVERING` (state `RUNNING`,
  `finished_at: null`), atomically with the status write — and **only after**
  ARCHIVE has produced a verified, renamed `working/testdisk.img` (§3/§6). A
  failed working-copy prep produces **no** record and leaves status at
  `READY_FOR_RECOVERY`.
- Resolved to `COMPLETED`/`FAILED` from ARCHIVE's result after it returns, or to
  `INTERRUPTED` at the resume branch.
- `type` requires the single new enum member `RecoveryOperationType.TESTDISK`.
  `state` reuses the existing four states with **no** semantic change.

**Must NOT be persisted to `case.json`** (per `RecoveryOperationRecord.md` §7 —
each remains owned live by its producer):

- The working-copy path or its existence — `working/testdisk.img` is a
  **disposable derivative**, not a case fact.
- `evidence/testdisk.log`, TestDisk exit code, command line, or TUI output —
  transient; the log lives on disk under `evidence/`, ECHO records the events.
- Recovered file counts / sizes / `recovered/testdisk/` paths — read **live**
  via the ARCHIVE summary API; never copied into the record.
- The replace-confirmation decision — an operator event → ECHO `OPERATOR`, not a
  structured fact.
- `recovery_outcome` — remains the operator's separate case-level judgement,
  independent of the per-operation record (`AP-003`, I9).

`evidence/acquisition_source.json`, `source.sha256`, and the canonical image are
unchanged and untouched by TestDisk.

---

## 5. Which operator decisions must remain manual

Per `SL-006` (The Operator Decides) and the approval tiers in
`RecoveryOperationStandard.md`, TestDisk uses **Tier 2 (selection + confirmation)
plus a replace gate** — the "Method selection + replace confirm + `[y/N]`" model
already recorded in the Standard. All of the following stay manual:

1. **Method selection** — TestDisk vs PhotoRec vs cancel, in the SENTINEL menu.
   Logged via ECHO `OPERATOR`.
2. **Working-copy replace confirmation** — when `working/testdisk.img` already
   exists, SENTINEL must ask `[y/N]` before ARCHIVE recreates it (destroying the
   prior working copy). Default decline. Logged `OPERATOR`.
3. **Final proceed `[y/N]`** — after presentation of objective, source
   (`working/testdisk.img`), and output (`recovered/testdisk/`). Default
   decline.
4. **Everything inside the TestDisk TUI** — partition-table analysis and
   rewrite, boot-sector repair, filesystem/undelete choices, and the copy
   destination selection. TestDisk is interactive; Sentinel must **not** script
   or automate these potentially destructive filesystem writes. The operator
   drives the tool directly, exactly as PhotoRec runs interactively today.

Sentinel presents objective, inputs, output paths, and safety context; it never
decides the recovery action for the operator (`SL-004`, `SL-006`). ARCHIVE never
prompts (`RecoveryOperationStandard.md`).

---

## 6. Failures, interruptions, and resumability

TestDisk reuses the exact lifecycle already implemented for PhotoRec in
`session_manager.py` and the SENTINEL resume branch — no new mechanism.

**Failure classes** (mapping to `RecoveryOperationStandard.md` Failure
Behaviour):

| Failure point | Behaviour | `recovery_operations` | Status |
|---|---|---|---|
| Tool missing / privilege-drop mechanism unavailable or execution mode unusable / recovery identity absent or in a device-access group (§7A/§12) / insufficient free space (§3) / canonical image missing / working-copy prep (copy/verify/`fsync`/`rename`/`chown`) fails / original-device guard trips | ARCHIVE returns failure, ECHO `ERROR`, **before** `RECOVERING` is entered — so **no record is appended** (`RecoveryOperationRecord.md` §2: prerequisite-fail-before-start is not an operation); any `working/testdisk.img.tmp` is unlinked | none appended | unchanged (`READY_FOR_RECOVERY`) |
| Operator declines / cancels selection or replace | ARCHIVE not invoked; ECHO `OPERATOR` | none appended | unchanged |
| TestDisk runs then reports failure | append `RUNNING` → `complete_recovery_operation(success=False)` → `FAILED` | terminal `FAILED` | back to `READY_FOR_RECOVERY` |
| TestDisk ends normally | `RUNNING` → `COMPLETED` (execution completion only) | terminal `COMPLETED` | back to `READY_FOR_RECOVERY` |
| Process/power interruption mid-run | terminal update never runs; case at rest is `status=RECOVERING` with trailing `RUNNING` | resolved to `INTERRUPTED` at resume | resolved before re-offer |

**Interruption resolution** is already handled: the `if status ==
RecoveryStatus.RECOVERING:` resume branch in `bin/sentinel` calls
`resolve_interrupted_recovery_operation(...)`, which finalises the trailing
`RUNNING` entry to `INTERRUPTED` and persists it **before** offering recovery
again (`RecoveryOperationRecord.md` §5.3.1, I6/I7). This is tool-agnostic and
works for `TESTDISK` unchanged.

**Resumability — a deliberate distinction from imaging.** ddrescue imaging is
*resumable* (same `source.img`/`source.map`, `ImagingSafety.md`). TestDisk
recovery is **not resumed**; instead, **each approved run starts from a fresh
working copy**:

- The working copy is recreated (after replace confirmation) for every run, so a
  partial or filesystem-mutated working copy from an interrupted/failed run is
  never reused. This gives the operation-level idempotency the Standard asks for
  ("TestDisk creates a fresh working copy when replacement is approved";
  `RecoveryOperationStandard.md` Idempotency).
- The **canonical image is always intact**, so a fresh working copy is always
  reproducible from it. Interruption never damages evidence; it only discards a
  disposable derivative.
- Repeating the operation appends a new `RUNNING` record (append-only history);
  the prior `INTERRUPTED`/`FAILED`/`COMPLETED` entries are immutable.

---

## 7. Security, forensic, and audit considerations

- **SL-002 / canonical immutability (the central control).** TestDisk can write
  to the filesystem it opens; it must therefore open **only** the disposable
  `working/testdisk.img`. ARCHIVE must never pass `images/source.img` to
  TestDisk and must refuse any resolved target equal to the original device path
  (mirroring `PHOTOREC_REFUSED_ORIGINAL`). This is the single most important
  safety rule for this operation and is enforced at the ARCHIVE boundary
  (`RecoveryOperationStandard.md` Safety Requirements; `ImagingSafety.md`
  Canonical Image Immutability).
- **Case-local paths only.** Working copy, recovered output, and log all resolve
  under `<session.recovery_path>` (`working/`, `recovered/testdisk/`,
  `evidence/testdisk.log`); the technician never types paths
  (`RecoveryOperationStandard.md` Artifact Ownership; ARCHIVE operating
  principles).
- **Privilege separation (the second central control — universal, see §7A).**
  A Sentinel runtime account is commonly in a device-access group (e.g. `disk`)
  and can open raw block devices, so account membership alone does **not**
  confine TestDisk. Unless the runtime identity is provably confined, TestDisk
  must run under a privilege drop to a dedicated identity that cannot reach block
  devices or the canonical image, via the configured drop mechanism and
  execution mode (§7A). Kernel-enforced denial of both the canonical image and
  the raw block device from the confined identity was verified on the reference
  host (§11).
- **Atomic working-copy completion.** A half-written working copy must not be
  usable; prep follows create-`.tmp`-restricted-`0600` → copy → verify →
  `fsync` file (before rename) → `rename` → `fsync` directory (after rename) →
  `chown` → cleanup-on-failure (§3), so it either yields a complete copy or
  fails cleanly
  before `RECOVERING` with no partial `working/testdisk.img`
  (`RecoveryOperationStandard.md` Idempotency / Safety). The copy *mechanism*
  (reflink / sparse / plain copy) is still an implementation detail to be
  **validated on real Sentinel hardware before being fixed as policy**
  (`Backlog.md`; `EV-13`; §8B).
- **Forensic integrity / chain of custody.** The evidentiary anchor remains the
  immutable canonical image plus `source.sha256`. The working copy is an
  explicitly derivative, disposable artifact that TestDisk *may* modify; that is
  acceptable **because** the canonical copy is preserved. TestDisk's own log
  (`evidence/testdisk.log`) plus ECHO's append-only `audit.log` plus the
  `recovery_operations` record together document that a filesystem-recovery
  attempt occurred, when, and its execution state.
- **Audit (ECHO, append-only).** Required events, reusing the Standard's ECHO
  matrix: recovery recommendation (ORACLE, INFO); method selected / declined /
  cancelled and replace approved / declined (SENTINEL, `OPERATOR`); working-copy
  prepared (ARCHIVE, INFO); TestDisk session started (ARCHIVE, INFO); completed
  (INFO) or failed (ERROR); interruption finalised (SENTINEL, INFO). Log lines
  identify the operation and the relevant case-local paths.
- **No overclaiming — exit code 0 means only a normal session end.** A TestDisk
  process exit code of `0` indicates the interactive session **ended normally**
  (the operator quit the TUI), **not** that a partition was rebuilt or that any
  customer data was recovered. ARCHIVE therefore maps a clean exit to the
  execution state `COMPLETED` (session completion), never to a claim of recovery
  success. TestDisk exit codes are otherwise not documented as a reliable
  outcome signal, so ARCHIVE must not branch recovery logic on numeric codes
  beyond "process launched and returned vs. failed to launch / crashed."
  Recovery outcome remains a separate operator judgement and/or an observation of
  `recovered/testdisk/` (`RecoveryOperationStandard.md`;
  `RecoveryOperationReporting.md`).

---

## 7A. Execution model: privilege separation (universal requirements)

This section states the **portable, host-independent** execution requirements for
TestDisk. They are the practical realisation of the two central safety controls:
canonical-image immutability (§7) and non-access to the original block device.
The concrete values from the reference host are recorded separately in §11
(Reference Validation: MiniBerry); nothing in this section is specific to that
host.

### Core requirement — the confined execution identity

TestDisk must **never** run under an identity that can reach the evidence or the
device. It must run under an identity that:

1. **cannot access raw block devices** (`/dev/sd*`, `/dev/nvme*`, `/dev/mmcblk*`,
   etc.);
2. **cannot access the canonical image** (`images/source.img`); and
3. **can access only** the working image (`working/testdisk.img`), the output
   directory (`recovered/testdisk/`), and the log target
   (`evidence/testdisk.log` / `evidence/`).

If the Sentinel runtime account itself already satisfies (1)–(3), it may run
TestDisk directly; **in practice it usually does not** — the account frequently
belongs to a device-access group (e.g. `disk`) and can open block devices, in
which case running TestDisk as that account would expose the original device.
Therefore, unless the runtime identity is provably confined, TestDisk runs under
a **privilege drop to a dedicated confined identity**.

### Configurable execution — no hard-coded account, command, or mode

The following are **configuration**, not fixed application constants:

- **Recovery account** — the confined identity's name (or uid/gid). Default /
  reference value: **`sentinel-recovery`** (validated in §11). Resolved by name
  to uid/gid at runtime; never assume a specific numeric uid/gid.
- **Privilege-drop command** — a configurable command template that drops to the
  recovery identity, clears supplementary group membership, and execs TestDisk.
  The reference implementation uses `setpriv --reuid=<uid> --regid=<gid>
  --clear-groups -- …` (§11); any mechanism giving the same guarantees is
  acceptable.
- **Execution mode** — how the drop is invoked. Sentinel must support:
  - **root** — Sentinel already runs as root; it performs the drop directly (no
    `sudo` needed).
  - **passwordless sudo** — Sentinel runs as an unprivileged account with
    passwordless `sudo`; the drop is wrapped in `sudo` (the reference mode, §11).
  - **external** — another compatible privilege-separation mechanism: any
    host-provided means (e.g. an alternate drop tool, a pre-existing
    already-confined runtime identity, or a container/namespace boundary) that
    satisfies the core requirement above. This is a first-class supported mode;
    it requires a configured privilege-drop mechanism, is only validated
    structurally before execution, and is never invoked during preparation.

The command template and mode are read from configuration/deployment, so the
same code runs unchanged across these environments.

### Fail-closed capability checks (before execution)

Before starting — prerequisite-class, so failure happens **before** `RECOVERING`
and appends **no** `recovery_operations` record (§6) — Sentinel must verify, and
**refuse to run** if any check fails:

1. The configured recovery identity **exists** and resolves to a stable uid/gid.
2. That identity is **non-root** (uid ≠ 0, primary gid ≠ 0, and not a member of
   the root group) **and** is **not** a member of any device-access or privileged
   group (e.g. `disk`, `sudo`) — i.e. it satisfies the "cannot access block
   devices" requirement. Supplementary group membership is enumerated through the
   **host identity service** (e.g. `os.getgrouplist`, which consults nsswitch:
   files, LDAP/SSSD, …), not a local-only scan, so network-sourced membership is
   not missed; if enumeration fails, the check fails closed.
3. The configured **privilege-drop mechanism is available** (e.g. the drop tool
   is on `PATH`) and the configured **execution mode is usable** (running as
   root, or `sudo` is invocable non-interactively, as configured).
4. The canonical image is present and is **not** owned/accessible by the recovery
   identity (§7); the working image / output / log targets are prepared and owned
   by the recovery identity (below).

Sentinel does **not** create or modify host accounts to satisfy these checks
(see §12); it only verifies them and fails closed.

### Required ownership / permissions

`chown` is applied to **exactly these three targets** and nothing else;
`images/source.img` is never handed to the recovery identity.

| Path | Required state | Purpose |
|---|---|---|
| `images/source.img` | `root:root` (or a non-recovery owner), `0400`, **never `chown`ed to the recovery identity** | Recovery identity denied read **and** write |
| `working/testdisk.img` | `chown` recovery identity, `0600` | The only image the dropped process can open |
| `recovered/testdisk/` | `chown` recovery identity, `0700` | Dropped process writes recovered data here |
| `evidence/testdisk.log` (or `evidence/`) | `chown` recovery identity on the log target, `0640` | Dropped process appends its `/log` output |

### Path-traversal requirement

The recovery identity must be able to **traverse** every ancestor directory down
to the chowned targets. Ownership of the leaf alone is insufficient: if any
parent lacks the "others execute" bit for the recovery identity, writes fail with
`Permission denied` even on a leaf the identity owns.

- **Requirement:** the case-tree structural directories (`<recovery_path>`,
  `recovered/`, `evidence/`, and any intervening path down to the mount) must be
  **traversable** by others — mode `o+x` (e.g. `0755`, or `0711` to also deny
  listing). ARCHIVE must not tighten the structural parents to `0700`.
- The **leaf** targets stay owned by the recovery identity (`recovered/testdisk/`
  `0700`, `working/testdisk.img` `0600`), so listing/content is not exposed to
  other unprivileged users; only traversal is granted on the structural path.

(This requirement was discovered and confirmed on the reference host — see §11.)

---

## 8. Does the current architecture support this cleanly, or need extension?

**It supports TestDisk cleanly at the architectural level.** The Recovery
Operation lifecycle, the append-only `recovery_operations` record, the
`session_manager` append/complete/resolve helpers, the `RECOVERING` status, the
ECHO matrix, the SUMMARY per-operation states, and the artifact-ownership layout
were all written to accommodate a second recovery tool, and several documents
name TestDisk explicitly. No architectural pattern needs to be redesigned, and
no new module, dependency, or workflow stage is required (`EV-7`, `AP-006`,
Implementation-Standards).

**Small, additive extensions are required** (each is a normal, sanctioned
addition, not a redesign):

1. **`RecoveryOperationType.TESTDISK` enum member** (`core/status.py`) — the one
   reserved-until-implemented extension point (`RecoveryOperationRecord.md`
   §3.2). Persistence/hydration then work unchanged.
2. **ARCHIVE `execute_testdisk_recovery(session)` + working-copy preparation**,
   plus `create_recovery_folder(...)` creating the `working/` directory (today
   it creates `images/recovered/exports/notes/reports/evidence` but **not**
   `working/`). Prep includes the free-space precheck, the atomic
   `.tmp`→verify→`fsync`→`rename`→`chown`→cleanup sequence (§3/§6), and launching
   TestDisk via the configured privilege-drop mechanism and execution mode (§7A).
3. **SENTINEL menu extension** in `_run_recovery_method_selection(...)`: replace
   the current "TestDisk planned" note with a real option, add the replace
   confirmation gate, and present objective/inputs/outputs. Reuse
   `append_running_recovery_operation(session, RecoveryOperationType.TESTDISK
   .value)` / `complete_recovery_operation(...)`.
4. **ORACLE recommendation** update so TestDisk is recommended first (LOW) when
   installed, PhotoRec as fallback (`Backlog.md`), keeping recommendation
   explainable (`SL-004`/`SL-005`).
5. **Localization keys** (`i18n/en.json`, `de.json`) for the new menu option,
   replace prompt, presentation, and result lines, preserving EN/DE parity.
6. **SUMMARY line** for TestDisk mapped to the existing performed / declined /
   cancelled / failed states.
7. **Deployment/provisioning of the confined recovery identity** (§12) — a
   host-provisioning step, **not** application code: provision the unprivileged
   account (reference: `sentinel-recovery`), confirm it is **not** in a
   device-access/privileged group, and confirm the configured privilege-drop
   mechanism is present. Sentinel never creates the account.

**Decisions before implementation** (`Verify-Understanding`,
`Role-and-Decision-Boundaries`):

- **A. Recovered-artifact accounting for `recovered/testdisk/` — DECIDED
  (option a).** `summarize_recovered_artifacts(...)` currently scans **only**
  `recovered/recup.*` (PhotoRec's layout). Product decision (Raz): **extend the
  ARCHIVE summary to also count `recovered/testdisk/`**; HERMES then reflects it
  with no reporting-architecture change.

  **Disjoint recovered-artifact roots — mandatory to avoid double-counting.**
  Each tool owns a **non-overlapping** subtree of `recovered/`:
  `recovered/recup.*` (PhotoRec) and `recovered/testdisk/` (TestDisk). The
  extended summary must count each root **independently** and must **not** scan a
  parent that contains both (i.e. do **not** generalise to "all of
  `recovered/`", which would double-count if roots ever nest or overlap). Because
  the roots are disjoint, the per-tool counts sum without overlap, and a run of
  one tool never inflates the other's count. ARCHIVE is the single owner of this
  counting logic; HERMES stays observational.
- **B. Working-copy mechanism.** reflink vs sparse vs plain copy affects time,
  space, and correctness on the target hardware; `Backlog.md` and `EV-13`
  require validation on real Sentinel hardware before any mechanism is fixed as
  policy. Not an architectural blocker; a hardware-validation task.

Neither decision blocks the architecture; both are contained to ARCHIVE and to a
product choice.

---

## 9. Constitutional alignment

- **SL-002 / Canonical Immutability:** TestDisk operates only on a disposable
  working copy; the fingerprinted canonical image is never opened or modified.
- **SL-004 / SL-005 / SL-006:** the operation is presented and explained; risk
  and recommendation come from ORACLE; every destructive filesystem action is an
  explicit, manual operator decision inside the tool.
- **SL-007 (Preserve Workflow):** TestDisk slots into the existing
  `READY_FOR_RECOVERY → RECOVERING → READY_FOR_RECOVERY` flow; no prior safety
  decision is contradicted.
- **AP-002 (No circular deps):** ARCHIVE executes, SENTINEL persists, HERMES
  reads; no module depends on HERMES.
- **AP-003 (Facts vs decisions):** one immutable per-attempt record; recovered
  counts stay observational; `recovery_outcome` stays a separate decision.
- **AP-004 (One responsibility):** ARCHIVE performs, SENTINEL orchestrates and
  persists, ORACLE recommends, ECHO records, SUMMARY aggregates, HERMES reports.
- **AP-006 (Workflow before features):** integrates into the existing recovery
  step; adds no new stage.
- **EV-7 / EV-11 / EV-12 / Implementation-Standards:** smallest change consistent
  with the requirement — one enum member, one ARCHIVE function plus working-copy
  prep, one SENTINEL menu extension, one ORACLE tweak, localization, and a
  SUMMARY line; existing modules and persistence reused; no new dependency.

---

## 10. Out of scope / not authorised here

- No source, test, configuration, or existing-document changes are made by this
  document.
- Per-operation Technician "Recovery execution" narration and any customer-facing
  recovery wording remain **deferred** exactly as in
  `RecoveryOperationReporting.md` §6–§7; TestDisk does not force them.
- Differentiated ORACLE "Recovery Problem" classification remains deferred
  (`Backlog.md`); TestDisk needs only the method-recommendation tweak in §8.4.

---

## 11. Reference Validation: MiniBerry (2026-07-18)

This section is the **reference implementation** of the universal requirements in
§7A, with the concrete, host-specific values as measured. It is illustrative,
not normative — other hosts may use different account names, uids/gids, drop
tools, or execution modes provided they satisfy §7A. Recorded from a read-only,
disposable validation on the real MiniBerry (Raspberry Pi); all test artifacts
were created under `mktemp` and removed — no Sentinel source, tests, docs, or
case data were touched.

### Host-specific environment (MiniBerry)

- **Runtime account:** `MiniBerry` (uid 1000); groups include `sudo` and
  **`disk`** (plus `dialout`, `video`, `gpio`, `i2c`, `spi`, …). Passwordless
  `sudo` available. **Execution mode = passwordless sudo** (§7A): Sentinel runs
  interactively as `MiniBerry`, not root, not a systemd service (the only
  "sentinel" systemd objects are the LUKS `sentinel_storage` device/mount).
- **TestDisk version:** **7.1** (`/usr/bin/testdisk`, 2019 build) — older than
  the dev-Mac 7.2; the `Backlog.md` launch shape (`/log`, no `/logname`) matches
  7.1.
- **Drop tool:** `setpriv` **available** (`/usr/bin/setpriv`); `runuser` **not
  installed** — so `runuser` is not a usable fallback *on this host*.
- **Block devices:** `/dev/sda`–`/dev/sdd` present as `root:disk 0660`; no
  `/dev/nvme*`.
- **Runtime account is NOT confined:** `dd if=/dev/sda` **succeeded** as
  `MiniBerry` (disk-group membership) → confirms the drop is required here.

### Reference confined identity — `sentinel-recovery`

- Provisioned and retained: system account, **`uid=999`, `gid=991`**, primary
  group `sentinel-recovery`, **no supplementary groups**, shell
  `/usr/sbin/nologin`, password locked. Not in `disk`, `sudo`, `dialout`, or any
  hardware-access group.

### Reference production command (validated)

Resolve uid/gid by name at runtime; the reference mode wraps the drop in `sudo`:

```
sudo setpriv --reuid="$(id -u sentinel-recovery)" \
             --regid="$(id -g sentinel-recovery)" \
             --clear-groups -- \
             testdisk /log working/testdisk.img
```

(`--clear-groups` is what sheds the `disk`-group access; cwd `evidence/`; target
always `working/testdisk.img`.)

### Validated results

- **Confinement (dropped identity):** read `images/source.img` (`root:root
  0400`) → **Permission denied**; read `/dev/sda` → **Permission denied**.
- **Write-path (dropped identity, traversable `0755` case tree):** `testdisk
  /list working/testdisk.img` → **opened** (real geometry line); write
  `recovered/testdisk/fileA.txt` → **OK**; append `evidence/testdisk.log` →
  **OK**.
- **Path traversal:** writes on owned leaves failed until structural parents were
  `o+x`; a `0700` case root blocked writes. Real case dirs are `0755` → holds
  (§7A traversal requirement).
- **Canonical protection:** `root:root 0400` denies even the owning runtime
  account both read and write.
- **Kernel-enforced confinement verdict:** **PASS** under the `sudo setpriv
  --clear-groups` drop; **FAIL** if TestDisk were run as the runtime account.

---

## 12. Provisioning the confined recovery identity (deployment responsibility)

Provisioning the confined identity is a **host / deployment concern, not core
application behaviour**. Sentinel **never creates, modifies, or deletes host
accounts**; it only *verifies* (fail-closed, §7A) that a suitable identity and
drop mechanism exist, and refuses to run otherwise. Account creation belongs in
the deployment/provisioning documentation and runbooks.

**What deployment must provide (host-independent):**

- A dedicated, unprivileged identity for running TestDisk (default / reference
  name `sentinel-recovery`, configurable) that satisfies the §7A core
  requirement: **not** a member of `disk`, `sudo`, or any group granting
  block-device or privileged access; no interactive login; no password.
- The configured **privilege-drop mechanism** available on the host, and an
  **execution mode** (root, passwordless sudo, or another compatible mechanism)
  by which Sentinel can perform the drop and `chown` the three recovery targets
  (§7A) to that identity.
- Case-tree structural directories that remain `o+x`-traversable (§7A traversal
  requirement).

**Reference provisioning command** — the concrete, validated command for the
MiniBerry reference host lives in §11 (a Debian `useradd --system … nologin` +
`passwd -l`). Other OSes/hosts use their own equivalent; the only invariant is
the resulting identity satisfying §7A.

**Sentinel's fail-closed pre-run checks** are defined in §7A ("Fail-closed
capability checks"): identity exists and resolves; identity not in a
device-access/privileged group; drop mechanism available and execution mode
usable; canonical image protected and working/output/log targets owned by the
identity. Any failure fails **before** `RECOVERING` and appends **no**
`recovery_operations` record (§6).

---

## Related Documents

- `RecoveryOperationStandard.md`
- `ImagingSafety.md`
- `RecoveryOperationRecord.md`
- `RecoveryOperationReporting.md`
- `ARCHIVE.md`
- `RecoveryCase.md`
- `Backlog.md`
- `SentinelLaws.md`
- `EngineeringValues.md`
- `ArchitecturePrinciples.md`
