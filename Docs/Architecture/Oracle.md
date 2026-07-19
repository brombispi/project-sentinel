Project Sentinel

ORACLE — Constitutional Specification

Version: 1.0
Status: Active
Applies to: All ORACLE work, present and future

⸻

Preamble

This document is the authoritative design reference for ORACLE.

It is an architecture specification only. It defines what ORACLE is, what it
owns, what it must never do, and how it complies with the Sentinel Laws and the
Architecture Principles.

It is intentionally independent of any programming language, data format,
library or runtime detail. Where this document conflicts with an
implementation, the implementation must change.

If a future feature cannot be reconciled with this specification, the feature
must be reconsidered before the specification is amended.

⸻

# 1. Purpose

ORACLE answers exactly one question:

"What is the constitutionally recommended next action?"

ORACLE is:

- deterministic — identical inputs always produce an identical recommendation;
- explainable — every recommendation carries the reasoning that produced it;
- stateless — ORACLE holds no memory between recommendations;
- read-only — ORACLE observes facts and produces interpretation, nothing more.

ORACLE never decides.

ORACLE never executes.

ORACLE interprets facts; it never creates them.

ORACLE recommends, and the operator decides.

⸻

# 2. Responsibilities

ORACLE owns the interpretation of assessed facts into constitutionally sound
guidance. Its responsibilities are:

- Recovery strategy recommendations — proposing the recommended overall
  approach to a recovery given the assessed condition of the source.
- Recovery method recommendations — proposing the recommended technique or
  sequence of techniques that best serves the recommended strategy.
- Workflow recommendations — proposing the next constitutionally consistent
  step in the recovery workflow.
- Abstaining — declining to recommend when the available facts are
  insufficient, contradictory, or would require ORACLE to guess.

Abstaining is a first-class responsibility, not a failure. When facts do not
support a recommendation, the correct recommendation is to recommend nothing
and to say why.

ORACLE never owns:

- observation of devices or hardware;
- safety evaluation of the source device;
- validation of the destination;
- the decision to proceed;
- the execution of any operation;
- the creation, modification or deletion of facts;
- reporting to the operator or the customer;
- the audit trail.

Those responsibilities belong to other subsystems. ORACLE consumes their
facts and returns interpretation.

⸻

# 3. Constitutional Principles

ORACLE is bound by the Sentinel Laws and the Architecture Principles. This
section states how compliance is achieved.

## Sentinel Laws

- SL-002 — Protect Original Media.
  ORACLE shall always prefer recommendations that avoid modifying the original
  media when a safer alternative exists, favouring image-based recovery over
  operations against original media.

- SL-003 — Observe Before Acting.
  ORACLE shall recommend only on the basis of facts that have already been
  observed and assessed. ORACLE shall never recommend action on an unknown or
  unassessed device; in such cases it abstains.

- SL-004 — Explain Every Decision.
  Every recommendation ORACLE produces shall carry a clear, operator-readable
  explanation. ORACLE shall never produce an unexplained recommendation.

- SL-005 — Quantify Risk.
  Every recommendation shall carry an estimated risk level and, where the facts
  support it, a confidence level. Where confidence cannot be established, ORACLE
  states so plainly rather than implying certainty.

- SL-006 — The Operator Decides.
  ORACLE provides guidance only. It never makes the final decision and never
  assumes operator authority. Where uncertainty exceeds confidence, ORACLE
  recommends rather than decides, and may recommend abstention.

- SL-007 — Preserve Workflow.
  Every recommendation shall be consistent with all prior observations and
  decisions. ORACLE shall never recommend an action that contradicts an earlier
  safety assessment or the established workflow sequence.

## Architecture Principles

- AP-001 — Every Decision Must Be Explainable.
  ORACLE's output is structured to carry the decision, the reason, the
  supporting evidence, the governing law, the assessed risk, and the
  recommended next step. This is the native shape of an ORACLE recommendation.

- AP-002 — No Circular Dependencies.
  ORACLE consumes facts in one direction only. It never asks another subsystem
  for permission in order to produce a recommendation, and no subsystem depends
  on ORACLE for permission to produce the facts ORACLE reads.

- AP-003 — Facts Are Immutable.
  ORACLE reads facts and never modifies them. A recommendation is an
  interpretation, represented as a separate object from the facts that produced
  it. Facts describe reality; recommendations describe interpretation; the two
  remain independent.

- AP-004 — One Responsibility Per Subsystem.
  ORACLE has exactly one responsibility: to recommend. It does not observe,
  evaluate safety, execute, report, or record. Any drift toward a second
  responsibility is a violation of this specification.

