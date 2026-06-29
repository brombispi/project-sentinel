# Sentinel Architecture

## Purpose

Sentinel is laboratory software designed to support professional data recovery workflows.

Its purpose is to help technicians perform recoveries safely, consistently, and with complete documentation.

Sentinel is not a recovery engine. Instead, it orchestrates the entire recovery process while integrating existing recovery tools.

---

## Core Principles

1. Safety before speed.

2. Every important action is documented.

3. Every decision is explainable.

4. The safest workflow should also be the easiest workflow.

5. A technician should understand any recovery case, even months later.

6. Sentinel should teach while it works.

7. Real-world laboratory workflow drives development.

8. Sentinel records facts, not assumptions.

---

## Sentinel Philosophy

Sentinel manages recovery cases, not recovery tools.

The recovery engine is only one instrument in the laboratory.

Sentinel coordinates the complete lifecycle of a recovery case:

- Intake
- Assessment
- Planning
- Imaging
- Recovery
- Verification
- Reporting
- Archival

Every recovery case should remain understandable, reproducible, and auditable long after the recovery has been completed.

Sentinel exists to support the technician, never to replace the technician.

The technician always makes the final decision.

---

## Recovery Case Lifecycle

Every recovery case progresses through a defined lifecycle.

1. Case Created
   - A unique case number is assigned.
   - Customer and intake information are recorded.

2. Assessment
   - The source device is identified.
   - Risks are evaluated.
   - The recovery strategy is proposed.

3. Preparation
   - The destination is selected.
   - Safety checks are performed.
   - Evidence is collected when required.

4. Recovery
   - Imaging or logical recovery is performed.
   - Progress is continuously documented.

5. Verification
   - The recovered data is reviewed.
   - Integrity and completeness are evaluated.

6. Delivery
   - The recovery report is generated.
   - The recovered data is prepared for delivery.

7. Archival
   - The recovery case is finalized.
   - All documentation, evidence, reports and logs remain permanently associated with the case.

   ---

## Core Entities

Sentinel is built around a small number of core entities.

### Recovery Case

The Recovery Case is the permanent record of a customer's recovery.

It contains:

- Customer information
- Intake information
- Devices
- Assessment
- Strategy
- Evidence
- Timeline
- Reports
- Recovery results
- Notes

A Recovery Case may exist for years.

---

### Recovery Session

A Recovery Session represents an active technician working on a Recovery Case.

It stores runtime information only.

A Recovery Session begins when a case is opened and ends when Sentinel is closed.

The Recovery Case remains permanently stored.

---

### Technician

The technician performs every recovery.

Sentinel assists the technician by providing information, guidance and safety checks.

The technician always makes the final decision.

---

### Evidence

Evidence consists of every artifact collected during the recovery.

Examples include:

- Device photographs
- SMART reports
- Disk images
- Recovery logs
- Screenshots
- Hex analysis

---

## System Architecture

Sentinel is composed of independent modules.

Each module has one clearly defined responsibility.

Modules communicate through the Recovery Case.

No module should duplicate the responsibilities of another module.

### ARGUS

Discovers and identifies storage devices.

---

### AEGIS

Assesses the selected source device and evaluates recovery risks.

---

### JANUS

Validates destination selection and prevents unsafe recovery operations.

---

### ORACLE

Builds the recommended recovery strategy based on the assessment.

---

### CODEX

Provides contextual technical knowledge and recovery guidance.

---

### ARCHIVE

Creates and maintains the permanent recovery case structure.

---

### ECHO

Records technical events and maintains the audit trail.

---

### SENTINEL

Coordinates the complete workflow.

Sentinel does not perform recovery itself.

It orchestrates the laboratory.

---

## Case Records

Sentinel separates current state from history.

### case.json

`case.json` represents the current state of the Recovery Case.

It answers:

- What is this case?
- Who is the case contact?
- What device is being assessed?
- What is the current status?
- What is the latest assessment?
- What strategy has been recommended?

`case.json` may be updated as the case progresses.

---

### audit.log

`audit.log` records technical events produced by Sentinel.

It answers:

- What did Sentinel do?
- Which module produced the event?
- When did the event happen?
- What safety decisions were made?

The audit log is append-only.

---

### Timeline

The timeline records the human-readable history of the Recovery Case.

It answers:

- What happened in the case?
- Who performed the action?
- Why was the status changed?
- What artifacts were added?
- What decisions were made?

The timeline is designed for technicians reviewing a case later.

The audit log is technical.

The timeline is operational.

---

## Design Boundaries

Sentinel has clearly defined responsibilities.

### Sentinel IS

- A laboratory operating system for professional data recovery.
- A recovery case management system.
- A workflow orchestration platform.
- A knowledge and decision support system.
- A documentation and reporting system.
- A safety-focused recovery assistant.

### Sentinel IS NOT

- A data recovery engine.
- A filesystem repair utility.
- A disk imaging engine.
- A hex editor.
- A SMART analysis engine.
- A partition editor.

Sentinel integrates existing specialist tools whenever appropriate.

Its responsibility is to coordinate, document and guide their use.

