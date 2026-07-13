Project Sentinel Development Workflow

Version: 0.1
Status: Active

Purpose

This document defines how Project Sentinel is developed.

Its purpose is to ensure that every contributor, whether human or AI, follows a consistent engineering process that preserves the project’s vision, quality and long-term maintainability.

⸻

Before Making Any Change

Every contributor shall first read and understand the following documents:

1. Vision.md
2. SentinelLaws.md
3. EngineeringValues.md
4. ArchitecturePrinciples.md

These documents form the Project Sentinel Constitution.

If implementation conflicts with the Constitution, the implementation must be changed.

⸻

Development Principles

* Preserve the existing workflow.
* Modify the smallest number of files necessary.
* Prefer simple solutions over clever ones.
* Do not redesign architecture unless explicitly requested.
* Keep modules focused on a single responsibility.
* Avoid introducing unnecessary dependencies.
* Protect existing working functionality.

⸻

Feature Development Process

Every new feature should follow this sequence:

1. Define the objective.
2. Agree on the workflow.
3. Implement the smallest functional version.
4. Compile the project.
5. Test the feature.
6. Review the implementation.
7. Commit only after the project is working.

Every development session should end with a working version of Sentinel.

⸻

AI Development Rules

AI assistants should:

* Explain proposed changes before implementing them.
* Preserve existing project structure.
* Avoid modifying unrelated files.
* Prefer extending existing modules over creating new ones.
* Never assume requirements that are not documented.
* Ask for clarification when business logic is ambiguous.
* Never replace engineering judgement.

⸻

Repository Policy

The Git repository is the single source of truth.

Conversations, notes and ideas provide guidance but do not override the repository.

Every accepted change should be committed in small, meaningful increments.

⸻

Engineering Goal

The objective is not to produce the largest amount of code.

The objective is to produce the safest, clearest and most maintainable recovery platform possible.

⸻

Roles:

* Architect (Raz): Defines the product vision, workflow and engineering decisions.
* Engineering Advisor (ChatGPT): Reviews architecture, challenges assumptions, preserves project consistency and helps design features.
* Implementation Assistant (Cursor): Implements approved changes, proposes improvements and operates within the constraints defined by the Constitution.

A feature is considered complete only when:

• Implementation is finished.
• The workflow behaves as intended.
• Real-world testing has been performed when applicable.
• Documentation reflects the implementation.
• No known critical defects remain.