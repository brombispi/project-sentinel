# Engineering Planning — Post HERMES Phase 2

Status: Planning (analysis only)
Author role: Cursor (implementation assistant)
Scope: Roadmap and architecture review after HERMES Phase 2

This document is a planning artifact only. It proposes no implementation and
authorises no change. It is grounded in the current repository state
(`Source/`, `Tests/`, `Docs/`) and the Sentinel Constitution, not in speculative
future ideas.

---

## 1. Current project status after HERMES Phase 2

### What exists and works end-to-end

The core recovery workflow is implemented and orchestrated by the CLI
(`Source/bin/sentinel`) through the Recovery Case state machine
(`core/status.py`, `RecoveryCase.md`):

- **Case lifecycle**: create, discover active/archived, open, resume, reopen,
  archive. Persistent case numbers and workspace creation.
- **Observation / Decision**: ARGUS (device discovery, SMART), AEGIS (source
  safety), ORACLE (strategy, recovery-method recommendation), JANUS (destination
  validation), CODEX (filesystem knowledge).
- **Operation / Evidence**: ARCHIVE performs forensic imaging (ddrescue),
  SHA-256 fingerprinting, acquisition-state classification, and PhotoRec
  recovery; case relocation to approved storage.
- **Audit**: ECHO append-only audit log, fail-safe, integrated across the
  workflow.
- **Finalization**: operator-selected `recovery_outcome` and `completed_at`
  timestamp persisted in `case.json`.

### HERMES specifically (the Phase 2 milestone)

- **Technician Report** — fully implemented, wired into the delivery workflow
  (`_run_delivery_workflow` → `save_technician_report()`), and tested.
- **Customer Report** — implemented **and tested at the module level**
  (`build_customer_report`, `build_customer_markdown`, `save_customer_report`,
  `build_report("customer")`), with a full design document
  (`Docs/Architecture/CustomerReport.md`). It reuses the Technician Report
  pattern and the existing `ReportFormatter`; it introduces no new module or
  data model.
- **Partner Report** — declared but a stub (`NotImplementedError`), reserved for
  future outsourced recoveries.

### The most important status finding

**The Customer Report is built and tested but is not reachable by the operator.**
The CLI delivery workflow only generates the Technician Report. Nothing in
`Source/bin/sentinel` calls `save_customer_report()` / `build_report("customer")`.

Per AP-006 (*Workflow Before Features*) and Engineering Value 14 (*documentation
must describe the implemented system*), a feature is complete only when it fits
into the operator's workflow. HERMES Phase 2 is therefore **code-complete but
workflow-incomplete**: the capability exists in the module and in the tests, but
the recovery engineer cannot produce a Customer Report from a real case today.

Net status: the platform can take a case from intake through imaging, recovery,
outcome recording, and a Technician Report. The Customer Report — the customer-
facing half of roadmap v0.5 — is one small integration step away from being
usable.

---

## 2. Next 5 milestones (dependency order)

1. **M1 — Surface the Customer Report in the delivery workflow**
2. **M2 — Persist a structured recovery-operation record**
3. **M3 — Report-content localization (DE/EN)**
4. **M4 — PDF export of existing report content**
5. **M5 — CODEX knowledge expansion (SMART attributes + storage risk rules)**

These follow the roadmap's own v0.5 → v0.6 progression and depend only on what
already exists in the codebase.

---

## 3. Milestone detail

### M1 — Surface the Customer Report in the delivery workflow

- **Objective**: Let the operator generate the already-built Customer Report at
  the Delivery phase, alongside the Technician Report, with explicit approval
  (SL-006) and the same refuse-on-overwrite behaviour.
- **Why here**: It completes HERMES Phase 2 end-to-end and closes the "Customer-
  friendly report" line of roadmap v0.5. The builder, tests, and design doc
  already exist; only the CLI wiring in `_run_delivery_workflow` and two i18n
  prompt strings are missing. It unblocks every later reporting milestone (a
  report the operator cannot produce cannot be localized, exported, or improved).
- **Complexity**: **Low.** One CLI function extended, existing owner APIs reused,
  no new module or data model.
