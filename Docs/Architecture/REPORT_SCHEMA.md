# Report Schema

Version: 0.1  
Status: Active

---

## Purpose

This document defines what information belongs in Sentinel reports.

It specifies the content schema for the Technician Report and the Customer Report. It does not define implementation, file formats, templates, or HERMES behaviour.

For reporting architecture, module ownership, and generation flow, see `REPORTING.md`.

Every field listed here must trace to data already recorded in the Recovery Case by the module that owns it. HERMES presents schema fields; it does not create them.

---

## Technician Report

The Technician Report is the complete operational record of a Recovery Case.

It is intended for recovery engineers who need to understand, review, or continue the case.

### Case information

Permanent case identity and workflow state.

Includes:

- Case number
- Creation date
- Current status
- Assigned technician
- Report generation date

Source: case records, SENTINEL.

---

### Customer information

Contact and identification details for the customer associated with the case.

Includes:

- Name
- Telephone
- Email
- Company (when recorded)

Source: case records.

---

### Intake summary

The customer's original request and context recorded at case creation.

Includes:

- Requested recovery
- Incident description
- Previous recovery attempts
- Data priority
- Additional observations from intake

Source: case records.

---

### Device identity

Technical identification of every storage device associated with the case.

Includes:

- Source device identity
- Destination device(s)
- Additional media
- Observations collected by ARGUS (including SMART where available)
- Device photographs (references)

Source: ARGUS, case records.

---

### Assessment results

The technical evaluation performed before preservation and recovery work.

Includes:

- Risk level
- Recovery decision
- Recommended precautions
- Confidence level
- Filesystem and media observations relevant to assessment

Source: AEGIS, ARGUS, case records.

---

### AEGIS safety decisions

Safety evaluations and permissions recorded during the case.

Includes:

- Source-device safety assessment
- Risk quantification
- Workflow permission or denial
- Explanation of each safety decision

Source: AEGIS.

---

### ORACLE strategy

The recommended and selected recovery approach for the case.

Includes:

- Recommended strategy
- Selected strategy
- Strategy evolution (where recorded)
- Rationale linked to assessment

Source: ORACLE, case records.

---

### Imaging details

Forensic acquisition work performed on the source device.

Includes:

- Imaging operation status and result
- Tool and method used
- Artifact paths (`source.img`, `source.map`, ddrescue log)
- Acquisition identity evidence
- Incomplete or resumed imaging history (where applicable)

Source: ARCHIVE, case records, evidence.

Imaging safety states are governed by `ImagingSafety.md`.

---

### Integrity verification

Evidence that the canonical forensic image was fingerprinted.

Includes:

- SHA-256 fingerprint record
- Fingerprint operation result
- Canonical acquisition completion status

Source: ARCHIVE, evidence.

Fingerprinting confirms image file integrity. It does not prove equivalence to the original source device.

---

### Recovery execution

Image-based recovery operations performed after preservation.

Includes:

- Recovery operation type and tool
- Operation status and result
- Output locations
- Execution success versus recovery outcome (as recorded)

Source: ARCHIVE, case records.

---

### Recovery statistics

Quantitative summary of recovery results.

Includes:

- File counts
- Data volume recovered
- Formats or categories recovered (where recorded)
- Operation-specific statistics returned by recovery tools

Source: ARCHIVE, case records.

---

### Audit timeline

Chronological record of significant technical and operational events.

Includes:

- Timeline entries from the Recovery Case
- Relevant audit-log events from ECHO
- State transitions
- Operation start and completion events

Source: case records, ECHO.

---

### Operator notes

Observations and remarks recorded by technicians during the case.

Includes:

- Investigation findings
- Physical observations
- Internal remarks
- Customer statements recorded as notes

Source: case records.

Notes describe observations. They do not replace the timeline or audit log.

---

### Final recommendations

Laboratory-facing guidance at case completion.

Includes:

- Recommended next steps for the technician or laboratory
- Unresolved items requiring follow-up
- Technical precautions for delivery or archival
- Internal handover notes (where recorded)

Source: case records, ORACLE, AEGIS (as already recorded).

Final recommendations may include detail beyond what appears in the Customer Report.

---

## Customer Report

The Customer Report is the delivery-facing summary of a Recovery Case.

It is intended for the customer who requested the recovery. Language must be plain and non-technical unless a technical term is unavoidable.

### Case information

Information the customer needs to identify their recovery.

Includes:

- Case number
- Customer name
- Report date

Source: case records.

Contact details and internal workflow status are omitted.

---

### Device received

A plain description of the storage device submitted for recovery.

Includes:

- Device type and capacity (where known)
- Identifying label or model (where appropriate for the customer)
- Number of devices received

