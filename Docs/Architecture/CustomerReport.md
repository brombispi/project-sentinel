# Customer Report (HERMES Phase 2)

Version: 0.3
Status: Implemented

---

## Purpose

This document defines the architecture of the **Customer Report** produced by
HERMES. It is a design document only; it does not authorise implementation.

It is subordinate to and consistent with:

- `REPORTING.md` — reporting architecture, HERMES responsibility, generation flow
- `REPORT_SCHEMA.md` — report content schema and information classification
- `RecoveryCase.md` — the Recovery Case as single source of truth
- The Sentinel Constitution (`SentinelLaws.md`, `ArchitecturePrinciples.md`,
  `EngineeringValues.md`)

The Customer Report is a delivery-facing summary generated at the Delivery phase,
after recovery and verification are complete, only on explicit technician
approval (SL-006).

This revision (0.2) applies mandatory corrections concerning what can be
*authoritatively proven* from recorded case data, and narrows the design to the
existing Technician Report and `ReportFormatter` architecture.

---

## 1. Architecture: reuse, do not redesign

The Customer Report reuses the exact pattern already proven by the Technician
Report in `Source/modules/hermes.py`. Phase 2 adds a parallel trio of methods and
one section-order constant. It introduces **no** new module, **no** new data
model, and **no** change to `ReportFormatter`.

Existing Technician Report shape to mirror:

- `build_technician_report()` returns an ordered dict of sections → field dicts.
- `build_technician_markdown()` calls
  `ReportFormatter().format_markdown(title, report, section_order=...)`.
- `save_technician_report()` writes `reports/technician_report.md`.

Customer Report equivalents (Phase 2):

- `build_customer_report()` — replaces the current `NotImplementedError` stub.
- `build_customer_markdown()` — delegates to the existing `ReportFormatter`.
- `save_customer_report()` — writes `reports/customer_report.md`.
- `CUSTOMER_REPORT_SECTIONS` / `CUSTOMER_REPORT_FILENAME` constants, mirroring
  `TECHNICIAN_REPORT_SECTIONS` / `TECHNICIAN_REPORT_FILENAME`.

`ReportFormatter.format_markdown` is used unchanged. No formatter redesign is
proposed or required.

---

## 2. Report generation flow

```
Case work complete + verification reviewed
        ↓
SENTINEL offers reporting (Delivery phase)
        ↓
Technician explicitly approves Customer Report generation      (SL-006)
        ↓
Hermes(session).build_customer_report()
        ↓
  read_case_manifest(case.json)        → identity, contact, intake, device, completion, outcome
  classify_acquisition_state(...)      → whether a forensic image was created (authoritative)
  summarize_recovered_artifacts(...)   → observable recovered artifacts for delivery (observational only)
        ↓
Assemble Customer-Visible sections (structured data + versioned HERMES policy content)
        ↓
ReportFormatter.format_markdown("Customer Report", ..., section_order=CUSTOMER_REPORT_SECTIONS)
        ↓
save_customer_report() → reports/customer_report.md   (refuse to overwrite)
        ↓
ECHO records report generation
        ↓
Case proceeds to archival
```

Invariants (identical to the Technician Report):

- **Read-only.** HERMES calls existing owner read-APIs only. It never writes to
  `case.json`, the timeline, or evidence.
- **No inference.** Missing or unrecorded data renders as an explicit placeholder.
  HERMES never fabricates or derives facts (AP-003).
- **Same-source rule.** Every case-derived field traces to a fact recorded by its
  owning module. HERMES summarises; it does not create case facts.

---

## 3. Why `case.json` remains the only source of truth

- **Constitutional invariant.** `RecoveryCase.md` designates the Recovery Case as
  the single source of truth; modules communicate through it. The Customer Report
  is a derived, non-authoritative artifact (`REPORTING.md`).
- **Owner-API access only.** HERMES already reads through
  `read_case_manifest`, `classify_acquisition_state`,
  `summarize_recovered_artifacts`, etc., and the existing test
  `test_recovery_statistics_uses_owner_api_not_filesystem` forbids HERMES from
  traversing the workspace itself. Phase 2 keeps this rule.
- **One fact set, two audiences.** Customer and Technician reports derive from the
  same `case.json` and evidence, so they cannot contradict each other. If a fact
  is not in the case, it appears in neither report.

### 3.1 Reproducibility (corrected)

