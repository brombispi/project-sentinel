# Recovery Operation Record (Milestone M2)

Version: 0.2
Status: Design (analysis only)
Author role: Cursor (implementation assistant)
Scope: Persist an authoritative, append-only recovery-operation history in `case.json`

---

## Revision note

Version 0.1 recommended a single current/final record (Model A). That model is
**not approved**: a later attempt would overwrite earlier authoritative structured
facts and force consumers to fall back to the non-authoritative `audit.log`.

Version 0.2 adopts, by product decision, an **append-only list of recovery-operation
attempts (Model B)** with explicit interruption handling and retry rules. All
sections below reflect Model B. Model A is retained only as a rejected alternative
in §4.

---

## Purpose

This document designs the authoritative case record that proves image-based
**recovery operations** were attempted, completed, failed, or interrupted on a
Recovery Case, closing the documented blocker in `CustomerReport.md` §10 (and §6).

It is a design document only. It authorises no implementation and modifies no
source, tests, or existing documentation.

It is subordinate to and consistent with the Sentinel Constitution
(`SentinelLaws.md`, `EngineeringValues.md`, `ArchitecturePrinciples.md`) and the
existing recovery documents (`RecoveryCase.md`, `RecoveryOperation.md`,
`RecoveryOperationStandard.md`, `REPORT_SCHEMA.md`, `REPORTING.md`,
`CustomerReport.md`).

The design deliberately does **not** redesign the recovery workflow. It adds one
optional, append-only list to the existing `case.json` and reads/writes it at
workflow points that already exist.

---

## 1. Problem statement

Today the recovery execution leaves **no durable, structured proof** that a
recovery operation ran:

- `execute_photorec_recovery(session)` (`Source/modules/archive.py`) returns an
  **ephemeral** result dict. It is never written to `case.json`.
- The only durable trace is `audit.log` (ECHO), which is **Internal Only** and,
  by contract, "does not parse or interpret log entries" (`echo.py`). It is not
  an authoritative structured fact source and must not be one.
- `recovery_outcome` (`core/status.py`, persisted by `manifest.py`) is an
  **operator decision** recorded at finalization. By its own definition it is
  "never derived from recovered file counts or statistics." It is an
  interpretation, **not** proof that an operation executed (AP-003: facts and
  decisions are separate).
- `summarize_recovered_artifacts(...)` counts files on disk. It proves artifacts
  *exist*; it does **not** prove an operation ran, and must not be used to infer
  one (`CustomerReport.md` correction 2).
- `classify_acquisition_state(...)` authoritatively proves **imaging**, not
  file recovery.

Consequence: the Customer Report "Work Performed" is limited to the imaging fact,
and the Technician Report "Recovery execution" section cannot authoritatively
narrate recovery operations. M2 supplies the missing structured fact, preserved
per attempt.

---

## 2. What counts as a recovery operation (Question 1)

A **recovery operation** for this record is a single image-based data-recovery
execution that SENTINEL delegates to ARCHIVE against the forensic image. For M2 the
only supported operation is **PhotoRec** (`execute_photorec_recovery`), which runs
in the `RECOVERING` status.

An operation is defined by **entry into execution**: the point at which SENTINEL
sets `RECOVERING` and delegates to ARCHIVE (`Source/bin/sentinel`, lines ~451–459).

Explicitly **excluded** (each already has its own authoritative record or is not
an operation):

| Not a recovery operation for this record | Why | Existing authority |
|---|---|---|
| Forensic imaging | Preservation, not recovery; already provable | acquisition state + `source.map` |
| Fingerprinting | Integrity evidence, not recovery | `source.sha256` |
| Recovery **method selection cancelled** | ARCHIVE never started | ECHO `OPERATOR` |
| Recovery **declined** by operator | ARCHIVE never started | ECHO `OPERATOR` |
| Prerequisite failure **before** execution (tool missing, image missing) | Operation "does not start" per the Standard | ECHO `ERROR` + result dict |

**Rule:** the record represents an ARCHIVE recovery execution that *began* (reached
the in-progress `RECOVERING` phase). Decline, cancel, and
prerequisite-fail-before-start are workflow/operator events, already owned by ECHO
and workflow state; they are **not** appended.

Future recovery-class operations (e.g. TestDisk) would follow the same lifecycle,
but M2 supports **PHOTOREC only** and reserves no other tool identifiers (§3).

