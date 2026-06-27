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

---

# Core Architecture

                Operator
                    │
                    ▼
                +---------+
                | ARGUS   |
                +---------+
               Observation
                    │
                    ▼
                +---------+
                | AEGIS   |
                +---------+
            Safety Assessment
                    │
                    ▼
                +---------+
                | ORACLE  |
                +---------+
          Recovery Strategy
                    │
          ┌─────────┴─────────┐
          ▼                   ▼
     +---------+         +---------+
     | SAGE    |         | ECHO    |
     +---------+         +---------+
   Knowledge            Audit Log
          │
          ▼
     +---------+
     | ARCHIVE |
     +---------+
   Imaging / Clone
          │
          ▼
     +---------+
     | HERMES  |
     +---------+
 Reports / Export

---

# Shared Component

CODEX

Shared Structured Knowledge Repository.

CODEX stores verified recovery knowledge.

Knowledge is organised by category and may be consulted by any subsystem.

CODEX is not part of the execution pipeline.

CODEX never observes.

CODEX never evaluates.

CODEX never decides.

CODEX never executes.

CODEX answers one question:

"What is known?"

---

# Module Responsibilities

ARGUS

Question:
"What exists?"

Produces observations.

---

AEGIS

Question:
"Is it safe?"

Produces safety assessments.

---

ORACLE

Question:
"What should be done?"

Produces recovery strategies.

---

SAGE

Question:
"Why?"

Explains concepts and recommendations.

---

ARCHIVE

Question:
"How do we preserve?"

Performs imaging and cloning.

---

HERMES

Question:
"How do we communicate?"

Produces reports and documentation.

---

ECHO

Question:
"What happened?"

Maintains audit logs.

---

CODEX

Question:
"What is known?"

Maintains structured engineering knowledge.

---

# Design Principle

Every module answers exactly one question.

If a module begins answering multiple questions,
its responsibilities should be reconsidered.