- **Technical risk**: **Low.** HERMES is read-only; the failure mode
  (`FileExistsError`) is already handled for the Technician Report and mirrored
  for the Customer Report. Main care point: keeping generation optional and
  approval-gated, and not implying recovery-operation facts the case does not
  hold (see M2).
- **User value**: **High.** Delivers the customer-facing deliverable that is the
  entire point of v0.5, with no new engineering surface.

### M2 — Persist a structured recovery-operation record

- **Objective**: Record an authoritative, structured recovery-operation entry
  (e.g. a `recovery_operation` block in `case.json`) at finalization, so reports
  can state what recovery work was performed rather than inferring it.
- **Why here**: This is the one **documented blocker** in the codebase
  (`CustomerReport.md` §10). Today `execute_photorec_recovery` returns an
  ephemeral dict; the only durable trace is the Internal-Only, unparsed
  `audit.log`. As a result the Customer Report's "Work Performed" is limited to
  the imaging fact, and the Technician Report cannot authoritatively narrate the
  recovery operation. Fixing this deepens both reports produced in M1.
- **Complexity**: **Medium.** Small persisted schema addition plus a write at the
  existing finalization point; must respect AP-003 (facts immutable, owned by the
  producing module — ARCHIVE/SENTINEL, never HERMES).
- **Technical risk**: **Medium.** Touches `case.json` schema and business logic.
  Requires a **product-architect decision** on what counts as an authoritative
  recovery-operation fact and how corrections are represented (append-only,
  Rule 6). Must not disturb existing case-loading or the resume/reopen paths.
- **User value**: **Medium–High.** Makes both reports honestly describe the
  recovery, improving customer trust and long-term case auditability.

### M3 — Report-content localization (DE/EN)

- **Objective**: Localize report titles, headings, labels, placeholders, and the
  versioned HERMES policy content (recommendations, disclaimer) for both report
  types, reusing the existing i18n layer.
- **Why here**: The CLI is already bilingual (`en.json` / `de.json`), but report
  **content** is hard-coded English (`CustomerReport.md` §8). For a German
  laboratory (DigiRettung), handing a German customer an English report is a
  concrete trust/UX gap. It belongs after M1 (the customer report must be
  reachable) and benefits from M2's finalized data being in place.
- **Complexity**: **Medium.** Mechanical but broad: introduce report strings into
  the i18n layer and thread the language through `Hermes`/`ReportFormatter`
  without changing report structure. The Phase 2 policy content is already
  designed as *versioned* to allow localized variants.
- **Technical risk**: **Low–Medium.** Risk of drift between EN/DE wording and of
  altering the proven formatter. Mitigate by keeping structure identical and only
  substituting strings.
- **User value**: **High** for German customer delivery; neutral for internal
  technician use.

### M4 — PDF export of existing report content

- **Objective**: Render the existing Technician/Customer Report content to a
  printable PDF, stored as a case artifact on explicit approval.
- **Why here**: Roadmap v0.5 ("PDF export") and v1.0 ("Printable reports").
  `REPORTING.md` explicitly scopes PDF as a *presentation layer over the same
  report content*, not a new data model. It must come after content is stable and
  localized (M1, M3) to avoid exporting the wrong or English-only text.
- **Complexity**: **Medium.** First rendering dependency for the project; keep it
  thin and driven by the same section data.
- **Technical risk**: **Medium.** New third-party dependency (conflicts with the
  "avoid unnecessary dependencies" value — choose a minimal, well-maintained
  library and validate on real Sentinel hardware, including the Raspberry Pi
  target). No fabricated facts: PDF must render only already-derived content.
- **User value**: **Medium–High.** Professional, deliverable-quality output for
  customers and archival.

### M5 — CODEX knowledge expansion (SMART attributes + storage risk rules)

- **Objective**: Extend CODEX with SMART-attribute knowledge, USB/storage risk
  rules, additional filesystem knowledge, and the first real recovery-case
  knowledge entries (roadmap v0.6).
- **Why here**: It improves decision quality (AEGIS/ORACLE) and explanations
  (SL-004/SL-005) once the reporting pipeline is complete, so richer knowledge is
  both surfaced to the operator and captured in reports. It depends on nothing
  structural and is safely deferrable until reporting is done.