---

## 3. Recommended schema (Questions 6, 7)

### 3.1 Shape

`recovery_operations` is an **ordered, append-only JSON array**. Each actual
execution attempt appends exactly one record. Existing records are never
overwritten or removed.

```json
"recovery_operations": [
    {
        "type": "PHOTOREC",
        "state": "COMPLETED",
        "started_at": "2026-07-16T11:00:05",
        "finished_at": "2026-07-16T11:42:31"
    }
]
```

### 3.2 Per-record fields (minimum)

| Field | Type | Meaning |
|---|---|---|
| `type` | enum string — `PHOTOREC` only for M2 | Which recovery operation ran |
| `state` | enum string — `RUNNING`, `COMPLETED`, `FAILED`, `INTERRUPTED` | Execution completion state (§5.3), never recovery effectiveness (§6) |
| `started_at` | ISO-8601 string | When execution began |
| `finished_at` | ISO-8601 string or `null` | When execution reached a terminal state; `null` only while `RUNNING` |

- `type` and `state` are recommended as `str`-valued `Enum`s in `core/status.py`
  alongside `RecoveryStatus` and `RecoveryOutcome` (e.g. `RecoveryOperationType`
  with a single `PHOTOREC` member; `RecoveryOperationState` with the four members),
  keeping the workflow vocabulary in one authoritative place and preventing
  free-text drift.
- `type` is **PHOTOREC only**. TESTDISK and other tools are **not** reserved; the
  enum gains a member only when such an operation is actually implemented.
- `started_at` / `finished_at` reuse the ISO-8601 convention already used by
  `created_at` and `completed_at`.

### 3.3 Operation identity for update — no ID required (Question 7)

An operation is identified for update by **its position and state**, not by an ID:

> The only mutable record is the **final list entry when its `state` is `RUNNING`**.
> Every other entry is terminal (`COMPLETED`, `FAILED`, `INTERRUPTED`) and
> immutable.

This is unambiguous because of the retry invariants (§5.4): at most one `RUNNING`
record may exist, and because terminal entries are immutable and a new attempt only
appends after the previous `RUNNING` record is resolved, any `RUNNING` record is
always the last element. Therefore both the terminal update (§5.2) and the
interruption resolution (§5.3) act on "the last entry, and only if it is
`RUNNING`." No two records ever compete for the same update.

**Inspection conclusion: an explicit operation ID is not necessary** and is not
added. Adding one would introduce unnecessary complexity (contrary to EV-7 and
Implementation-Standards) without resolving any real ambiguity. If a future
requirement introduces concurrent operations or cross-references between operations
and other structured records, an `id` (or sequence integer) can be added additively
at that time; M2 does not need it and does not foreclose it.

### 3.4 Rejected alternative — Model A (single record)

A single `recovery_operation` object was rejected: a re-run would overwrite the
prior attempt's authoritative structured facts, leaving only the non-authoritative
`audit.log` as evidence of earlier attempts. That violates the intent of
`RecoveryCase.md` Rule 6 (append-only; corrections create new records rather than
replacing history) and forces consumers onto an Internal-Only source. Model B keeps
the full attempt history authoritative and structured in `case.json`.

---

## 4. Ownership (Question 2)

Ownership follows AP-003 (facts immutable, owned by their producer), AP-004 (one
responsibility per subsystem), and the **existing** `case.json` writer boundary.

| Concern | Owner | Basis |
|---|---|---|
| **Execution outcome fact** (did the operation succeed/fail) | **ARCHIVE** | ARCHIVE executes and returns the authoritative result dict (`RecoveryOperationStandard.md`, Result Contract) |
| **Operation type** being delegated | **SENTINEL** | SENTINEL selects and delegates the operation |
| **Interruption finalization** (`INTERRUPTED`) | **SENTINEL** | An execution that never returned; SENTINEL owns workflow state and finalizes its own orphaned `RUNNING` record |
| **Persistence into `case.json`** (append/update the list) | **SENTINEL** | `case.json` is written only by `manifest.py` / `session_manager.py` today; ARCHIVE never writes `case.json` |
| **Reading the list for reports** | **HERMES** (read-only) | HERMES presents; never authors |

