# Recovery Operation

## Purpose

A Recovery Operation represents a single, well-defined task that Sentinel performs during a recovery case.

Every Recovery Operation has one objective, one execution path, and one outcome.

Recovery Operations allow Sentinel to separate workflow decisions from execution details.

The technician approves a Recovery Operation.

ARCHIVE executes it.

---

## Required Information

Every Recovery Operation must define:

- Operation type
- Source device
- Destination target
- Expected artifacts
- Tool or method used
- Risk level
- Required confirmation
- Current status
- Start time
- End time
- Result

A Recovery Operation must be understandable before execution and reviewable after execution.

---

## Operation Lifecycle

Every Recovery Operation follows the same lifecycle:

1. Planned
2. Approved
3. Running
4. Completed

If execution cannot begin or finish successfully, the operation enters:

- Failed
- Cancelled

Once a Recovery Operation has started, every state transition must be recorded by ECHO.

---

## Execution Rules

Every Recovery Operation must satisfy the following rules before execution:

- The operation must have a clearly defined objective.
- The source device must already be identified.
- The destination must already be approved.
- The operation must be understandable to the technician before execution.
- The technician must explicitly approve the operation.
- The operation must never silently modify its objective during execution.
- Every significant event must be recorded by ECHO.
- The operation must always return a final result to Sentinel.
- Forensic imaging operations must comply with `Docs/Architecture/ImagingSafety.md`.

If any prerequisite is not satisfied, the operation must not begin.

---

## Relationship to Sentinel

A Recovery Operation is created and managed by Sentinel.

Sentinel is responsible for:

- Determining when an operation may begin.
- Presenting the operation to the technician.
- Obtaining explicit operator approval.
- Delegating execution to the appropriate subsystem.

ARCHIVE is responsible for executing the approved operation.

ECHO records the operation throughout its lifecycle.

HERMES reports the completed operation.

Recovery Operations never make strategic decisions. They execute decisions that have already been made by Sentinel's workflow.