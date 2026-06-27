Project Sentinel

Architecture Principles

Version: 0.1
Status: Active

⸻

Purpose

The Architecture Principles define how Project Sentinel is engineered internally.

While the Sentinel Laws protect customer data and guide operator safety, the Architecture Principles protect the integrity, maintainability and scalability of the software itself.

Every subsystem should comply with these principles.

If software implementation conflicts with an Architecture Principle, the implementation should be reconsidered before proceeding.

⸻

AP-001

Every Decision Must Be Explainable

A subsystem shall never approve or deny an operation without providing:

* A clear decision.
* The reason for the decision.
* The evidence supporting the decision.
* The applicable Sentinel Law (when relevant).
* The assessed level of risk.
* The recommended next step.

Project Sentinel should never produce unexplained decisions.

⸻

AP-002

No Circular Dependencies

Subsystems shall exchange information in a single direction.

No subsystem may depend on another subsystem for permission in order to obtain the information required to make that permission decision.

If a circular dependency is discovered, the architecture must be redesigned rather than patched.

⸻

AP-003

Facts Are Immutable

Subsystems own the information they create.

Other subsystems may read those facts but shall not modify them.

Interpretations, recommendations and decisions must be represented as separate objects.

Facts describe reality.

Decisions describe interpretation.

The two shall remain independent.

⸻

AP-004

One Responsibility Per Subsystem

Each subsystem shall have one clearly defined responsibility.

Examples:

* ARGUS observes.
* AEGIS evaluates.
* ORACLE recommends.
* ARCHIVE performs operations.
* HERMES generates reports.
* ECHO records events.

When a subsystem begins performing multiple responsibilities, it should be divided into smaller, focused components.


AP-005

The Repository Is the Source of Truth

The Project Sentinel repository defines the official state of the project.

Running systems are deployments.

Backups are archives.

Conversations are guidance.

Only the repository represents the current implementation of Project Sentinel.