- AP-006 — Workflow Before Features.
  ORACLE integrates into the existing recovery workflow rather than bypassing
  it. A recommendation is meaningful only at its correct point in the workflow
  and only when it advances the operator's next constitutional step.

⸻

# 4. Inputs

ORACLE consumes authoritative facts produced by other subsystems. It never
originates a fact. Each input has an owning subsystem, and ORACLE may read it
only because that subsystem has already established it as fact.

## Authoritative inputs

- Device observations.
  - Owner: ARGUS.
  - Ownership reason: ARGUS is the observation subsystem; it alone establishes
    what exists and what can be measured.
  - ORACLE may consume: yes, as read-only fact.

- Source safety assessment.
  - Owner: AEGIS.
  - Ownership reason: AEGIS is the decision subsystem for source-device safety;
    it alone establishes whether work on the source may proceed.
  - ORACLE may consume: yes, as read-only fact.

- Recovery Case state and prior decisions.
  - Owner: SENTINEL (via the Recovery Case).
  - Ownership reason: SENTINEL orchestrates the workflow and owns the current
    state and the record of prior approved decisions.
  - ORACLE may consume: yes, as read-only fact, and only as presented within the
    RecommendationContext.

- Verified technical knowledge.
  - Owner: CODEX.
  - Ownership reason: CODEX is the knowledge subsystem; it maintains verified
    recovery knowledge that any subsystem may consult.
  - ORACLE may consume: yes, as read-only reference.

ORACLE consumes these inputs only as assembled into the RecommendationContext
(Section 5). ORACLE does not reach into subsystems directly.

## Forbidden inputs

ORACLE shall never consume the following, regardless of availability:

- audit.log — the audit trail is ECHO's record of what happened; it is not an
  input to interpretation and must not influence a recommendation.
- Hardware — ORACLE never observes devices directly; observation belongs to
  ARGUS.
- HERMES — reporting output is a downstream product and must never feed back
  into a recommendation.
- Recovery artifacts — forensic images, map files, fingerprints, recovery
  outputs and other evidence are operational artifacts owned by ARCHIVE; ORACLE
  reasons over assessed facts, not over raw artifacts.
- Free-text inference — ORACLE shall never derive recommendations from
  unstructured notes, prose, or any source that has not been established as a
  structured fact by an owning subsystem.

⸻

# 5. Recommendation Context

The RecommendationContext is the single, complete, read-only set of facts that
ORACLE requires to produce a recommendation.

SENTINEL constructs the RecommendationContext. SENTINEL owns the workflow and
the Recovery Case, and is therefore the only subsystem positioned to assemble
the authoritative facts into one coherent input.

ORACLE never constructs the RecommendationContext. ORACLE receives it, reasons
over it, and returns a recommendation.

The RecommendationContext is immutable for the lifetime of a recommendation
evaluation. Once ORACLE begins reasoning over it, the facts it contains do not
change. This preserves deterministic behaviour — the same context always yields
the same recommendation — and prevents ORACLE from observing changing state
mid-evaluation, which would make a recommendation depend on timing rather than
on fact.

This division preserves subsystem boundaries:

- It keeps ORACLE stateless — ORACLE holds nothing and gathers nothing; every
  fact it needs arrives with the request.
- It keeps ORACLE free of circular dependencies (AP-002) — ORACLE never queries
  other subsystems to build its own input.
- It keeps facts immutable (AP-003) — ORACLE reads a prepared, read-only view
  and cannot alter the facts within it.
- It keeps responsibilities singular (AP-004) — assembly is orchestration and
  belongs to SENTINEL; interpretation belongs to ORACLE.

If a recommendation would require a fact not present in the RecommendationContext,
ORACLE abstains. It does not seek the missing fact itself.

⸻

# 6. Recommendation Object

A Recommendation is the immutable object ORACLE returns. It is described here
conceptually; implementation syntax is intentionally out of scope.

A Recommendation carries the following fields:

- recommendation — the recommended next action, strategy or method, stated
  clearly enough for the operator to act upon or reject.
- reason — the constitutional and technical reasoning that produced the
  recommendation, expressed for operator understanding (SL-004).
- evidence — the specific facts within the RecommendationContext that support
  the recommendation, so the operator can trace interpretation back to fact.
- governing law — the Sentinel Law or Laws that most directly govern the
  recommendation, making the constitutional basis explicit.
- risk — the estimated level of risk associated with acting on the
  recommendation (SL-005).
- confidence — the level of confidence in the recommendation given the
  available facts, or an explicit statement that confidence cannot be
  established (SL-005, SL-006).
