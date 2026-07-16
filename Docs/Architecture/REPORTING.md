# Reporting

Version: 0.1  
Status: Active

---

## Purpose

The reporting system communicates the outcome of a Recovery Case to the people who need it.

Sentinel already records facts throughout the recovery workflow. Reporting turns those facts into readable documents without changing them.

Reporting is owned by **HERMES** (future). HERMES reads the Recovery Case and produces reports and exports. HERMES does not observe devices, assess safety, execute operations, or modify case state.

The Recovery Case remains the single source of truth. Reports are derived artifacts, not authoritative records.

---

## Reporting Responsibility

HERMES is a presentation module only.

It gathers existing case information, formats that information, and generates reports. Its responsibility ends at readable output.

HERMES must:

- Gather existing case information from the Recovery Case.
- Format information for the intended audience.
- Generate Technician Reports, Customer Reports, and future export formats.

HERMES must never:

- Perform business logic.
- Make workflow decisions.
- Modify case state.
- Derive operational conclusions.

Every decision recorded in a report was already made elsewhere. ARGUS observes devices. AEGIS evaluates safety. ORACLE recommends strategy. JANUS validates destinations. ARCHIVE executes operations and records results. SENTINEL orchestrates workflow state. Each module owns the data and conclusions for its domain.

HERMES presents those decisions. It does not reassess risk, reinterpret strategy, judge operation outcomes, or advance the case. If a report states that imaging was safe, that statement comes from AEGIS. If it describes the recovery approach, that comes from ORACLE. HERMES selects the relevant recorded facts and renders them clearly.

---

## Design Goals

Reporting shall:

- Present a complete, accurate summary of what happened in the case.
- Separate facts from presentation. HERMES reads module-owned data; it does not reinterpret decisions.
- Produce explainable output. Every significant statement in a report should trace back to recorded case data.
- Support two distinct audiences: the technician and the customer.
- Preserve the append-only history of the Recovery Case. Report generation adds artifacts; it does not rewrite prior records.
- Integrate at the end of the workflow, after recovery work and verification are complete.
- Remain optional at the point of generation. The technician approves when a report is produced.

Reporting shall not:

- Execute recovery operations.
- Modify `case.json`, the timeline, evidence, or the audit log.
- Replace the Recovery Case as the permanent record.
- Duplicate responsibilities already owned by other modules.

---

## Technician Report and Customer Report

Sentinel produces two report types from the same Recovery Case. They share source data but serve different purposes.

### Technician Report

The Technician Report is the operational record for the laboratory.

It is intended for recovery engineers reviewing the case now or months later.

It includes:

- Case identity and current status
- Customer and intake context
- Device identification and observations
- Assessment, risk evaluation, and strategy
- Timeline of significant actions
- Operation outcomes and evidence references
- Verification results and deliverables
- Technical detail sufficient to reconstruct decisions

It may include internal notes and technical language appropriate for professional recovery work.

### Customer Report

The Customer Report is the delivery-facing summary.

It is intended for the customer who requested the recovery.

It includes:

- Case identity
- A plain-language description of the request and outcome
- What was attempted and what was recovered
- What was delivered
- Any limitations or unresolved items the customer should understand

It excludes:

- Internal technician notes
- Low-level technical artifacts not relevant to the customer
- Audit-log detail intended for engineering review

### Separation Rule

Both reports are generated from the same Recovery Case facts.

HERMES selects and formats content; it does not create new facts.

If a statement appears in either report, it must be traceable to data owned by a specific module or to technician-authored case records.

The Technician Report and Customer Report are independent outputs. Generating one does not require generating the other, though both may be produced in the same reporting step.

---

## Information Ownership

Reporting reads from the Recovery Case. Each fact in a report originates from the module that created it.

| Information | Owner | Report use |
|-------------|-------|------------|
| Case number, status, assigned technician | SENTINEL / case records | Both |
| Customer and intake details | Case records | Both (Customer Report: primary) |
| Device identification | ARGUS | Both |
| SMART and device observations | ARGUS | Technician Report (summary in Customer Report where relevant) |
| Safety assessment and risk level | AEGIS | Technician Report (plain-language summary in Customer Report) |
| Recommended and selected strategy | ORACLE | Both |
| Destination validation | JANUS | Technician Report |
| Forensic images, fingerprints, recovery outputs | ARCHIVE | Technician Report (outcome summary in Customer Report) |
| Operation results and artifact paths | ARCHIVE | Technician Report |
| Timeline entries | Case records | Technician Report (selected entries in Customer Report) |
| Technician notes | Case records | Technician Report only |
| Technical events | ECHO | Technician Report |
| Report files themselves | HERMES | Stored as case artifacts after generation |

**Rule:** HERMES aggregates and formats. It does not modify facts created by other modules.

