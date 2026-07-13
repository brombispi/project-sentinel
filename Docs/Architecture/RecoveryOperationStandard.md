# Recovery Operation Standard

This document defines the architectural contract that every Recovery Operation in Project Sentinel must satisfy.

It is extracted from the lifecycle already shared by forensic imaging, fingerprinting, PhotoRec, and the approved TestDisk design. It does not redesign existing operations.

---

## Common Lifecycle

Every Recovery Operation follows the same structural sequence:

```
Eligibility          Workflow has reached the point where the operation may be offered
      ↓
Presentation         SENTINEL shows objective, inputs, outputs, and paths
      ↓
Approval             Technician explicitly confirms (or declines / cancels)
      ↓
In progress          RecoveryStatus reflects the running phase (where applicable)
      ↓
Execution            ARCHIVE performs the operation and returns a result
      ↓
Reporting            SENTINEL displays the result; ECHO records events
      ↓
Post-operation       RecoveryStatus returns to the appropriate idle state
      ↓
Summary              SUMMARY reports outcome at session end
```

**Variations already present:**

| Operation | Approval model | In-progress status |
|-----------|----------------|-------------------|
| Forensic imaging | Single `[y/N]`; resume per `ImagingSafety.md` | `IMAGING` |
| Fingerprinting | Chained after immediate imaging success; retry per `ImagingSafety.md` | None dedicated |
| PhotoRec | Method selection + `[y/N]` | `RECOVERING` |
| TestDisk (planned) | Method selection + replace confirm + `[y/N]` | `RECOVERING` |

Future operations must fit this lifecycle. They may differ in approval steps and status labels, but not in ownership or result flow.

---

## Purpose

A Recovery Operation is a single, well-defined task with:

- one objective
- one execution path
- one outcome

Recovery Operations separate workflow decisions (already made before execution) from execution (performed by ARCHIVE). They do not choose strategy, assess safety, or select devices.

---

## Prerequisites

Before ARCHIVE may begin, the following must already be satisfied:

| Prerequisite | Source | Applies today |
|--------------|--------|---------------|
| Recovery Case exists with `recovery_path` | ARCHIVE | All |
| Source device identified | ARGUS / SENTINEL | All |
| AEGIS approved the source (not STOP) | AEGIS | Imaging and downstream |
| Destination approved (where case workspace requires it) | JANUS | Imaging and downstream |
| Case workspace on approved Recovery Storage (where required) | ARCHIVE relocate | Imaging and downstream |
| Required tool installed | ARCHIVE pre-check | All |
| Operation-specific inputs exist (e.g. `source.img`) | ARCHIVE pre-check | Fingerprint, PhotoRec, TestDisk |
| Prior workflow step succeeded (where chained) | SENTINEL | Fingerprint after imaging; recovery after fingerprint |

**Rule:** If any prerequisite fails, ARCHIVE returns a failure result immediately, logs `ERROR` to ECHO, and does not start the operation.

ARCHIVE does not obtain prerequisites itself. SENTINEL only offers the operation when the workflow has satisfied them.

---

## Operator Approvals

| Requirement | Standard |
|-------------|----------|
| **Who approves** | The technician (SL-006) |
| **Who solicits approval** | SENTINEL only |
| **Default** | Decline — confirmations use `[y/N]`; anything other than `y` is decline or cancel |
| **Understandability** | Operation must be understandable before approval: name, source, destination/output paths |
| **Explicit logging** | Decline, cancel, and selection are logged via ECHO `OPERATOR` |
| **No ARCHIVE prompts** | ARCHIVE never asks the technician for decisions |

**Approval tiers (already in use):**

1. **Direct confirmation** — imaging: one `[y/N]` after presentation (see `ImagingSafety.md` for acquisition states and resume).
2. **Selection + confirmation** — PhotoRec: method menu, then `[y/N]`.
3. **Chained execution** — immediate post-imaging fingerprinting: no separate approval; runs only after approved imaging succeeds in the same workflow pass. Later fingerprint retry when re-entering the workflow is governed by `ImagingSafety.md`.