**Decision:** ARCHIVE remains the sole authority for *whether an execution
succeeded* (`COMPLETED`/`FAILED`); SENTINEL is the sole *persistence* authority for
`case.json` and appends/updates the list from (a) the operation type it is about to
run and (b) ARCHIVE's returned result, and it alone finalizes an orphaned `RUNNING`
record to `INTERRUPTED`. This preserves the single-writer invariant on `case.json`,
avoids a new circular dependency (AP-002), and mirrors how `completed_at` and
`recovery_outcome` are already persisted by SENTINEL while their *meaning* is owned
elsewhere. HERMES **never** writes the list (AP-004; `CustomerReport.md` §3).

---

## 5. Lifecycle and interruption-resolution flow (Questions 3, 4, 5)

Three persistence actions, all bound to **already existing** workflow points. No
new workflow steps are introduced.

```
READY_FOR_RECOVERY
      │  operator approves recovery (existing [y/N])
      ▼
(precondition: no trailing RUNNING record — see retry rules §5.4)
SENTINEL sets RECOVERING  ──►  APPEND {type: PHOTOREC, state: RUNNING,
      │                                started_at: now, finished_at: null}
      ▼
ARCHIVE executes the recovery operation, returns result
      │
      ▼
SENTINEL sets READY_FOR_RECOVERY ──► UPDATE last entry (must be RUNNING):
                                     state = COMPLETED | FAILED, finished_at = now
```

Interruption path (execution never returns — power loss / process kill):

```
… APPEND RUNNING persisted, tool running …
      ✗ process dies; the terminal UPDATE never runs
      ▼
case.json at rest: status = RECOVERING, last recovery_operations entry = RUNNING
      ▼
later: operator reopens/continues the case → SENTINEL loads it (read-only)
      ▼
existing RECOVERING-resume branch informs the operator          (Source/bin/sentinel ~1661)
      ▼
SENTINEL UPDATE last entry (RUNNING → INTERRUPTED, finished_at = resolution time),
persisted, BEFORE offering a new attempt                        (before ~1673)
```

### 5.1 Creation — append (Question 3)

The record is appended at the **exact** moment SENTINEL transitions the case into
`RECOVERING`, before ARCHIVE runs the (potentially long) tool — the existing
`update_status(session, RECOVERING, …)` call site (`Source/bin/sentinel` ~451). The
appended record has `state = RUNNING`, `started_at = now`, `finished_at = null`.

Because `update_status` re-serializes the whole manifest atomically, the
`RECOVERING` status and the new `RUNNING` list entry land in a **single atomic
`case.json` write**. This is the crash-safety anchor: an interruption leaves both
signals consistent on disk.

### 5.2 Terminal update — COMPLETED / FAILED (Question 4)

After ARCHIVE returns, at the existing post-operation
`update_status(session, READY_FOR_RECOVERY, …)` call site (`Source/bin/sentinel`
~461), SENTINEL updates **the last list entry, which must be `RUNNING`**:

- `state = COMPLETED` if ARCHIVE's result reports success.
- `state = FAILED` if ARCHIVE's result reports failure.
- `finished_at = now` in both cases.

This is the only place `RUNNING → COMPLETED|FAILED` occurs during a live run.

### 5.3 State semantics (Question 5)

| `state` | Exact meaning | Set by | `finished_at` |
|---|---|---|---|
| `RUNNING` | Persisted **immediately before** the delegated recovery execution begins | SENTINEL (append, §5.1) | `null` |
| `COMPLETED` | The delegated operation **returned its authoritative success result** | SENTINEL from ARCHIVE result (§5.2) | set |
| `FAILED` | The delegated operation **returned failure, or raised a handled execution failure** that ARCHIVE converts into a failure result | SENTINEL from ARCHIVE result (§5.2) | set |
| `INTERRUPTED` | An earlier `RUNNING` operation is **detected during case loading/workflow resumption and explicitly finalized as interrupted before another attempt may start** | SENTINEL (interruption resolution, §5.3.1) | set (resolution time) |

`RUNNING` is never a durable *final* state: at rest it always denotes an operation
that has not yet been resolved and must be finalized to `INTERRUPTED` before a new
attempt (§5.4).

#### 5.3.1 Interruption resolution — the exact existing workflow point (Question 9)

- **Loading must not mutate.** `load_case` (`Source/modules/case_loader.py`) only
  **hydrates** `recovery_operations` (§7). It performs **no** write. There is no
  automatic write during read-only hydration.