This follows Architecture Principle AP-003: facts are immutable; reports are interpretations of presentation, not replacements for source records.

---

## Report Generation Flow

Reporting occurs after recovery work reaches a delivery-ready state.

```
Case work complete
        ↓
Verification reviewed
        ↓
SENTINEL offers reporting (Delivery phase)
        ↓
Technician approves report generation
        ↓
HERMES reads Recovery Case (read-only)
        ↓
HERMES assembles Technician Report and/or Customer Report
        ↓
Report files written to case workspace (reports/)
        ↓
ECHO records report generation
        ↓
Case proceeds to archival
```

### Preconditions

Before HERMES generates a report:

- The Recovery Case must contain the facts the report requires.
- Recovery operations intended for inclusion must have reached a final result.
- The technician must explicitly approve report generation.

If required data is missing, HERMES reports what is unavailable. It does not infer or fabricate missing facts.

### Outputs

Generated reports are stored with the Recovery Case, alongside other evidence.

They remain permanently associated with the case number and timeline.

---

## Why Reporting Belongs at the End of the Workflow

Reporting is step 8 (Delivery) in the Recovery Case lifecycle, before Archival.

This placement is deliberate.

1. **Completeness.** A report summarizes finished work. Generating it earlier would produce incomplete or misleading documentation.

2. **Immutability of facts.** Modules record facts as work progresses. HERMES reads the final set of facts once the technician confirms the case is ready for delivery.

3. **No operational side effects.** Reporting must not influence imaging, recovery, or safety decisions. Placing it after execution ensures HERMES remains read-only with respect to operational state.

4. **Verification first.** The customer and the laboratory both depend on verified outcomes. Reporting follows verification, not the reverse.

5. **Workflow consistency.** The safest workflow should also be the easiest workflow. Deferring reports until delivery prevents duplicate or superseded documents and keeps a single authoritative case history.

Session-level summaries (for example, terminal output at the end of a Recovery Session) may appear earlier. Those are operational feedback, not the formal case reports described here.

---

## Future PDF Generation

The initial HERMES implementation will produce structured report content suitable for terminal and file output.

PDF export is a planned presentation layer, not a separate reporting system.

Future PDF generation shall:

- Render the same Technician Report or Customer Report content already derived from the Recovery Case.
- Not introduce new facts or a parallel data model.
- Store exported PDFs as case artifacts when the technician approves export.

Printable, customer-ready PDFs are tracked on the project roadmap (`Docs/Roadmap.md`, v0.5 Reports).

---

## Future Partner Reports

Partner reports are a future extension of the same HERMES architecture.

Some recipients — partner laboratories, insurers, or legal reviewers — may require a defined subset of case information in a specific format.

Future partner reports shall:

- Read from the same Recovery Case as Technician and Customer reports.
- Apply audience-specific templates without altering source facts.
- Remain optional outputs, generated only when requested and approved.

Partner reports do not require new modules or a separate case record. They require additional HERMES templates and field selection rules agreed with the product architect.

---

## Guiding Engineering Principles

Reporting design complies with the Project Sentinel Constitution.

### Sentinel Laws

- **SL-004 (Explain Every Decision):** Reports must be understandable. Technical detail in the Technician Report and plain language in the Customer Report both satisfy this law for their audiences.
- **SL-006 (The Operator Decides):** Report generation requires technician approval. HERMES does not publish reports autonomously.

### Architecture Principles

- **AP-001 (Every Decision Must Be Explainable):** Report content traces to recorded evidence.
- **AP-002 (No Circular Dependencies):** HERMES reads the Recovery Case; other modules do not depend on HERMES to perform their work.
- **AP-003 (Facts Are Immutable):** HERMES formats facts; it does not modify them.
- **AP-004 (One Responsibility Per Subsystem):** HERMES generates reports. ARCHIVE executes operations. ECHO records events.
- **AP-006 (Workflow Before Features):** Reporting integrates at Delivery, after the established recovery workflow.

### Engineering Values

- Every recovery should be documented.
- Every recommendation should be explainable.
- Transparency and customer trust take precedence over speed of delivery.
- Documentation must describe the implemented system; this document defines the intended reporting architecture before HERMES is implemented.

---

## Related Documents

- `Architecture.md` — Recovery Case lifecycle and Delivery phase
- `00_System_Architecture.md` — HERMES position in the module architecture
- `RecoveryCase.md` — Case components and permanent record rules
- `RecoveryOperation.md` — Operation lifecycle and HERMES relationship
- `ARCHIVE.md` — Evidence and operation outputs consumed by reporting
- `ArchitecturePrinciples.md` — AP-003, AP-004, and module responsibilities
- `SentinelLaws.md` — SL-004 and SL-006