Future operations use tier 1 or 2. Tier 3 is permitted only for non-interactive, read-only steps tightly bound to an already-approved parent operation (immediate post-imaging fingerprinting is the approved example).

---

## Safety Requirements

| Requirement | Implementation pattern |
|-------------|------------------------|
| **Protect original media** (SL-002) | Imaging reads the source device; recovery operations use `images/source.img` read-only or a disposable working copy — never write to the original device. Imaging safety: see `ImagingSafety.md`. |
| **Refuse wrong target** | ARCHIVE rejects execution when the resolved path equals the original device path |
| **Canonical image immutable** | See `ImagingSafety.md` |
| **Paths under case** | All artifacts remain under `<case>/`; technician does not construct paths manually |
| **Tool availability** | Missing tool → fail before execution, do not partial-run |
| **Read-only evidence ops** | Fingerprinting reads the image only; evidence written via atomic replace |
| **No silent objective change** | Operation parameters are fixed at presentation and approval time |

Safety warnings belong to **SENTINEL** (presentation/guidance). Safety enforcement belongs to **ARCHIVE** (refuse invalid targets, prerequisite checks).

---

## Forensic Imaging Safety

Acquisition states, resumable ddrescue imaging, canonical image immutability, fingerprint retry, mounted-descendant safety, source identity validation, and ARCHIVE imaging enforcement are defined in `ImagingSafety.md`.

This document retains the generic Recovery Operation lifecycle only.

---

## Idempotency

**Recovery Operations should be idempotent whenever practical.**

- Re-running an operation must not damage existing case evidence.
- Atomic writes should be used where appropriate.
- Existing outputs must be replaced, reused, or preserved according to an explicit operation-specific rule.
- TestDisk creates a fresh working copy when replacement is approved.
- Imaging idempotency, resume, and canonical image rules: see `ImagingSafety.md`.

---

## Execution Ownership

| Role | Responsibility |
|------|----------------|
| **SENTINEL** | Determines eligibility; presents operation; obtains approval; updates RecoveryStatus; delegates to ARCHIVE; displays result |
| **ARCHIVE** | Prerequisite checks; execution; artifact collection; returns result dict; ECHO start/end events |
| **ECHO** | Append-only audit of significant events |
| **SUMMARY** | End-of-session human-readable aggregation; no decisions |
| **ORACLE** | May recommend recovery methods before selection; does not execute |
| **AEGIS / JANUS** | Must have already approved before operation is offered; not involved during execution |

**Invariant:** Recovery Operations never make strategic decisions. They execute decisions already made by the workflow.

---

## ECHO Logging Requirements

Every Recovery Operation must produce audit events in `audit.log`:

| Event | Module | Level | When |
|-------|--------|-------|------|
| Operation started | ARCHIVE | INFO | After prerequisites pass, before tool execution |
| Operation completed | ARCHIVE | INFO | On normal completion |
| Operation failed | ARCHIVE | ERROR | On tool failure, I/O error, or prerequisite failure |
| Operator approved selection | SENTINEL | OPERATOR | Method chosen or execution confirmed |
| Operator declined / cancelled | SENTINEL | OPERATOR | Confirmation rejected or menu cancelled |
| Recommendation (recovery methods) | ORACLE | INFO | Before recovery method selection |

**Rules:**

- ECHO is append-only.
- Log messages identify the operation and relevant paths.
- Status changes made by SENTINEL via `update_status` also produce `SESSION` INFO events.

---

## Result Contract

ARCHIVE always returns a result dictionary to SENTINEL. Every operation shares this **minimum core**:

| Field | Type | Meaning |
|-------|------|---------|
| `success` | bool | Execution completed without tool-reported failure |
| `status` | string | Operation outcome label (`completed`, `failed`, `ended`, etc.) |
| `message` | string | Human-readable explanation |
| `artifacts` | list | Paths to observable outputs created or updated |

