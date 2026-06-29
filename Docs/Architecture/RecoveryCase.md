# Recovery Case

A Recovery Case is the permanent record of a customer's data recovery.

A Recovery Case begins when a customer requests assistance.

A Recovery Case can be reopened at any time.

A Recovery Case ends only when it is archived.

Every important action performed during the recovery becomes part of the Recovery Case.

Nothing is permanently removed from the Recovery Case history.

---

## Purpose

The Recovery Case exists to preserve the full context of the recovery.

It answers:

- Who requested the recovery?
- What happened?
- Which devices were involved?
- What decisions were made?
- What actions were performed?
- What evidence was collected?
- What data was recovered?
- What was delivered?
- What remains unresolved?

---

## Components

Every Recovery Case is composed of the following components.

### Identity

Identifies the Recovery Case.

Examples:

- Case Number
- Creation Date
- Current Status
- Assigned Technician

---

### Customer

Information required to identify and contact the customer.

Examples:

- Name
- Telephone
- Email
- Company (optional)

---

### Intake

Records the customer's request.

Examples:

- Requested recovery
- Incident description
- Previous recovery attempts
- Data priority
- Additional observations

---

### Devices

Records every storage device associated with the case.

Examples:

- Source device
- Destination device(s)
- Additional media
- Device photographs

---

### Assessment

Contains the technical evaluation performed before recovery.

Examples:

- Risk level
- Recovery decision
- Recommended precautions
- Confidence level

---

### Strategy

Describes the recovery approach selected for the case.

Examples:

- Imaging first
- Logical recovery
- File carving
- Filesystem repair
- Hybrid recovery

The strategy may evolve as new evidence becomes available.

---

### Timeline

Records the chronological history of the Recovery Case.

The timeline is append-only.

Each entry records:

- Time
- Action
- Technician
- Result

The timeline answers:

"What happened during this recovery?"

---

### Notes

Notes contain observations made by technicians.

Notes may include:

- Customer statements
- Physical observations
- Recovery recommendations
- Investigation findings
- Internal remarks

Notes describe observations.

They do not replace the timeline.

---

### Evidence

Evidence contains every artifact collected during the recovery.

Examples:

- Device photographs
- SMART reports
- Disk images
- Screenshots
- Log files
- Hex analysis

Evidence remains permanently associated with the Recovery Case.

---

## Recovery Case States

Every Recovery Case progresses through a defined sequence of states.

### NEW

The case has been created.

No technical work has been performed.

---

### INTAKE

Customer information and recovery request are being recorded.

---

### ASSESSING

The source device is being evaluated.

The recovery risk and recommended strategy are determined.

---

### PREPARING

The recovery environment is being prepared.

Examples:

- Destination selected
- Safety validation
- Evidence collection
- Recovery plan confirmed

---

### RECOVERING

Recovery operations are in progress.

Examples:

- Imaging
- Logical recovery
- File carving

---

### VERIFYING

Recovered data is being reviewed and validated.

---

### READY_FOR_DELIVERY

The recovery has been completed.

Reports and recovered files are ready.

---

### DELIVERED

Recovered data has been delivered to the customer.

---

### ON_HOLD

The case is temporarily paused.

Examples:

- Waiting for customer response
- Waiting for replacement hardware
- Waiting for approval
- Waiting for additional information

---

### CLOSED

The case has been completed.

No further work is expected.

---

### ARCHIVED

The Recovery Case has been permanently archived.

It remains available for future reference.

---

## Recovery Case Rules

Every Recovery Case follows a number of fundamental rules.

### Rule 1

A Recovery Case has exactly one unique Case Number.

The Case Number never changes.

---

### Rule 2

Every Recovery Case has exactly one current state.

The current state always reflects the latest stage of the recovery.

---

### Rule 3

Every significant action creates a timeline entry.

Timeline entries are never deleted.

---

### Rule 4

Evidence is never removed from a Recovery Case.

New evidence may be added at any time.

---

### Rule 5

Every important decision must be explainable.

Sentinel should always record why a recommendation or decision was made.

---

### Rule 6

Recovery Cases are append-only.

Historical information is preserved.

Corrections create new records rather than replacing history.

---

### Rule 7

A Recovery Case can be reopened after completion.

Additional work continues the existing Recovery Case.

A new Recovery Case is created only for a new recovery request.

---

## Case Number

Every Recovery Case receives a unique Case Number.

The Case Number is the permanent identifier of the case.

It is used in:

- Case folder names
- Reports
- Timeline entries
- Audit logs
- Customer communication
- Internal references

The Case Number must never be reused.

The Case Number must never change after creation.

---

## Recovery Case Philosophy

A Recovery Case is the single source of truth for every recovery.

Every module reads from the Recovery Case.

Every module writes back to the Recovery Case.

Modules communicate through the Recovery Case rather than directly with one another.

The Recovery Case represents the complete history of the recovery.

A Recovery Case should remain understandable years after it was created.

A technician who did not originally perform the recovery should be able to understand the complete case by reviewing:

- Identity
- Intake
- Devices
- Assessment
- Strategy
- Timeline
- Notes
- Evidence
- Reports
- Deliverables

No additional knowledge should be required.

Sentinel should preserve not only the recovered data, but also the knowledge that led to the recovery.