- **Detection signal.** A case found at rest with `status == RECOVERING` denotes an
  interrupted recovery. (Normal completion always transitions out of `RECOVERING`;
  reopen of a completed case resolves to `READY_FOR_RECOVERY`, never `RECOVERING`.
  The existing code already labels this branch an "Interrupted recovery session".)
  The trailing `recovery_operations` entry will be `RUNNING`.
- **Resolution point.** The existing recovery-resume branch in `Source/bin/sentinel`
  (the `if status == RecoveryStatus.RECOVERING:` block, ~lines 1661–1666, which
  already logs `"Interrupted recovery session; offering recovery operations."`) is
  where the operator is informed. At this point, **before**
  `_run_recovery_method_selection` (~line 1673) can start a new attempt, SENTINEL
  finalizes the trailing `RUNNING` entry to `INTERRUPTED` (`finished_at =`
  resolution time), persists it, and records the resolution via ECHO.
- This is an **explicit, operator-informed workflow write**, not a hidden side
  effect of hydration.

### 5.4 Retry rules (Question 8)

- **At most one `RUNNING`.** A case may contain at most one `RUNNING` entry at any
  time, and it is always the last entry.
- **Resolve before retry.** A new attempt (append of a fresh `RUNNING`) **must not
  start** until any prior `RUNNING` record has been resolved to `INTERRUPTED`
  (§5.3.1). In the workflow this is guaranteed because the interruption resolution
  runs at the `RECOVERING`-resume branch before recovery is offered again.
- **Terminal immutability.** `COMPLETED`, `FAILED`, and `INTERRUPTED` entries are
  immutable: they are never overwritten, reordered, or removed. Only the trailing
  `RUNNING` entry is ever updated (once), and only to a terminal state.
- **Append for each new attempt.** Each subsequent approved execution appends a new
  `RUNNING` record; the history grows monotonically.

---

## 6. Execution completion vs recovery effectiveness (Question 6)

`state` describes **execution completion**, never recovery effectiveness:

- **Zero recovered items does not determine `FAILED` or `COMPLETED`.** A PhotoRec
  session that ends normally with zero recovered files is `COMPLETED` (execution
  succeeded); a session that fails to run is `FAILED`. This matches
  `RecoveryOperationStandard.md`, which separates *execution success* from
  *recovery outcome* and states "zero recovered files is not automatically an
  execution failure."
- `state` is derived solely from ARCHIVE's authoritative execution result
  (success/failure), or from SENTINEL's interruption finalization — never from file
  counts, byte sizes, or artifact presence.
- **`recovery_outcome` remains the operator's separate, case-level judgement**
  (`SUCCESSFUL` / `PARTIAL` / `UNSUCCESSFUL`), independent of these per-operation
  states. Neither is derived from the other (AP-003).

---

## 7. Facts that must NOT be duplicated

Per single-source-of-truth (`RecoveryCase.md`), AP-003, and the task constraints,
each record stores **only** `type`, `state`, `started_at`, `finished_at`. It must
**not** store:

| Excluded | Authoritative owner (unchanged) |
|---|---|
| Recovered file counts / directory counts / byte sizes | `summarize_recovered_artifacts(...)` (ARCHIVE), read live |
| Recovered output paths (`recovered/recup.*`) | ARCHIVE owner API / filesystem |
| Tool console output, command line, exit codes | Not persisted anywhere; transient (correct) |
| "Session started/ended" log lines, event timestamps | `audit.log` (ECHO), append-only |
| Case workflow status (`RECOVERING`, `READY_FOR_RECOVERY`, `COMPLETED`) | `session.status` / `case.json.status` |
| Operator's recovery **outcome** (`SUCCESSFUL`/`PARTIAL`/`UNSUCCESSFUL`) | `recovery_outcome` (separate decision; AP-003) |
| Imaging / integrity proof | acquisition state + `source.sha256` |
| Report prose | HERMES (derived, non-authoritative) |

Each record is a **fact about one execution** (type/state/timestamps), kept
strictly independent from `recovery_outcome` (an interpretation). This directly
satisfies "do not treat `recovery_outcome` as proof that a recovery operation ran"
and "do not use `audit.log` as the authoritative source."

---

## 8. Serialization and rehydration (Question 9, part)

Mirror the mechanism already proven for `recovery_outcome` and `completed_at`,
adapted for a list.