**Case facts are reproducible; report-generation metadata is not.**

Regenerating the Customer Report from an unchanged `case.json` and evidence yields
identical *case-derived* content (identity, device, intake, completion, outcome,
imaging state). However, **report-generation metadata changes on each run** — in
particular the "Report Generated" timestamp (`datetime.now()`) and, if policy
content is revised, the recommendation/disclaimer version. Reproducibility is
therefore a property of the recorded case facts, not of the rendered document as
a whole.

---

## 4. Report sections and field mapping

Sections follow `REPORT_SCHEMA.md` (Customer Report), corrected as below. All
case-derived fields exist today in `case.json` (`Source/modules/manifest.py`) or
via a named owner API.

### 4.1 Case Information

| Report field | Source | Notes |
|---|---|---|
| Case Number | `session_id` | via `read_case_manifest` |
| Customer Name | `case_contact.name` | identity only |
| Case Completed | `completed_at` | recorded when status → COMPLETED (`session_manager.py`), persisted in `case.json` |
| Report Generated | `datetime.now()` | report-generation metadata, **not** a case fact |

**"Case Completed" and "Report Generated" are separate fields** (correction 5).
The former is the recorded completion time of the case; the latter is the moment
the document was produced.

**Excluded:** `status` (internal workflow state), `created_at`, assigned
technician, `case_contact.phone`, `case_contact.email`.

### 4.2 Device Received

| Report field | Source | Notes |
|---|---|---|
| Device | `device.model` | customer-appropriate label |
| Capacity | `device.size_bytes` via `format_bytes` | natural units (e.g. "465.8 GB"); falls back to the recorded `device.size` string, then to "Not recorded" |
| Number of devices received | the source `device` block only | see below |

Capacity reuses the existing `format_bytes` helper (`Source/modules/summary.py`)
over the recorded `device.size_bytes` to produce natural units. No new formatter
is introduced. When `size_bytes` is absent, the recorded `device.size` string is
used; when neither is present, the placeholder is shown.

**The customer-submitted device is the source `device` only.** The `destination`
block is Sentinel-provided recovery target media and is **never** counted or
described as a device received from the customer (correction 1). "Number of
devices received" reflects the source device(s) the customer submitted, never the
destination.

**Excluded:** `device.path`, `device.serial`, `device.transport`,
`device.filesystem`, `device.size_bytes`, `device.role`, all SMART evidence
(ARGUS), and the entire `destination` block.

### 4.3 Problem Description

| Report field | Source |
|---|---|
| Requested recovery | `intake.recovery_request` |
| What happened | `intake.incident_description` |
| Most important data | `intake.data_priority` |

Customer-authored intake text is presented as recorded.

**Excluded:** `intake.previous_recovery_attempts` (correction 8).

### 4.4 Work Performed (limited to provable facts)

Only facts with an authoritative, persisted record appear here.

| Report field | Source | Authoritative? |
|---|---|---|
| Imaging | `classify_acquisition_state(...)` → one of three neutral states | Yes — persisted evidence |

- **Imaging is provable.** `classify_acquisition_state` authoritatively
  establishes the acquisition state. The Customer Report collapses the
  authoritative states into three neutral, mutually exclusive statements and adds
  no detail beyond them:
  - **Completed** (`completed_canonical`) — "A complete forensic image of the
    device was created."
  - **Not completed** (`inconsistent_artifacts`, `invalid_map`,
    `imaging_complete_fingerprint_missing`, `incomplete_ddrescue`) — "The forensic
    image of the device was not completed."
  - **Not performed** (`no_acquisition`) — "No forensic image of the device was
    created."
- **Recovery-operation performance is NOT asserted.** HERMES must not infer that
  logical/file recovery was or was not performed from the presence or absence of
  recovered artifacts (correction 2). As documented in §6, no persisted,
  structured record proves a recovery operation executed. Phase 2's Work Performed
  section is therefore **limited to the imaging fact** and does not narrate the
  recovery operation.

**Excluded:** acquisition state codes, ddrescue map status, sector sizes, tool
names (ddrescue, PhotoRec), artifact/output paths, SHA-256 digest, and the audit
log.

### 4.5 Recovery Outcome (neutral definitions only)

Populated from `recovery_outcome` in `case.json` — an operator decision recorded
at finalization, which by its own definition is "never derived from recovered file
counts or statistics" (`Source/core/status.py`).