- next step — the concrete next step ORACLE recommends within the workflow
  (AP-001, SL-007).

A Recommendation is interpretation, not fact. It describes what ORACLE believes
should be done given the facts; it does not describe reality and does not become
a fact by being produced. It is immutable once returned and is never treated as
an authoritative record.

Where the facts do not support a recommendation, ORACLE returns an explicit
abstention. An abstention is itself a valid Recommendation: it states that no
action is recommended and carries the reason and the missing facts that led to
it.

⸻

# 7. Workflow

ORACLE occupies a single, fixed position in the recovery workflow. It is
invoked after the facts have been assembled and before the operator decides.

```
ARGUS
  observes devices and collects facts
        │
        ▼
AEGIS
  assesses source-device safety
        │
        ▼
SENTINEL
  assembles the RecommendationContext
        │
        ▼
ORACLE
  produces a Recommendation
        │
        ▼
Operator
  decides
        │
        ▼
ARCHIVE
  executes the approved operation
        │
        ▼
HERMES
  reports
        │
        ▼
ECHO
  records
```

ORACLE sits between fact assembly and the operator's decision. It never
precedes observation or assessment, and it never follows the decision into
execution, reporting or recording. Its influence begins and ends with the
Recommendation it returns.

⸻

# 8. Persistence Policy

Recommendations are not authoritative facts.

A Recommendation is an interpretation of facts at a moment in time. It carries
no authority of its own and must never be treated as a record of what is true or
of what happened.

Recommendations are recomputable.

Because ORACLE is deterministic and stateless, the same RecommendationContext
always yields the same Recommendation. A Recommendation therefore does not need
to be stored to be recovered; it can always be reproduced from its facts.

Recommendations must never overwrite facts.

A Recommendation shall never modify, replace or annotate the facts that
produced it, nor any other fact owned by another subsystem (AP-003).

Optional future persistence must be separate and immutable.

If recommendations are ever stored — for example, to record what ORACLE advised
at a given point — that store shall be a separate, append-only, immutable record
that is clearly distinguished from facts. It shall never be an input to a future
recommendation (Section 4).

⸻

# 9. Professional Principles

ORACLE is held to the professional standards of the recovery discipline it
serves.

- Reproducibility — any recommendation can be reproduced from its facts by any
  party, at any later time.
- Determinism — identical inputs produce identical outputs, without exception.
- Explainability — every recommendation carries reasoning an operator can
  understand and challenge.
- Operator authority — ORACLE advises; the operator decides. ORACLE never
  assumes, implies or erodes that authority.
- No probabilistic behaviour — ORACLE does not sample, randomise or vary its
  output. Confidence is a stated assessment, not a probability that alters the
  result.
- No AI — ORACLE contains no model-based, learned or generative reasoning. Its
  logic is explicit and inspectable.
- No hidden reasoning — every factor that influences a recommendation is
  present in the recommendation. ORACLE keeps no private state and applies no
  undisclosed rule.

⸻

# 10. Future Evolution

The following are recognised as possible future directions. Each is future work
and none is authorised by this document.

- Filesystem-aware recommendations — reasoning that incorporates
  filesystem-level facts, once such facts are established by an owning subsystem.
  (Future work.)
- Improved strategy generation — richer strategy and method reasoning over the
  same authoritative facts. (Future work.)
- Recommendation history — an optional, separate, immutable record of past
  recommendations, subject to Section 8. (Future work.)
- Rule-set versioning — explicit versioning of ORACLE's reasoning rules so that
  a historical recommendation can be understood against the rules in force when
  it was produced. (Future work.)

Any such enhancement must preserve determinism, statelessness, explainability
and the subsystem boundaries defined in this specification.

⸻

# 11. Non-Goals

ORACLE will never do the following. These are permanent boundaries, not current
limitations.

- Observe hardware — observation belongs to ARGUS.
- Execute recovery — execution belongs to ARCHIVE.
- Modify the manifest, the Recovery Case, or any fact — facts are immutable and
  owned by their subsystems.
- Override the operator — the operator always decides.
- Infer missing facts — where facts are insufficient, ORACLE abstains rather
  than guesses.
- Generate reports — reporting belongs to HERMES.
- Record events — the audit trail belongs to ECHO.
- Hold state between recommendations — ORACLE is stateless.
- Assemble its own input — the RecommendationContext is constructed by SENTINEL.

⸻

Closing

ORACLE exists to convert assessed facts into constitutionally sound guidance,
and to do so transparently enough that the operator can trust it, challenge it,
and remain fully in command of every decision.

ORACLE recommends. The operator decides. Nothing about ORACLE may erode that
boundary.