**Runtime carrier.** Add one attribute to `RecoverySession` (`core/session.py`),
e.g. `recovery_operations: list = field(default_factory=list)`, defaulting to an
empty list.

**Write** (`Source/modules/manifest.py`, `write_case_manifest`). Following the
existing conditional pattern used for `completed_at`/`recovery_outcome`, write the
key only when the list is non-empty:

```python
if getattr(session, "recovery_operations", None):
    manifest["recovery_operations"] = session.recovery_operations
```

Written atomically by the existing `_atomic_write_json`. Serialized as a JSON array
under the key `recovery_operations`.

**Append / update semantics.** SENTINEL mutates `session.recovery_operations`
in memory (append a new `RUNNING` record; or update the trailing `RUNNING` record's
`state`/`finished_at`) and then persists via the existing `save_case` /
`update_status`, which re-serialize the whole manifest. No new persistence
machinery is introduced.

**Rehydrate** (`Source/modules/case_loader.py`, `load_case`). Following the existing
lines that hydrate `completed_at` / `recovery_outcome`:

```python
session.recovery_operations = manifest.get("recovery_operations", [])
```

Hydration is **read-only** and never mutates the case (§5.3.1).
`read_case_manifest` needs no change: `recovery_operations` is **not** added to
`REQUIRED_MANIFEST_FIELDS`.

---

## 9. Backward compatibility (Question 10)

The list is **optional and additive**; no migration machinery is required, and none
is proposed.

- **Absent `recovery_operations` hydrates as an empty list** (`[]`), treated as "no
  recovery operations recorded." This mirrors how legacy cases without
  `recovery_outcome` / `completed_at` already load (see
  `test_recovery_outcome.py::RecoveryOutcomeLoadTests`,
  `test_completed_at.py::CompletedAtLoadTests`).
- **No inferred historical attempts.** Existing cases are never back-filled from
  `audit.log`, recovered artifacts, or `recovery_outcome`; doing so would invent
  facts (AP-003, no inference). Observed reality confirms this is safe: the only
  on-disk cases are fixtures — one `case.json` containing `{}` and one case with
  `audit.log` only — none carrying a recovery operation to back-fill.
- **Write path** writes the key only when the list is non-empty, so re-saving a
  legacy case does not fabricate one.
- **Required fields unchanged**, so old manifests remain valid.

---

## 10. HERMES consumption without inferring facts (Question 11)

HERMES remains **read-only**. It reads `recovery_operations` through the case
manifest owner API (`read_case_manifest`), consistent with its contract
(`test_recovery_statistics_uses_owner_api_not_filesystem`). It renders recorded
values and adds nothing. **This milestone does not implement or finalize report
wording**; the following states only what HERMES *may safely say* from the list.

HERMES may safely state, strictly from the recorded list:

- **That recovery operations were recorded, and how many** (the number of entries).
- **Per entry: `type`, `state`, `started_at`, `finished_at`** as recorded — suitable
  for the Technician Report "Recovery execution" section (`REPORT_SCHEMA.md`), which
  is the authoritative source that section always needed.
- **That at least one operation `COMPLETED`** (execution completed) — without
  claiming any specific data was recovered.
- **That an operation was `INTERRUPTED`** or `FAILED`, as recorded.
- **That no recovery operation was recorded** when the list is empty (explicit
  placeholder).

HERMES must **not**:

- Infer a recovery operation from recovered artifacts, file counts, or exit codes.
- Equate `state` with recovery effectiveness, or conflate any entry with
  `recovery_outcome` (which remains its own neutral section).
- Expose tool names, counts, or paths in the Customer Report.
- Summarize the list into claims not directly supported by the recorded fields.
- Render missing data as anything other than an explicit placeholder.

Concrete customer-facing wording (e.g. "a file recovery operation was performed")
is deferred to the report milestones (M1/M3) and to product confirmation; it is out
of scope here.

---

## 11. Invariants and required tests (Question 12)

### 11.1 Invariants

- **I1 — Existence / append-only.** An entry is appended **iff** ARCHIVE recovery
  execution was entered (`RECOVERING`). Entries are never overwritten (except the
  single `RUNNING → terminal` transition of the trailing entry), reordered, or
  removed. Decline, cancel, and prerequisite-fail-before-start append nothing.
- **I2 — Typed values.** `type ∈ {PHOTOREC}`; `state ∈ {RUNNING, COMPLETED,
  FAILED, INTERRUPTED}`. No free-text values; no reserved-but-unimplemented types.