The value maps to a **neutral definition only** (correction 4). HERMES must not
infer reasons, percentages, quality judgements, or case-specific limitations:

| `recovery_outcome` | Neutral customer-facing meaning |
|---|---|
| `SUCCESSFUL` | The requested data was recovered successfully. |
| `PARTIAL` | Some of the requested data was recovered. |
| `UNSUCCESSFUL` | The requested data could not be recovered. |
| absent / `None` | No recovery outcome has been recorded. |

No additional interpretation is added.

### 4.6 Files Recovered (observational)

Populated from `summarize_recovered_artifacts(...)`:

| Report field | Source | Notes |
|---|---|---|
| Recovered Items | `recovered_file_count` | the owner API counts *every* file under `recovered/recup.*`, not only user files; the label reflects this |
| Recovered Data | `recovered_size_bytes` via `format_bytes` | natural units |

The label **"Recovered Items"** is used rather than "Recovered Files" because the
owner API (`_count_recovered_artifacts`) counts all observable artifacts under the
recovered output, which may include tool-generated files, not a guaranteed count
of user files. Presented strictly as an **observation of artifacts prepared for
delivery**, not as a claim about what operation produced them and not as a
statement about recovery completeness (consistent with correction 2).

**Excluded:** `recup_directories` output paths and evidence filenames.

### 4.7 Recommendations (HERMES policy content)

Generic post-delivery guidance. This is **versioned, HERMES-owned policy content —
not a fact sourced from `case.json`** (correction 7). It carries a policy version
identifier so a given report can state which recommendation text it used.

Ordering places verification first, then backup guidance: (1) verify the recovered
data opens correctly, (2) contact the laboratory if files are missing or
unreadable, (3) keep independent backups, (4) store recovered data on a different
device.

**No customer-specific recommendation field is added to the case model in this
phase** (correction 9). Case-specific customer recommendations, if ever desired,
are a separate future change requiring the product architect's approval.

### 4.8 Disclaimer (HERMES policy content)

Standard scope/limitation text. Like recommendations, this is **versioned,
HERMES-owned policy content**, presented (not authored) by HERMES, and not sourced
from `case.json`.

---

## 5. Information intentionally excluded (internal to Sentinel)

The Customer Report must never expose:

- **Device internals:** paths, serials, transport, filesystem, `size_bytes`,
  role, SMART data.
- **Destination media:** the entire `destination` block (Sentinel-provided, not
  customer-submitted).
- **Safety/strategy internals:** AEGIS risk, confidence, decision codes/reasons;
  ORACLE strategy internals.
- **Forensic/technical artifacts:** acquisition state codes, ddrescue map status,
  sector sizes, SHA-256 digest and fingerprint evidence, tool names,
  artifact/output paths.
- **Operational records:** the audit log/timeline, technician notes, internal
  workflow `status`, `created_at`, assigned technician.
- **Contact PII beyond identity:** phone and email (name only).
- **Intake previous recovery attempts.**

Enforcement is structural: the customer builder simply never reads these fields
into its section dict, so they cannot leak through the formatter. This maps
one-to-one to the "Technician Only" and "Internal Only" classes in
`REPORT_SCHEMA.md`.

---

## 6. Inspected code: authoritative proof of recovery work

Requirement (correction 3): identify the authoritative existing owner API or
recorded operation that proves recovery work occurred; if none exists, limit Work
Performed to provable facts.

Findings from the current code:

1. **`summarize_recovered_artifacts(recovery_path)` (`Source/modules/archive.py`)**
   counts files under `recovered/recup.*` on disk. It proves artifacts *exist*; it
   does **not** prove a recovery operation ran. Per correction 2 it must not be
   used to infer that recovery was or was not performed.
