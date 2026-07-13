# Backlog

- Human-friendly timestamps without microseconds.
- Atomic writes for session_registry.json.
- Auto-create session_registry.json if missing.
- Configurable Recoveries path.
- Recovery timeline with phase durations.
- Professional terminal formatting.
- Color coding for SAFE / WARNING / CRITICAL.
- Offline-first licensing idea.
- Trusted CODEX contribution workflow.
## User Guidance

- Guided diagnostic interview.
- Cross-reference technician observations with ARGUS findings.
- Confidence score for recommendations.
- Explain every disabled action.

## CODEX

- Community contribution system.
- Reviewed knowledge approval workflow.
- Known device behaviour database.
- Confidence levels for knowledge entries.

## UI

- Standard Mode.
- Expert Mode.

## Diagnostic Workflow

- Guided diagnostic interview with cross-reference between technician observations and ARGUS findings.

## Deployment

- Prevent deployment from overwriting runtime state files.
- Exclude runtime data (state, logs, recoveries) from deployment.

## Architecture

- Recovery Problem classification.
  - Intended reasoning model: Facts → Recovery Problem → Recovery Strategy → Recovery Recommendation → Recovery Operation.
  - ORACLE owns Recovery Problem classification.
  - Recovery Problem describes the data-loss situation, not the tool or workflow.
  - Implementation deliberately deferred until TestDisk or another real consumer requires differentiated recommendations.
  - No code, schema, classes, or case.json fields yet.

## Recovery Operations

- TestDisk integration.
  - Filesystem-aware recovery from a disposable working copy; preserve filenames and directory structure when possible.
  - Canonical image: images/source.img (fingerprinted; never passed to TestDisk).
  - Working copy: working/testdisk.img (only image TestDisk may open).
  - Recovered files: recovered/testdisk/.
  - TestDisk log: evidence/testdisk.log.
  - Working copy created before each approved run (replace after confirmation if one exists).
  - Copy requirements: safe working copy, atomic completion, canonical source.img never modified.
  - Copy mechanism (reflink, sparse copy, standard copy, etc.) is an implementation detail; validate on real Sentinel hardware before fixing as policy.
  - Launch: cwd = evidence/; command testdisk /log <case>/working/testdisk.img (TestDisk 7.1; no /logname).
  - Copy destination: recovered/testdisk/ (technician guidance; not subprocess cwd).
  - ORACLE recommends TestDisk first when installed (LOW confidence); PhotoRec remains fallback.