Operations may add fields (e.g. `digest`, `recovered_file_count`). The core four are mandatory.

### Execution success versus recovery outcome

Recovery Operations report three distinct concepts:

| Concept | Meaning |
|---------|---------|
| **Execution success** | ARCHIVE completed the operation without tool-reported failure. Reflected in `success` and `status`. |
| **Tool-reported outcome** | What the external tool returned (for example, exit code 0, session ended normally). |
| **Recovery outcome** | Whether customer data was recovered. Determined by the presence or absence of recovered artifacts. |

**Rule:** Recovery Operations report execution success separately from recovery outcome. The presence or absence of recovered artifacts determines recovery outcome.

Clarifications:

- Exit code 0 may mean only that an interactive session ended normally.
- Zero recovered files is not automatically an execution failure.
- Sentinel must not claim that customer data was successfully recovered based only on a tool exit code.

Prerequisite failure returns `success: false` before the in-progress phase begins.

SENTINEL displays the result. SUMMARY consumes it at session end. No module other than ARCHIVE constructs the result.

---

## Artifact Ownership

| Category | Location | Owner | Examples |
|----------|----------|-------|----------|
| **Acquisition** | `images/` | ARCHIVE (imaging) | `source.img`, `source.map` |
| **Integrity evidence** | `evidence/` | ARCHIVE (fingerprint) | `source.sha256`, `acquisition_source.json` |
| **Tool logs** | `evidence/` | ARCHIVE (execution cwd / params) | `testdisk.log` |
| **Recovered customer data** | `recovered/` | ARCHIVE initiates; tool writes | `recovered/recup.*`, `recovered/testdisk/` |
| **Working copies** | `working/` | ARCHIVE (prep step) | `working/testdisk.img` |
| **Observation evidence** | `evidence/` | ARGUS | `source.smart.txt` |

**Rules:**

- All artifacts remain under `<session.recovery_path>`.
- ARCHIVE resolves paths; the technician does not enter filenames.
- Evidence uses atomic write where applicable (fingerprint: `.tmp` → replace).
- Artifacts are listed in the result `artifacts` field when observable after execution.

---

## Summary Reporting

SUMMARY runs once at session end. It:

- does not make decisions
- translates module outputs into a fixed human-readable block
- reports whether each operation was performed, declined, cancelled, or failed
- reports operation-specific outcomes (hash recorded, session ended, file counts)

**Per-operation summary states (already used):**

| State | Label pattern |
|-------|---------------|
| Not reached | `Not performed` |
| Declined by operator | `Declined by operator` |
| Cancelled at selection | `Cancelled by operator` |
| Executed successfully | `Completed` / `Hash recorded` / `Ended normally` |
| Executed with failure | `Failed` |

Future operations must define their summary line and map to these states. SUMMARY does not interpret strategy or safety.

---

## Failure Behaviour

| Failure point | ARCHIVE | SENTINEL | RecoveryStatus |
|---------------|---------|----------|----------------|
| Prerequisite not met | Return failure result; ECHO ERROR | Display message; do not enter in-progress | Unchanged from pre-offer state |
| Operator declines | Not invoked | ECHO OPERATOR; optional declined flag for SUMMARY | Unchanged |
| Operator cancels selection | Not invoked | ECHO OPERATOR | Unchanged |
| Tool missing | Return failure; ECHO ERROR | Display message | In-progress not entered |
| Execution fails | Return failure; ECHO ERROR | Display result block | Revert or hold per operation mapping |
| Execution succeeds | Return success; ECHO INFO | Display result block | Advance to post-operation state |
| Chained child fails | Return failure | Display; may block downstream offers | Parent state reflects last successful step |

**Rules:**

- ARCHIVE never raises unhandled failures to the operator; it always returns a result dict.
- Failure does not silently skip logging.
- Downstream operations are not offered when an upstream chained step fails.

