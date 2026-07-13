# ARCHIVE

## Mission

ARCHIVE is Project Sentinel's preservation subsystem.

Its mission is to safely execute approved recovery operations while minimizing risk to the customer's original media.

ARCHIVE never decides what should be done.

ARCHIVE performs only operations that have already been approved by Sentinel's workflow.

Its first implementation is forensic imaging.

---

## Responsibilities

ARCHIVE is responsible for:

- Creating and maintaining recovery case workspaces.
- Resolving standard artifact paths.
- Preparing approved preservation operations.
- Executing approved preservation operations.
- Returning operation results to Sentinel.

ARCHIVE is not responsible for:

- Selecting the source device.
- Selecting the destination device.
- Assessing safety.
- Recommending strategy.
- Asking the technician for decisions.
- Generating reports.

---

## Position in the Recovery Workflow

ARCHIVE operates only after the assessment workflow has successfully completed.

Recovery workflow:

ARGUS
→ observes

AEGIS
→ evaluates safety

ORACLE
→ recommends the recovery strategy

JANUS
→ validates the recovery destination

ARCHIVE
→ executes the approved preservation operation

ECHO
→ records everything that happened

HERMES
→ reports the completed recovery

---

## Inputs and Outputs

### Inputs

ARCHIVE receives:

- An approved recovery operation from Sentinel.
- The selected source device.
- The approved destination device.
- The recovery session.
- The operation parameters generated automatically by Sentinel.

ARCHIVE assumes that:

- ARGUS has already identified the devices.
- AEGIS has already approved the source.
- ORACLE has already selected the recovery strategy.
- JANUS has already approved the destination.
- The operator has already confirmed execution.

### Outputs

ARCHIVE returns:

- Operation result (success or failure).
- Operation status.
- Generated recovery artifacts.
- Relevant execution information for ECHO and HERMES.

---

## Supported Operations

ARCHIVE executes preservation operations.

In ARCHIVE v1, the only supported operation is:

### Create Forensic Image

Purpose:

Create a sector-by-sector image of the original storage device without modifying the source media.

Expected artifacts:

- source.img
- source.map
- ddrescue.log

Acquisition identity evidence:

- evidence/acquisition_source.json

Imaging safety:

Acquisition states, resumable ddrescue imaging, mounted-descendant safety, source identity validation, and canonical image immutability are governed by `ImagingSafety.md`.

Execution principles:

- The original device is never written to.
- Recovery artifacts are stored automatically inside the current recovery case.
- The technician never enters filenames or output paths manually.
- Every operation requires explicit confirmation before execution.
- Every operation is recorded by ECHO.

---

## Operating Principles

ARCHIVE follows these principles during every preservation operation:

- Execute only operations approved by Sentinel.
- Preserve the customer's original media whenever possible.
- Prefer simple, predictable workflows over operator flexibility.
- Use sensible defaults to reduce human error.
- Never require the technician to manually construct file paths or filenames.
- Make every operation understandable before execution.
- Execute one operation at a time.
- Return clear success or failure information to Sentinel.
- Never silently change or skip an approved operation.