- **Complexity**: **Low–Medium.** Mostly structured data plus lookups through the
  existing CODEX interface; volume rather than architectural difficulty.
- **Technical risk**: **Low.** Additive and read-only with respect to the
  workflow; main risk is knowledge quality/accuracy, not code stability.
- **User value**: **Medium.** Better recommendations and clearer explanations;
  compounding value as real cases accumulate.

---

## 4. Architectural gaps to address before adding more features

1. **Feature-to-workflow gap (highest priority).** The Customer Report exists in
   the module and tests but is not wired into the operator workflow. Under AP-006
   this means the Phase 2 milestone is not actually complete. Close this
   (M1) before starting new reporting features.

2. **No authoritative recovery-operation record (documented blocker).** There is
   no persisted, structured proof that a recovery operation ran; the only durable
   trace is the Internal-Only `audit.log`. This constrains honest reporting and
   should be resolved (M2) before enriching either report's "Work Performed".

3. **Reports are monolingual while the product is bilingual.** Report content is
   hard-coded English despite full EN/DE CLI localization. For customer-facing
   output this is a trust gap, not merely cosmetic (M3).

4. **Documentation vs implementation divergence to verify (low priority).**
   `Architecture.md` / `RecoveryCase.md` describe a human-readable **Timeline**
   and **Notes** as first-class case components distinct from `audit.log`, but the
   implemented technician report sources its "Audit Timeline" from ECHO's audit
   log and there is no separate timeline/notes store. This should be reconciled
   (either implement the distinction or update the docs) but does not block M1–M2.
   Recommend confirming with the product architect rather than assuming intent.

5. **First rendering/output dependency not yet introduced.** PDF export (M4) will
   be the project's first document-rendering dependency. Decide the dependency
   policy and Raspberry Pi compatibility deliberately, consistent with the
   "avoid unnecessary dependencies / test on real hardware" values.

None of gaps 3–5 should be started before gaps 1–2 are addressed.

---

## 5. Recommended next milestone

**Recommendation: M1 — Surface the Customer Report in the delivery workflow.**

Justification:

- **It finishes what Phase 2 started.** The Customer Report builder, its tests,
  and its design document already exist. The only missing piece is CLI wiring and
  two operator prompts. Leaving it unreachable means the platform advertises a
  capability the operator cannot use — a direct AP-006 (*Workflow Before
  Features*) violation.
- **Lowest complexity, lowest risk, highest immediate value.** HERMES is
  read-only; the overwrite/approval patterns are already proven by the Technician
  Report. There is no schema change and no new dependency.
- **It is the dependency root for everything else in reporting.** Localization
  (M3) and PDF export (M4) all operate on a report the operator can actually
  generate. Delivering M1 first makes the later milestones meaningful.
- **It respects the Constitution and the smallest-change principle.** It extends
  one existing function, modifies the fewest files, preserves the existing
  recovery workflow, and adds no abstractions.

M2 (recovery-operation record) is the correct *second* step, but because it
touches `case.json` schema and business logic it requires a product-architect
decision first; M1 needs no such decision and can proceed immediately upon
approval.

---

## 6. Blockers discovered

- **One documented blocker exists**: there is no authoritative, persisted,
  structured record proving a recovery operation executed (`CustomerReport.md`
  §10). It is a **product-architect decision**, not a coding obstacle, and it
  **does not block the recommended next milestone (M1)** — the Customer Report is
  usable today with imaging-limited "Work Performed". It becomes relevant at M2.
- **No other blockers** were found for M1. The customer-report code path is
  present, tested, and consistent with its design document; wiring it in is
  unobstructed.

---

## 7. Confirmation: no code or tests modified

During this review I only **read** files and ran read-only Git inspection
(`git status`, `git log`, `git diff`). No source (`Source/`), no tests
(`Tests/`), and no existing documentation were modified. The working tree was
clean before and after this analysis. The sole new file is this planning
document (`Docs/Engineering/Planning_Post_HERMES_Phase2.md`), created as the
requested deliverable.