- **I3 — Single RUNNING / trailing position.** At most one `RUNNING` entry exists,
  and it is always the last element. Terminal entries are immutable.
- **I4 — Timestamp consistency.** Every entry has `started_at`. `finished_at` is
  `null` **iff** `state == RUNNING`, and set for `COMPLETED`/`FAILED`/`INTERRUPTED`.
- **I5 — Execution, not effectiveness.** `COMPLETED ⇔ ARCHIVE result success`;
  `FAILED ⇔ ARCHIVE result failure`. Never derived from recovered counts/artifacts;
  zero recovered items never forces `FAILED` or `COMPLETED`.
- **I6 — Interruption resolution.** A `RUNNING` entry at rest (case status
  `RECOVERING`) is finalized to `INTERRUPTED` by SENTINEL at the existing
  `RECOVERING`-resume workflow point, after informing the operator and before any
  new attempt. Loading/hydration performs no write.
- **I7 — Retry gate.** A new `RUNNING` entry cannot be appended while a prior
  `RUNNING` entry is unresolved.
- **I8 — Ownership.** HERMES never writes the list; only SENTINEL persists it;
  `COMPLETED`/`FAILED` meaning is ARCHIVE's. `case.json` keeps a single writer.
- **I9 — Independence.** The list and `recovery_outcome` are independent; neither is
  computed from the other.
- **I10 — Backward compatibility.** A manifest without `recovery_operations` loads
  with `session.recovery_operations == []`; no back-fill or inference.
- **I11 — Durability.** The list is written atomically and preserved across reopen,
  status change, and archive (like `recovery_outcome`/`completed_at`).
- **I12 — No duplication.** Each entry contains only `type`, `state`, `started_at`,
  `finished_at` — none of the excluded facts in §7.

### 11.2 Tests (mirroring `test_recovery_outcome.py` / `test_completed_at.py`)

**Append & terminal update**
1. Entering `RECOVERING` appends `{PHOTOREC, RUNNING, started_at, finished_at:null}`
   as a new trailing entry (I1, I3, I4).
2. Successful ARCHIVE return updates the trailing entry to `COMPLETED`,
   `finished_at` set; no other entry changes (I4, I5).
3. Failed ARCHIVE return updates the trailing entry to `FAILED`, `finished_at` set
   (I5).
4. Zero recovered items with a normal PhotoRec end yields `COMPLETED`, not `FAILED`
   (I5, §6).

**Append-only history & retry**
5. A second attempt appends a new entry; the earlier terminal entry is byte-for-byte
   unchanged (I1, I3).
6. Only the trailing `RUNNING` entry is mutable; an attempt to update a terminal
   entry is rejected/never occurs (I3).
7. A new attempt cannot start while a prior `RUNNING` entry is unresolved (I7).

**Interruption**
8. A case persisted with `status = RECOVERING` and a trailing `RUNNING` entry, when
   resumed, is finalized to `INTERRUPTED` (`finished_at` set) at the resume point,
   with the operator informed, before recovery is offered (I6).
9. `load_case` hydration performs no write: loading such a case does not by itself
   mutate `case.json` (I6).

**Serialization / hydration / legacy**
10. Round-trip: a list with mixed states serializes and rehydrates identically
    (I11).
11. Absent `recovery_operations` hydrates to `[]` (I10).
12. Legacy manifest without the field loads successfully with an empty list; no
    inferred attempts (I10).
13. Archive preserves the list on disk (I11).
14. Reopen / subsequent status change preserves the list (I11).

**Typing & independence**
15. Enum guard: only `PHOTOREC` / the four states are valid; TESTDISK and other
    tools are absent (I2), mirroring `RecoveryOutcomeEnumTests`.
16. The list and `recovery_outcome` are written/read independently (I9).

**Reporting (read-only)**
17. HERMES renders per-entry `type`/`state`/timestamps and an explicit placeholder
    when the list is empty, without inferring from artifacts or exit codes (I8, §10).

---

## 12. Unresolved blockers / product decisions

These require the product architect (Raz); they are out of scope for this design
and are **not** assumed:

1. **Customer-facing wording** for recovery operations in the Customer Report is
   deferred to M1/M3 and must be confirmed and localized, remaining neutral and
   non-inferential (§10). Not a blocker for persisting the record.