2. **`execute_photorec_recovery(session)` (`Source/modules/archive.py`)** returns
   an in-memory result dict. **This result is not persisted to `case.json`.** Its
   only durable trace is `audit.log` lines ("PhotoRec session started" / "…ended
   normally") written by ECHO.
3. **`read_audit_log(recovery_path)` (`Source/modules/echo.py`)** returns raw log
   lines and, by contract, "does not parse or interpret log entries." The audit
   log is classified **Internal Only** and is unsuitable for the Customer Report.
4. **`recovery_outcome` in `case.json`** (written by `manifest.py`, read by
   `read_case_manifest`, loaded by `case_loader.py`) is an operator decision
   recorded at finalization. It is the authoritative *recorded outcome* of the
   recovery.
5. **`classify_acquisition_state(recovery_path)` (`Source/modules/archive.py`)**
   authoritatively establishes whether a forensic image was created.

**Conclusion.** The only authoritative, persisted, customer-appropriate proofs
are: **imaging** (`classify_acquisition_state`) and the **recorded outcome**
(`recovery_outcome`). There is **no** persisted, structured record proving that a
recovery *operation* executed. Phase 2's Work Performed section is therefore
limited to the imaging fact (§4.4), and the recovery result is conveyed only
through the neutral `recovery_outcome` (§4.5) and the observational Files Recovered
summary (§4.6).

---

## 7. Inspected code: overwrite / refusal behavior

The Customer Report must match the Technician Report's behavior in
`save_technician_report()`:

- Ensure the reports directory exists: `mkdir(parents=True, exist_ok=True)`.
- If the target file already exists, **raise `FileExistsError`** and do not
  overwrite.

`save_customer_report()` applies the same refusal-on-existing behavior to
`reports/customer_report.md`. Reports are never silently overwritten, preserving
the append-only spirit of the case record.

---

## 8. Inspected code: localization behavior (and Phase 2 scope)

Current state of the codebase:

- **Report content is not localized.** In `Source/modules/hermes.py` and
  `Source/modules/report_formatter.py`, the report title ("Technician Report"),
  section headings (e.g. "Case Information"), field labels (e.g. "Case Number"),
  and placeholders (e.g. "Not recorded", "Not reported", "None recorded",
  "No audit events recorded", "Present but unreadable") are **hard-coded English
  strings**. `hermes.py` does not import the `i18n` layer.
- The only localized report-related strings are **CLI operator messages** around
  generation — `report.prompt.generate` and `report.label.saved_path` in
  `en.json` / `de.json` — which are printed by `Source/bin/sentinel`, not embedded
  in the report file.

**Phase 2 scope decision (correction 10).** The Customer Report follows the exact
same convention as the existing Technician Report: hard-coded English titles,
headings, labels, and placeholders, rendered by the unchanged `ReportFormatter`.
Phase 2 does **not** introduce report-content localization, because the Technician
Report is not localized either and no requirement proves it necessary now.

The recommendation and disclaimer policy content (§4.7–4.8) is defined as
**versioned** HERMES-owned content authored in English for Phase 2, structured so
that localized variants (e.g. DE) can be added later as a separate, approved
change **without** altering this report architecture. Report-content localization
(including these policy strings and the shared report labels) remains a future
enhancement applicable to both report types, out of Phase 2 scope.

---

## 9. Constitutional alignment

- **SL-004 (Explain Every Decision):** plain-language customer content; every
  case-derived statement traces to recorded data.
- **SL-006 (The Operator Decides):** generation requires explicit technician
  approval.
- **AP-002 (No Circular Dependencies):** HERMES reads the case; no module depends
  on HERMES.
- **AP-003 (Facts Are Immutable):** HERMES summarises; it never modifies facts.
- **AP-004 (One Responsibility Per Subsystem):** HERMES presents; ARCHIVE
  executes; ECHO records.
- **AP-006 (Workflow Before Features):** reporting integrates at Delivery.

---

## 10. Unresolved implementation blocker

**No authoritative, persisted, structured record proves that a recovery operation
was executed.** `execute_photorec_recovery` returns an ephemeral result that is
never written to `case.json`; the only durable trace is the Internal-Only,
unparsed `audit.log`. Consequences for Phase 2:

- Work Performed (§4.4) is limited to the provable imaging fact.
- The recovery result reaches the customer only via the neutral `recovery_outcome`
  (§4.5) and the observational Files Recovered summary (§4.6).

**Resolution requires a product-architect decision** (out of scope for this
design): whether to persist a structured recovery-operation record (e.g. a
`recovery_operation` block in `case.json` written at finalization). If added in a
future phase, the Customer Report's Work Performed section can be extended to
describe the recovery operation authoritatively, without otherwise changing this
architecture.

---

## Related Documents

- `REPORTING.md`
- `REPORT_SCHEMA.md`
- `RecoveryCase.md`
- `ImagingSafety.md`
- `ArchitecturePrinciples.md`
- `SentinelLaws.md`