---

## Status Transitions

RecoveryStatus expresses case workflow phase, not individual operation micro-states. Operations map to status as follows:

| Operation | Pre-operation status | In-progress status | Post-operation status (success) | Post-operation status (failure / decline) |
|-----------|---------------------|--------------------|---------------------------------|---------------------------------------------|
| Forensic imaging | `READY_FOR_IMAGING` | `IMAGING` | `READY_FOR_RECOVERY` (after fingerprint) | Decline: `READY_FOR_IMAGING`; imaging fail: remains `IMAGING` |
| Fingerprinting | (during imaging flow) | None dedicated | `READY_FOR_RECOVERY` | `READY_FOR_IMAGING` |
| PhotoRec | `READY_FOR_RECOVERY` | `RECOVERING` | `READY_FOR_RECOVERY` | `READY_FOR_RECOVERY` |
| TestDisk (planned) | `READY_FOR_RECOVERY` | `RECOVERING` | `READY_FOR_RECOVERY` | `READY_FOR_RECOVERY` |

**Standard for future operations:**

1. Use an existing RecoveryStatus value for in-progress when one fits (`IMAGING`, `RECOVERING`).
2. Transition to in-progress only after operator approval.
3. Transition out of in-progress after ARCHIVE returns, regardless of success or failure.
4. Do not introduce new status values without architectural approval.
5. Every transition is persisted via `update_status` and logged by ECHO.

---

## Operation Comparison

| Aspect | Imaging | Fingerprint | PhotoRec | TestDisk (planned) |
|--------|---------|-------------|----------|-------------------|
| ARCHIVE executes | Yes | Yes | Yes | Yes |
| SENTINEL presents | Yes | Yes (implicit) | Yes | Yes |
| Operator approval | Direct | Chained | Selection + direct | Selection + replace + direct |
| Tool pre-check | Yes | Yes | Yes | Yes |
| ECHO start/end | Yes | Yes | Yes | Yes |
| Result dict | Yes | Yes | Yes | Yes |
| Artifacts under case | Yes | Yes | Yes | Yes |
| SUMMARY line | Yes | Yes | Yes | Yes |
| Protects original | Reads only | N/A (image) | Image only | Working copy only |

---

## Architectural Contract

Every future Recovery Operation **must**:

1. Have a single clear objective understandable before execution.
2. Wait until workflow prerequisites are satisfied before being offered.
3. Be presented by SENTINEL with objective, inputs, and output paths.
4. Receive explicit technician approval (except approved chained read-only sub-steps).
5. Transition RecoveryStatus to in-progress only after approval.
6. Be executed by ARCHIVE without technician interaction inside ARCHIVE.
7. Verify tool availability and inputs before starting.
8. Enforce safety rules at the ARCHIVE boundary (correct target, case-local paths).
9. Log start, completion, and failure to ECHO.
10. Return the core result contract to SENTINEL.
11. Store artifacts under the Recovery Case in the appropriate subtree.
12. Be reported by SUMMARY with performed / declined / cancelled / failed semantics.
13. Restore or hold RecoveryStatus appropriately after ARCHIVE returns.
14. Be idempotent whenever practical.
15. Report execution success separately from recovery outcome.

Every future Recovery Operation **must not**:

- Make strategic or safety decisions.
- Prompt the technician from ARCHIVE.
- Write outside the Recovery Case workspace.
- Modify or replace the canonical `images/source.img` except as permitted by `ImagingSafety.md`.
- Operate on the original customer device for recovery-class operations.
- Skip ECHO logging for significant events.
- Claim customer data was successfully recovered based only on a tool exit code.

---

## Relationship to Existing Documentation

This standard refines `Docs/Architecture/RecoveryOperation.md` by grounding it in observed implementation. It does not replace that document.

Forensic imaging safety is defined in `ImagingSafety.md`. Where this document previously stated imaging-specific rules, `ImagingSafety.md` takes precedence.