Source: ARGUS, case records (summarised).

Technical paths, SMART attributes, and hex-level detail are omitted.

---

### Problem description

The customer's situation in plain language.

Includes:

- What happened to the data
- What the customer reported at intake
- What data was most important to recover

Source: case records (intake, summarised).

---

### Work performed (non-technical)

A plain-language account of what the laboratory did.

Includes:

- Whether the device was imaged before recovery
- Whether logical recovery or file recovery was performed
- Major steps taken, described without tool names or technical jargon where possible

Source: ARCHIVE, ORACLE, case records (summarised).

Detailed operation logs, artifact paths, and audit events are omitted.

---

### Recovery outcome

The result of recovery work from the customer's perspective.

Includes:

- Whether recovery was successful, partial, or unsuccessful
- What was and was not recovered
- Known limitations or unresolved items

Source: ARCHIVE, case records (summarised).

---

### Files recovered

What the customer receives.

Includes:

- Description of recovered data
- Approximate file count or data volume (where appropriate)
- Delivery medium or format (where recorded)

Source: ARCHIVE, case records.

Internal output paths and evidence filenames are omitted.

---

### Recommendations

Plain-language guidance for the customer after delivery.

Includes:

- How to handle recovered data
- Suggested backup practices
- Any follow-up the customer should consider

Source: case records (customer-facing subset of recommendations).

Internal laboratory guidance and technical handover notes are omitted.

---

### Disclaimer

Standard limitations and expectations for the customer.

Includes:

- Scope of the recovery service
- Limits of what recovery can guarantee
- Responsibility for verifying recovered data
- Retention or archival policy references (where applicable)

Source: agreed laboratory policy, presented by HERMES.

---

## Information Classification

Every report field belongs to one visibility class.

| Field | Classification |
|-------|----------------|
| Case information (Technician Report) | Technician Only |
| Customer information | Technician Only |
| Intake summary | Technician Only |
| Device identity | Technician Only |
| Assessment results | Technician Only |
| AEGIS safety decisions | Technician Only |
| ORACLE strategy | Technician Only |
| Imaging details | Technician Only |
| Integrity verification | Technician Only |
| Recovery execution | Technician Only |
| Recovery statistics | Technician Only |
| Audit timeline | Internal Only |
| Operator notes | Internal Only |
| Final recommendations | Technician Only |
| Case information (Customer Report) | Customer Visible |
| Device received | Customer Visible |
| Problem description | Customer Visible |
| Work performed (non-technical) | Customer Visible |
| Recovery outcome | Customer Visible |
| Files recovered | Customer Visible |
| Recommendations | Customer Visible |
| Disclaimer | Customer Visible |

### Classification definitions

**Customer Visible**

Information approved for inclusion in the Customer Report. It may be summarised from Technician Report source fields but must not expose internal or technician-only detail.

**Technician Only**

Information for the Technician Report. It supports professional recovery work but is not presented to the customer in full form.

**Internal Only**

Information restricted to laboratory operations. It never appears in the Customer Report. It supports audit, review, and continuity of work within the laboratory.

### Classification rules

1. A field classified as Customer Visible must not expose Internal Only content.
2. HERMES may summarise Technician Only source data when producing Customer Visible fields. It must not infer content not already recorded.
3. Internal Only fields remain available in the Technician Report for authorised laboratory review.
4. If the same concept appears in both reports (for example, case identity or recommendations), each report field is classified independently according to its audience.

---

## Future Report Types

A **Partner Report** is reserved for future outsourced recoveries.

Examples include work sent to specialist partner laboratories for firmware repair, PC-3000, cleanroom, or RAID recovery.

The Partner Report will contain only the information required by the receiving partner laboratory. It will draw from the same Recovery Case facts as existing reports but apply a partner-specific field selection.

It intentionally excludes DigiRettung internal notes and customer-facing explanations. It is a technical handover document between laboratories, not an internal audit record or a customer delivery summary.

Partner Report rules:

- Partner involvement is false by default.
- A case may be marked as involving a partner at any point after assessment if escalation becomes necessary.
- Sentinel must generate a Partner Report only when partner involvement is recorded for that case.
- Partner Report generation remains an explicit operator action, not an automatic action.
- The full Partner Report schema is not defined in this document.

---

## Related Documents

- `REPORTING.md` — Reporting architecture, HERMES responsibility, and generation flow
- `RecoveryCase.md` — Recovery Case components and permanent record rules
- `00_System_Architecture.md` — Module responsibilities and information ownership
- `ImagingSafety.md` — Imaging and integrity verification rules
- `RecoveryOperationStandard.md` — Operation results and statistics
