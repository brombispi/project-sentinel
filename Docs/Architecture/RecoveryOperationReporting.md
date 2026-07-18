# Recovery Operation Reporting (Milestone M3)

Version: 0.2
Status: Approved (implemented)
Author role: Cursor (implementation assistant)
Scope: Make HERMES consume the authoritative `recovery_operations` history
(M2) wherever it currently **infers** recovery work, without changing report
architecture, `ReportFormatter`, report sections, localization, or case fields.

---

## Revision / milestone note

Version 0.2 records the approved implementation decision: the replaced
Technician field is **renamed** from "Recovery Present" to **"Recovery Attempt
Recorded"**, and its truth is derived from the *existence* of any recorded
recovery attempt in `recovery_operations` — **any** valid state (`RUNNING`,
`COMPLETED`, `FAILED`, `INTERRUPTED`) counts. An operation need **not** be
`COMPLETED`. The rename makes the field self-describing: it reports whether an
authoritative recovery *execution attempt* exists, which is a separate fact from
recovered artifacts, recovered-item count, and operation success.

`Planning_Post_HERMES_Phase2.md` §2 numbered "M3" as *report-content
localization*. By product decision that slot is now **consuming
`recovery_operations` in HERMES**; localization is deferred to a later
milestone. This is consistent with the M2 record design, which explicitly
persisted the structured history but **did not** consume it in reports
(`RecoveryOperationRecord.md` §10: "This milestone does not implement or
finalize report wording"). M3 closes exactly that consumption gap, and only
that gap.

This document records the approved M3 design that is now implemented in
`Source/modules/hermes.py` and `Tests/test_hermes.py`. It is subordinate to and
consistent with the Sentinel Constitution and the existing reporting documents
(`REPORTING.md`, `REPORT_SCHEMA.md`, `CustomerReport.md`,
`RecoveryOperationRecord.md`).

---

## 1. Problem statement

Before M2 there was no authoritative, persisted proof that a recovery
operation executed, so HERMES was deliberately built to **avoid inferring
one** (`CustomerReport.md` corrections 2–3, §6, §10). It therefore either
stayed silent about recovery execution or, in one place, used the presence of
recovered artifacts on disk as a **proxy** for "recovery happened."

M2 added the authoritative source: an append-only `recovery_operations` list in
`case.json` (`RecoveryOperationRecord.md`), each entry carrying `type`,
`state` (`RUNNING`/`COMPLETED`/`FAILED`/`INTERRUPTED`), `started_at`, and
`finished_at`. M3's job is to point HERMES at that authoritative list wherever
it currently infers, and nowhere else.

Key distinction that governs every finding below (from
`RecoveryOperationRecord.md` §6 and `CustomerReport.md` correction 2):

- **"A recovery operation was performed"** is a fact — now proven only by
  `recovery_operations`.
- **"Recovered artifacts exist on disk / how many / how large"** is a separate
  observation — proven by `summarize_recovered_artifacts(...)`, which "proves
  artifacts exist; it does **not** prove an operation ran, and must not be used
  to infer one."

HERMES may show both, but it must source each from its own authority and never
derive the first from the second.

---

## 2. What was inspected

- `Source/modules/hermes.py` — both report builders (Technician, Customer) and
  every field-building helper.
- `Source/modules/report_formatter.py` — confirms formatting is structural only
  (no recovery logic); **out of scope, unchanged**.
- `Source/modules/archive.py` — `summarize_recovered_artifacts(...)`
  (`recovery_present = recovered_directory_count > 0 or recovered_file_count > 0`)
  and `execute_photorec_recovery(...)`.
- `Source/modules/manifest.py` — `read_case_manifest(...)` returns the whole
  manifest dict, and M2's `write_case_manifest` persists `recovery_operations`
  only when non-empty. HERMES already calls `read_case_manifest`, so the list is
  reachable **through the existing owner API** with no new read path.
- `Docs/Architecture/REPORT_SCHEMA.md`, `CustomerReport.md`,
  `RecoveryOperationRecord.md`.
- `Tests/test_hermes.py` — existing report contracts and the owner-API rule
  (`test_recovery_statistics_uses_owner_api_not_filesystem`).

Report sections implemented **in code today** (the fixed set M3 must not
change):

- **Technician** (`TECHNICIAN_REPORT_SECTIONS`): Case Information, Customer
  Information, Intake Summary, Device Identity, Assessment Results, Imaging
  Details, Integrity Verification, **Recovery Statistics**, Audit Timeline.
- **Customer** (`CUSTOMER_REPORT_SECTIONS`): Case Information, Device Received,
  Problem Description, **Work Performed**, Recovery Outcome, Files Recovered,
  Recommendations, Disclaimer.

Note: `REPORT_SCHEMA.md` also defines a Technician **"Recovery execution"**
section (type/status/result). **It is not implemented in code.** Implementing
it would be *adding a report section*, which this milestone's constraints
forbid; it is therefore explicitly out of scope (see §6).

---

## 3. Every place HERMES touches recovery work

The table locates every point in `hermes.py` that says or implies something
about recovery execution, and classifies each as an **inference to replace** or
**already authoritative / observational → no change**.

| # | Location | What it does | Inference? |
|---|---|---|---|
| 1 | Technician → Recovery Statistics → **"Recovery Present"** (`_build_recovery_statistics`), renamed to **"Recovery Attempt Recorded"** | `"Yes"/"No"` from `summarize_recovered_artifacts()["recovery_present"]` (disk artifact presence) | **Yes — replace** |
| 2 | Technician → Recovery Statistics → counts/size/locations | Observational artifact statistics from the ARCHIVE owner API | No — observational, keep |
| 3 | Technician → **Audit Timeline** (`_build_audit_timeline`) | Renders raw ECHO `audit.log` lines, unparsed | No — non-authoritative raw presentation, keep |
| 4 | Customer → **Work Performed** (`_build_customer_work_performed`) | States imaging only; deliberately silent on recovery | No inference — silent (see §5) |
| 5 | Customer → **Files Recovered** (`_build_files_recovered`) | Observational artifact count/size from ARCHIVE owner API | No — observational, keep |
| 6 | Customer → **Recovery Outcome** (`_build_customer_recovery_outcome`) | Neutral wording from operator `recovery_outcome` | No — authoritative decision, keep |

The detailed per-occurrence analysis required by the milestone follows.

### Occurrence 1 — Technician "Recovery Present" → "Recovery Attempt Recorded" (the one genuine inference)

1. **Current implementation.**

```348:368:Source/modules/hermes.py
    def _build_recovery_statistics(self, recovered_summary):
        locations = recovered_summary["recup_directories"]
        ...
        return {
            "Recovery Present": (
                "Yes" if recovered_summary["recovery_present"] else "No"
            ),
            "Recovered File Count": recovered_summary["recovered_file_count"],
            ...
        }
```

where `recovery_present` is computed by ARCHIVE as
`recovered_directory_count > 0 or recovered_file_count > 0` — a pure disk scan.

2. **Why it is an inference.** "Recovery Present" answers *"was a recovery
   operation performed?"* using the presence of recovered files as a proxy.
   This is exactly the inference the constitution and M2 forbid
   (`RecoveryOperationRecord.md` §1/§7; `CustomerReport.md` correction 2). It is
   wrong in both directions now that an authoritative record exists:
   - **False negative:** a `COMPLETED` PhotoRec operation that recovered **zero
     files** (a legitimate, non-failure result per `RecoveryOperationRecord.md`
     §6) shows "Recovery Present: **No**", contradicting the recorded operation.
   - **False positive:** artifacts present with **no recorded operation** (e.g.
     a legacy case) show "Recovery Present: **Yes**" without authoritative
     proof an operation ran.

3. **Which `recovery_operations` field(s) replace it.** The **existence** of
   entries in `recovery_operations`. "Was a recovery attempt recorded" is
   answered by the list being non-empty; **any** valid `state` counts (§4). No
   other per-entry field is read.

4. **Report affected.** **Technician Report only.** (The Customer Report never
   exposed this field — §5.)

5. **Internal vs visible wording.** **Changes visible wording** of the
   Technician Report: the field is **renamed** "Recovery Present" →
   **"Recovery Attempt Recorded"**, and its *value* is now derived from the
   operation record (correcting the cases above). No section is added, removed,
   or reordered, and the field keeps its position as the first field of the
   Recovery Statistics section. The Technician Report is not localized
   (`CustomerReport.md` §8), so this touches **no** localization.

### Occurrences 2 & 5 — recovered-artifact statistics (no change)

The recovered file count, directory count, size, and output locations
(Technician "Recovery Statistics"; Customer "Files Recovered") are **honest
observations of artifacts prepared for delivery**, sourced from the ARCHIVE
owner API and already documented as observational, not as proof of an operation
(`CustomerReport.md` §4.6). `RecoveryOperationRecord.md` §7 explicitly keeps
recovered counts owned by `summarize_recovered_artifacts(...)` and read **live**
— they must **not** be moved into `recovery_operations`. **Recommendation: no
change.**

### Occurrence 3 — Technician "Audit Timeline" (no change)

`_build_audit_timeline` renders raw ECHO log lines and, by contract, does not
parse or interpret them. `audit.log` is **Internal Only** and must not become an
authoritative structured source (`RecoveryOperationRecord.md` §1, §7). HERMES
draws no fact from it; it is presentation of a raw log, not inference.
**Recommendation: no change.** (Do **not** "upgrade" the timeline to read
`recovery_operations`; that would add a new authoritative claim in a raw-log
section and blur ownership.)

### Occurrence 4 — Customer "Work Performed" (already authoritative; recovery narration deferred)

1. **Current implementation.** `_build_customer_work_performed` returns imaging
   only, from the authoritative `classify_acquisition_state(...)`:

```475:478:Source/modules/hermes.py
    def _build_customer_work_performed(self, acquisition_state):
        return {
            "Imaging": _customer_imaging(acquisition_state.get("state")),
        }
```

2. **Why it is *not* an inference.** It deliberately says nothing about recovery
   execution precisely to avoid inferring one (`CustomerReport.md` correction 2,
   §4.4, §10; guarded by `test_no_recovery_operation_claim_from_artifacts`). The
   imaging statement it *does* make is authoritative. So there is **no existing
   inference to replace** here.

3. **Relationship to `recovery_operations`.** M2 now makes it *possible* to add
   an authoritative, neutral recovery statement here. But adding one is **new
   customer-facing content**, not the replacement of an inference, and:
   - it introduces new customer-visible wording, which
     `RecoveryOperationRecord.md` §12 (blocker 1) explicitly **defers** to a
     wording/product decision that "must be confirmed and localized"; and
   - any such wording is a new translatable string, which collides with this
     milestone's **"avoid changing localization"** constraint.

4. **Report affected / visibility.** Customer Report; would change visible
   wording — therefore **out of scope for M3**.

5. **Recommendation.** **No change in M3.** Record it as the single deferred
   item (§7), to be taken up when customer recovery wording is confirmed and
   localization is in scope.

### Occurrence 6 — Customer "Recovery Outcome" (no change)

Sourced from the operator's `recovery_outcome` decision, mapped to neutral
wording. It is an authoritative decision, independent of `recovery_operations`
by design (`RecoveryOperationRecord.md` §6, I9). **Recommendation: no change.**

---

## 4. Authoritative rule for the renamed field (approved)

The field is renamed **"Recovery Present" → "Recovery Attempt Recorded"** and
sourced from the authoritative `recovery_operations` list instead of disk
artifact presence:

- **"Recovery Attempt Recorded" = "Yes"** iff `recovery_operations` contains at
  least one recorded recovery attempt.
- **"Recovery Attempt Recorded" = "No"** iff `recovery_operations` is **absent
  or empty** (no recovery attempt recorded; includes legacy cases that hydrate
  to `[]`, `RecoveryOperationRecord.md` §9).
- **Any valid operation state counts** — `RUNNING`, `COMPLETED`, `FAILED`,
  `INTERRUPTED`. An operation is **not** required to be `COMPLETED`.

Rationale (approved): the field reports whether an authoritative recovery
*execution attempt* exists — not whether it succeeded, and not whether artifacts
were produced. Attempted-but-`INTERRUPTED`/`FAILED`/`RUNNING` operations are
still recorded recovery attempts. Execution success is carried separately by
per-entry `state` and, at the case level, by `recovery_outcome`
(`RecoveryOperationRecord.md` §6, I9); recovered artifacts are carried by the
observational counts (Occurrence 2). The rename removes the earlier ambiguity of
"Recovery Present" (which read as either "an operation happened" or "artifacts
exist").

The observational counts (Occurrence 2) remain sourced from the ARCHIVE
summary, unchanged. A technician then sees an honest, decoupled set, e.g. a
completed operation that recovered nothing renders "Recovery Attempt Recorded:
Yes" with "Recovered File Count: 0", which is now correct rather than
contradictory.

---

## 5. Reports already using authoritative data (explicit "no change")

Per the milestone's instruction to call these out explicitly:

- **Customer "Work Performed" — Imaging:** already authoritative
  (`classify_acquisition_state`). No change.
- **Customer "Recovery Outcome":** already authoritative (`recovery_outcome`).
  No change.
- **Customer "Files Recovered" / Technician recovered counts:** already correct
  observational data via the ARCHIVE owner API. No change.
- **Technician "Audit Timeline":** already a raw, non-authoritative
  presentation. No change.
- **`ReportFormatter`:** structural only. No change.

Only **one** field in the entire reporting surface — Technician "Recovery
Present" — currently infers recovery work and is in scope for replacement.

---

## 6. Smallest implementation plan

Consume `recovery_operations` in exactly one place, through the owner API HERMES
already uses, changing no section, formatter, localization, or case field.

**Code changes — `Source/modules/hermes.py` (only file):**

1. **Read the authoritative list from the manifest HERMES already loads.** In
   `build_technician_report()`, obtain `recovery_operations` from the
   `manifest` dict already returned by `self._load_manifest()`
   (`manifest.get("recovery_operations", [])`). No new import, no new owner API,
   no filesystem traversal — preserving
   `test_recovery_statistics_uses_owner_api_not_filesystem`.

2. **Rename the field and source it from the record.** Change
   `_build_recovery_statistics(...)` to emit **"Recovery Attempt Recorded"**
   (replacing "Recovery Present") with a value derived from the
   recovery-operations list (per §4) instead of
   `recovered_summary["recovery_present"]`. Keep every other field in the section
   exactly as-is (still from `recovered_summary`) and keep the renamed field in
   the same first position. The builder gains one argument (the list) and a tiny
   private helper `_recovery_attempt_recorded(recovery_operations) ->
   "Yes"|"No"`.

That is the entire production change: **one file, one field's data source.**

**What is explicitly NOT changed (constraint compliance):**

- No new/removed/reordered report sections (the schema's unimplemented
  "Recovery execution" section is **not** added).
- No new fields (the plan *renames and re-sources* an existing field; it does
  not add per-operation type/state/timestamp fields — those need the
  out-of-scope section).
- No `ReportFormatter` change.
- No localization change (Technician Report is not localized; no new strings).
- No new case field (`recovery_operations` already exists from M2).
- No Customer Report change (Occurrence 4 deferred).
- No write path: HERMES stays strictly read-only (AP-002/AP-004,
  `RecoveryOperationRecord.md` I8).

**Test changes — `Tests/test_hermes.py`:**

- Rename the expected field to "Recovery Attempt Recorded" and update
  `test_recovery_statistics_populated_summary` /
  `test_recovery_statistics_empty_summary` /
  `test_recovery_statistics_uses_owner_api_not_filesystem` /
  `test_recovery_statistics_markdown_renders_after_integrity` so the field
  reflects the recovery-operations list rather than artifact presence.
- Add per approved list: `COMPLETED` with zero artifacts ⇒ "Yes"; `FAILED`
  with zero artifacts ⇒ "Yes"; `INTERRUPTED` ⇒ "Yes"; `RUNNING` ⇒ "Yes";
  legacy artifacts on disk with no operation records ⇒ "No"; absent
  `recovery_operations` ⇒ "No"; recovered counts still come from the owner API.
- Confirm the Recovery Statistics field set/order is unchanged apart from the
  rename.

---

## 7. Is an operation ID needed? / Unresolved decisions

- **Operation ID:** Not needed. M3 consumes only the *existence* of entries;
  it does not cross-reference individual operations. (`RecoveryOperationRecord.md`
  §3.3 already concluded no ID is required.)
- **"Recovery Present" semantics (minor product decision):** non-empty list
  (default, recommended) vs "any `COMPLETED`". §4.
- **Deferred (not a blocker):** authoritative recovery narration in the Customer
  Report "Work Performed" and any richer Technician "Recovery execution"
  section (per-operation type/state/timestamps). Both require the deferred
  customer-wording decision (`RecoveryOperationRecord.md` §12.1) and/or a new
  report section, and the latter also intersects the later localization
  milestone. Recommend addressing them together once wording + localization are
  in scope.

---

## 8. Expected user-visible differences

- **Technician Report — "Recovery Present" is renamed to "Recovery Attempt
  Recorded"** and now authoritative:
  - Any recorded attempt (`RUNNING`/`COMPLETED`/`FAILED`/`INTERRUPTED`),
    including a completed operation that recovered zero files: **"Yes"**
    (previously "No" for the zero-artifact case).
  - Recovered artifacts on disk but no recorded operation (legacy/edge):
    **"No"** (previously "Yes"; no-inference — counts still shown).
  - All other Recovery Statistics fields (counts, size, locations): unchanged.
- **Customer Report:** **no visible change** (recovery narration deferred).
- No change to any other section, report, or the Markdown structure.

---

## 9. Architectural risks

1. **Field meaning vs adjacent counts.** "Recovery Attempt Recorded" reports
   whether an attempt is recorded, while the adjacent counts describe artifacts.
   For legacy cases these can appear to disagree ("Recovery Attempt Recorded:
   No" with counts > 0). This is *honest* (no back-fill,
   `RecoveryOperationRecord.md` §9); the rename directly mitigates the earlier
   ambiguity by naming exactly what the field measures.
2. **Legacy-case output drift.** Existing cases without `recovery_operations`
   will report "Recovery Attempt Recorded: No" regardless of artifacts. This is
   the intended no-inference behaviour and is covered by a test, but it does
   change historical Technician Report output for such cases.
3. **Owner-API discipline.** The list must be read from the manifest dict
   HERMES already loads, never by scanning `case.json` or the workspace
   directly, to preserve the existing read-only/owner-API contract.
4. **Scope creep pressure.** The obvious "next step" (a full Recovery execution
   section / customer recovery sentence) is deliberately excluded; implementing
   it here would violate the no-new-section and no-localization constraints.
   Risk is process, not code — kept explicit in §7.
5. **Field rename discoverability.** Downstream consumers keying on the literal
   "Recovery Present" label would need updating. The only such consumer is
   `Tests/test_hermes.py`, updated in this milestone; no PDF/export or external
   integration reads report labels today.

All risks are low and contained to a single Technician field.

---

## 10. Constitutional alignment

- **AP-002 / AP-004 (no circular deps, one responsibility):** HERMES only reads;
  ARCHIVE owns execution outcome; SENTINEL owns persistence. Unchanged.
- **AP-003 (facts immutable, separate from decisions):** replaces an inference
  with a recorded fact; keeps `recovery_operations` and `recovery_outcome`
  independent.
- **AP-005 / single source of truth:** the reported fact now comes from
  `case.json`, not from a disk-artifact proxy or the Internal-Only `audit.log`.
- **SL-004 / EV-6 (explain / document recovery):** the Technician Report now
  states recovery-operation occurrence from the authoritative record.
- **Implementation-Standards / smallest change:** one file, one field's data
  source, existing owner API, existing section — the minimum that removes the
  inference.

---

## 11. Return summary (design deliverable)

- **Document:** `Docs/Architecture/RecoveryOperationReporting.md` (this
  document), updated to v0.2 with the approved label and semantics.
- **Code changes:** `Source/modules/hermes.py` only — read `recovery_operations`
  from the already-loaded manifest and populate Technician **"Recovery Attempt
  Recorded"** from it (§4, §6); matching test updates/additions in
  `Tests/test_hermes.py`.
- **Expected user-visible differences:** §8 — Technician "Recovery Present" is
  renamed to "Recovery Attempt Recorded" and becomes authoritative; Customer
  Report unchanged.
- **Architectural risks:** §9 — field meaning vs adjacent counts and legacy-case
  output drift; both low and tested.

---

## Related Documents

- `RecoveryOperationRecord.md`
- `REPORT_SCHEMA.md`
- `REPORTING.md`
- `CustomerReport.md`
- `RecoveryOperationStandard.md`
- `ArchitecturePrinciples.md`
- `SentinelLaws.md`
- `EngineeringValues.md`
