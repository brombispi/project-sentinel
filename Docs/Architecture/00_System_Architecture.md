# Project Sentinel
## System Architecture

Version: 0.5.0

---

# Philosophy

Project Sentinel is not a single recovery application.

It is a collection of specialized components that cooperate while remaining
independent.

Each component has exactly one responsibility.

No component should perform another component's role.

Sentinel is centered on the Recovery Case.

The Recovery Case is the single source of truth for every recovery.
Modules do not pass control through a fixed linear pipeline.
They read from the Recovery Case, perform their role, and write results back.

---

# Core Architecture

Sentinel is state-driven.

A Recovery Case progresses through defined states as work advances.
SENTINEL orchestrates the workflow.
Approved operations move the case between states.
Each state change and significant event is recorded.

```
                      Technician
                           │
                           ▼
                     ┌──────────┐
                     │ SENTINEL │  workflow orchestration
                     └────┬─────┘
                          │
                          ▼
                   Recovery Case
                   (state-driven)
                          │
    ┌─────────────────────┼─────────────────────┐
    │                     │                     │
  ARGUS               AEGIS                ORACLE
Observation          Decision             Decision
    │                     │                     │
    └─────────────────────┼─────────────────────┘
                          │
         ┌────────────────┼────────────────┐
         │                │                │
      CODEX             JANUS          case records
    Knowledge          Decision
         │                │
         └────────────────┘
                          │
                          ▼
                      ARCHIVE
              Operation / Evidence
                          │
         ┌────────────────┼────────────────┐
         │                │                │
     Imaging         Integrity        Recovery
         │                │                │
         └────────────────┼────────────────┘
                          │
                          ▼
                        ECHO
                    (audit trail)
                          │
                          ▼
                       HERMES
                   Reporting (future)
```

---

# Responsibility Categories

Sentinel separates six kinds of responsibility.
Each module owns one category or contributes within a clearly defined boundary.

## Observation

What exists on the system and what can be measured about it.

Owned by **ARGUS**.

ARGUS discovers storage devices and collects technical observations such as
SMART reports.

ARGUS records facts. It does not decide or execute.

---

## Decision

Whether work may proceed and what approach should be taken.

Owned by **AEGIS**, **ORACLE**, and **JANUS**.

- AEGIS evaluates source-device safety.
- ORACLE recommends recovery strategy.
- JANUS validates destination selection.

SENTINEL presents decisions to the technician and obtains explicit approval
before any operation begins.

---

## Knowledge

Verified technical knowledge that supports recovery work.

Owned by **CODEX**.

CODEX stores structured recovery knowledge organised by category.
Any subsystem may consult CODEX.

CODEX never observes, evaluates, decides, or executes.

SENTINEL presents explanatory output to the technician using information
provided by ORACLE and CODEX.
SAGE is not part of the runtime execution pipeline.

---

## Operation

Approved tasks performed on behalf of the Recovery Case.

Owned by **ARCHIVE**.

ARCHIVE executes only operations that Sentinel has already approved.
ARCHIVE does not select devices, assess safety, or ask the technician for
decisions.

---

## Evidence

Artifacts collected and permanently stored with the Recovery Case.

Primarily owned by **ARCHIVE**, with supporting collection by **ARGUS**.

Examples include forensic images, ddrescue map files, SHA-256 fingerprint
records, recovery outputs, SMART reports, and other collected artifacts.

Evidence is stored in the Recovery Case workspace.
ECHO records when evidence is created or changed.

---

## Reporting

Customer-facing and operational reports derived from the Recovery Case.

Owned by **HERMES** (future).

HERMES will read completed case data and produce reports and exports.
HERMES does not execute recovery operations.

---

# Information Flow

1. SENTINEL opens or resumes a Recovery Case.
2. Observation modules identify devices and collect technical facts.
3. Decision modules assess safety, recommend strategy, and validate destinations.
4. SENTINEL updates case state and presents the next approved step to the
   technician.
5. The technician approves each operation explicitly.
6. ARCHIVE executes the approved operation and returns results.
7. ARCHIVE collects operation artifacts as Evidence in the Recovery Case.
8. ECHO records every significant event in the audit trail.
9. HERMES will produce reports from the completed case (future).

Modules communicate through the Recovery Case, not directly with one another.

---

# Module Responsibilities

## SENTINEL

Question:
"What happens next?"

Orchestrates the complete recovery workflow.
Manages Recovery Case state transitions.
Presents decisions, knowledge, and operation proposals to the technician.
Delegates execution to the appropriate subsystem.

---

## ARGUS

Question:
"What exists?"

Discovers and identifies storage devices.
Collects observations such as SMART reports.

---

## AEGIS

Question:
"Is it safe?"

Evaluates source-device risk.
Produces safety assessments and workflow permission.

---

## ORACLE

Question:
"What should be done?"

Builds the recommended recovery strategy from the assessment.

---

## CODEX

Question:
"What is known?"

Maintains structured recovery knowledge.
Consulted by any subsystem that needs verified technical context.

---

## JANUS

Question:
"Is the destination safe?"

Validates destination selection.
Prevents unsafe recovery operations.

---

## ARCHIVE

Question:
"How do we preserve and recover?"

Creates recovery case workspaces.
Relocates cases to approved Recovery Storage.
Executes forensic imaging (see `ImagingSafety.md`).
Records image integrity fingerprints.
Executes approved image-based recovery operations.
Collects and stores operation artifacts as Evidence.

---

## ECHO

Question:
"What happened?"

Records technical events.
Maintains the append-only audit trail.

---

## HERMES

Question:
"How do we communicate?"

Produces reports and documentation from the Recovery Case.

Not yet implemented.

---

# Design Principle

Every module answers exactly one question.

If a module begins answering multiple questions,
its responsibilities should be reconsidered.

The Recovery Case coordinates the modules.
No module should duplicate another module's role.