2. **Retry gate presentation.** When a prior operation is `INTERRUPTED` and the
   operator starts a new attempt, confirm whether any additional operator prompt is
   desired beyond the existing "Interrupted recovery session" notice. The minimal
   design adds none.
3. **Timeline reconciliation (pre-existing, low priority).** `RecoveryCase.md`
   describes a human-readable Timeline distinct from `audit.log`; it is not
   implemented. `recovery_operations` now provides the authoritative structured
   attempt history; if a first-class Timeline is later introduced, confirm the two
   remain complementary (structured facts vs human narrative). (Noted in
   `Planning_Post_HERMES_Phase2.md` gap 4.)

No blocker prevents implementing the append-only record itself.

---

## 13. Constitutional alignment

- **SL-004 / EV-6 (Explain, document every recovery):** every recovery operation
  attempt becomes an explainable, recorded, structured fact.
- **EV-3 (log important actions):** complements — not replaces — ECHO; the list is
  the *authoritative structured* history, ECHO remains the raw event log.
- **AP-002 (No circular dependencies):** SENTINEL persists; ARCHIVE produces the
  outcome; HERMES only reads. No module depends on HERMES.
- **AP-003 (Facts immutable / separate from decisions):** entries are immutable once
  terminal; the list is kept independent of the `recovery_outcome` decision.
- **AP-004 (One responsibility per subsystem):** ARCHIVE executes, SENTINEL owns
  workflow/persistence and interruption finalization, ECHO records events, HERMES
  presents.
- **AP-005 / RecoveryCase single source of truth:** the authoritative attempt
  history lives in `case.json`, read by every consumer — no reliance on the
  Internal-Only `audit.log`.
- **AP-006 (Workflow before features):** appended/updated/resolved at existing
  workflow points; no new workflow step.
- **RecoveryCase Rule 6 (append-only history):** now satisfied directly by
  `recovery_operations` rather than deferred to `audit.log`.
- **Implementation-Standards / DevelopmentWorkflow:** smallest change consistent
  with the append-only requirement — one optional list, writes at existing call
  sites plus one explicit interruption-resolution write at the existing
  `RECOVERING`-resume point, one hydration line; no new module, no new dependency,
  existing workflow preserved.

---

## 14. Proposed `case.json` shape (illustrative)

Only the `recovery_operations` list is new. Everything else is unchanged.

Single successful attempt:

```json
{
    "session_id": "REC-2026-000001",
    "case_name": "Example Case",
    "created_at": "2026-07-16T10:00:00",
    "status": "COMPLETED",
    "device": { "path": "…", "model": "…", "serial": "…", "size_bytes": 123456 },
    "assessment": { "decision": "APPROVED", "reason": "…", "risk": "LOW", "confidence": 100 },
    "case_contact": { "name": "…" },
    "intake": { "recovery_request": "…" },
    "destination": { "path": "…", "model": "…" },
    "completed_at": "2026-07-16T12:00:00",
    "recovery_outcome": "PARTIAL",
    "recovery_operations": [
        {
            "type": "PHOTOREC",
            "state": "COMPLETED",
            "started_at": "2026-07-16T11:00:05",
            "finished_at": "2026-07-16T11:42:31"
        }
    ]
}
```

Interrupted first attempt, then a successful retry (append-only history):

```json
"recovery_operations": [
    {
        "type": "PHOTOREC",
        "state": "INTERRUPTED",
        "started_at": "2026-07-16T11:00:05",
        "finished_at": "2026-07-16T11:20:44"
    },
    {
        "type": "PHOTOREC",
        "state": "COMPLETED",
        "started_at": "2026-07-16T11:25:10",
        "finished_at": "2026-07-16T12:03:57"
    }
]
```

Attempt in progress or interrupted-at-rest (before resolution):

```json
"recovery_operations": [
    {
        "type": "PHOTOREC",
        "state": "RUNNING",
        "started_at": "2026-07-16T11:00:05",
        "finished_at": null
    }
]
```

---

## Related Documents

- `RecoveryCase.md`
- `RecoveryOperation.md`
- `RecoveryOperationStandard.md`
- `REPORT_SCHEMA.md`
- `REPORTING.md`
- `CustomerReport.md`
- `ArchitecturePrinciples.md`
- `SentinelLaws.md`
- `EngineeringValues.md`
