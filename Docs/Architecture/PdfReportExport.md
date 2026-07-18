# HERMES PDF Report Export

Version: 0.1
Status: Design (analysis only)
Author role: Cursor (implementation assistant)
Scope: Export the existing localized HERMES Technician and Customer Reports as
printable PDF documents, reusing the report content HERMES already builds,
without changing report architecture, report content, section order,
`case.json`, or the existing Markdown output.

---

## 0. Nature of this document

This is a design document only. It authorises no implementation and modifies no
source, tests, dependency declarations, or existing documentation. It is
subordinate to and consistent with the Sentinel Constitution
(`Vision.md`, `SentinelLaws.md`, `EngineeringValues.md`,
`ArchitecturePrinciples.md`) and the reporting documents (`REPORTING.md`,
`REPORT_SCHEMA.md`, `CustomerReport.md`, `ReportLocalization.md`).

`REPORTING.md` §"Future PDF Generation" already scopes PDF as *a planned
presentation layer, not a separate reporting system*, that must "render the same
Technician Report or Customer Report content already derived from the Recovery
Case" and "not introduce new facts or a parallel data model". This design
implements exactly that.

---

## 1. Repository and environment findings (what was inspected)

### 1.1 Report pipeline as it exists today

- **`Source/modules/hermes.py`** — `Hermes(session, language)` resolves one
  report language (explicit → operator UI language → English) without mutating
  global state. For each report type it exposes three layers:
  - `build_technician_report()` / `build_customer_report()` return an **ordered
    dict** of `{localized section title: {localized field label: value}}`.
    Values are strings, ints, bools, `None`-coerced placeholders, or
    lists/tuples (recommendations, disclaimer, audit events, multiple output
    locations).
  - `build_technician_markdown()` / `build_customer_markdown()` call
    `ReportFormatter().format_markdown(title, report, section_order=...)`.
  - `save_technician_report()` / `save_customer_report()` write
    `reports/<stem>.<lang>.md`, creating `reports/` and **refusing to overwrite**
    an existing same-language file (`FileExistsError`).
  - Filename helpers `technician_report_filename(lang)` /
    `customer_report_filename(lang)` return `"<stem>.<lang>.md"`.
- **`Source/modules/report_formatter.py`** — `ReportFormatter.format_markdown`
  is a pure, stdlib-only structural renderer: `# title`, `## section`, and
  `label: value` (or bulleted lists for list/tuple values). It renders whatever
  localized strings it is given, in the given `section_order`. It holds no i18n
  and no business logic.
- **`Source/i18n/translator.py`** — `translate(key, language=None, **kwargs)`
  resolves an explicit language against `en`/`de` catalogs with fallback
  (requested → English → `[key]`) and never mutates the global `_language`.
  Report prose already lives under `report.*` keys in `en.json`/`de.json`
  (titles, sections, fields, values, placeholders, customer sentences,
  recommendations, disclaimer). German umlauts are already present in `de.json`.
- **`Source/bin/sentinel`** — the Delivery workflow
  (`_run_delivery_workflow` → `_offer_report_generation`) offers the Technician
  Report then the Customer Report. Each: prompts `y/N`, calls
  `_prompt_report_language()` (default = UI language, **no** global mutation),
  saves the Markdown, prints a localized "saved" line, and logs to ECHO.
  Language is chosen **independently per report**.

**Conclusion:** the structured report dict that HERMES builds is already the
single, localized, in-memory representation from which Markdown is rendered. PDF
export is a second renderer over that same dict.

### 1.2 Dependency and packaging reality

- **`pyproject.toml`** declares `requires-python = ">=3.12"` and **no
  dependencies**. There is no `requirements.txt`, `setup.py`, `setup.cfg`,
  `Pipfile`, or lockfile.
- Every import under `Source/` is **standard library** (`json`, `os`, `sys`,
  `pathlib`, `datetime`, `hashlib`, `shutil`, `subprocess`, `fcntl`,
  `dataclasses`, `enum`) or an internal module. The project has **zero
  third-party runtime dependencies today.** A PDF engine would be the project's
  **first** third-party dependency — this is a deliberate, architect-level
  decision under the Engineering Value "avoid unnecessary dependencies".

### 1.3 Runtime environment (MiniBerry / Linux)

- `Docs/Engineering/EngineeringManual.md`: development is on the Mac Mini;
  runtime is a **Raspberry Pi ("MiniBerry")** at `/home/MiniBerry/drs`
  (Debian-based Linux, ARM). Production code is never edited on the Pi.
- **`deploy.sh`** deploys via `rsync` of **`Source/` only** (excluding
  `__pycache__`, `state/`, `Recoveries/`). Critically, **there is no
  `pip install` / virtualenv / dependency-provisioning step.** Consequences:
  1. Any Python dependency must be installed on the Pi **out of band** (e.g.
     `pip`/piwheels or the distribution package), and an **offline install**
     path must exist for a lab that may not have internet.
  2. Any **bundled asset** (e.g. a font) must live **under `Source/`** to be
     deployed at all. A top-level `Assets/` folder (mentioned in the manual) is
     **not** rsynced by `deploy.sh`.
- The tool runs offline in a recovery lab: **no network at render time** is a
  hard requirement.

### 1.4 Test conventions

- `unittest` under `Tests/`, run against `Source/` on `sys.path`.
- Tests build cases in `tempfile.TemporaryDirectory()`, write a `case.json`
  fixture, construct `Hermes(session, language)`, and assert on the structured
  dict and/or the rendered string.
- `Tests/test_report_localization.py` already asserts: EN/DE headings, labels,
  values, placeholders; untranslated technical facts; **no global-language
  mutation**; and the four language-qualified filenames with **independent
  per-(report, language) overwrite protection**.
- CLI functions are extracted from `bin/sentinel` via `ast` and executed with a
  mocked namespace; i18n parity is asserted in `test_i18n.py`.
- Fonts/umlauts are already exercised at the data layer (e.g.
  `assertRegex(" ".join(guidance), r"[äöüÄÖÜ]")`).

---

## 2. Q1 — Artifact architecture

### 2.1 Decision

**PDF is generated directly from the structured report dictionary that HERMES
already builds** — the same `build_technician_report()` /
`build_customer_report()` output that feeds Markdown. Markdown is **not** an
intermediate for PDF, and **no new intermediate representation is introduced.**
The structured, localized section dict **is** the shared representation; Markdown
and PDF are two sibling renderers of it.

```
case.json + evidence (facts, owned by other modules)
        │  read-only, owner APIs
        ▼
Hermes.build_<type>_report(language)   →  ordered {section: {label: value}}   ← the shared representation
        ├─────────────► ReportFormatter.format_markdown(...)  →  reports/<stem>.<lang>.md   (unchanged)
        └─────────────► PdfReportFormatter.format_pdf(...)    →  reports/<stem>.<lang>.pdf   (new)
```

### 2.2 Why not the alternatives

- **Markdown → PDF (parse the .md).** Rejected. It re-parses text HERMES already
  produced, needs a Markdown parser, and *loses structure* the dict still holds
  (list-vs-scalar distinction, section order). It also couples PDF to Markdown's
  textual formatting. This duplicates/relitigates content logic rather than
  reusing it.
- **A new shared intermediate representation.** Rejected as unnecessary. The
  ordered section dict is already a clean, localized, source-agnostic IR. Adding
  another layer violates "avoid unnecessary abstractions".

This is the smallest design that avoids duplicating any report-content logic:
all field selection, localization, placeholder handling, list rendering, and
section ordering stay in HERMES; the PDF renderer only lays out what it is given.

### 2.3 Confirmations required by the brief

- **`case.json` remains the source of case facts.** PDF export reads nothing new;
  it consumes the dict HERMES already derives via owner APIs
  (`read_case_manifest`, `classify_acquisition_state`,
  `summarize_recovered_artifacts`, `read_smart_evidence`,
  `read_fingerprint_evidence`, `read_audit_log`).
- **HERMES remains read-only.** No new writes to `case.json`, timeline, or
  evidence. The only new write is the PDF file under `reports/`.
- **No translated or rendered report content is persisted in `case.json`.**
  Rendering happens at generation time; only the `.pdf` file is written, exactly
  as `.md` is today.
- **`ReportFormatter` responsibilities stay clear.** `format_markdown` is
  unchanged and keeps rendering Markdown. PDF layout is a **separate concern**
  (see §2.4), so the proven, zero-dependency Markdown path is never coupled to
  the PDF engine.

### 2.4 Where the PDF renderer lives (extend vs. new module)

Recommendation: a **new, focused module `Source/modules/pdf_report_formatter.py`**
exposing `PdfReportFormatter.format_pdf(title, report, *, section_order,
report_kind, generated_at, metadata)` and returning **`bytes`**.

Rationale, weighing Implementation-Standards ("prefer extending existing
modules") against "preserve existing working functionality":

- The PDF engine is a heavy, optional third-party dependency. Putting
  `format_pdf` on `ReportFormatter` would risk importing the engine on the
  Markdown path (or force careful lazy imports inside a formerly stdlib-only
  module). Isolating it guarantees that **a missing/broken PDF engine can never
  affect Markdown generation** (a Q7 requirement).
- HERMES gains parallel methods that mirror the existing trio exactly:
  `build_technician_pdf_bytes()` / `save_technician_pdf()` and the customer
  equivalents, plus `technician_report_pdf_filename(lang)` /
  `customer_report_pdf_filename(lang)`.

(If the architect prefers strict "extend", the same code can live as a
lazily-imported `ReportFormatter.format_pdf` method; the containment argument
above is why a sibling module is recommended. This is a minor, reversible
placement decision — noted in §11.)

---

## 3. Q2 — PDF generation engine evaluation

All candidates were assessed against the constraints that matter here:
pure-Python vs. system libraries, Raspberry Pi (ARM/Debian) install burden,
**offline** operation, Unicode/German umlaut support, font control, page-break
control, tables/headings/lists/paths/hashes/long values, deterministic output,
testing difficulty, and licensing.

| Criterion | **ReportLab (OSS toolkit)** | **fpdf2** | **WeasyPrint** | **MD→HTML→PDF CLI (pandoc/LaTeX, wkhtmltopdf)** |
|---|---|---|---|---|
| Pure Python vs system deps | Pure-Python core (optional C accel auto-falls-back); pip pulls Pillow | Pure Python; pip pulls Pillow/fonttools/defusedxml | Needs **system libs**: cairo, pango, gdk-pixbuf, harfbuzz, libffi | External **binaries** (LaTeX distro or wkhtmltopdf) — large |
| MiniBerry/Linux install burden | Low: piwheels/`pip` wheel; no apt system libs for text | Low: pip wheel | **High**: multiple apt packages on the Pi | **High/very high**: hundreds of MB (TeX) or an unmaintained binary |
| Offline operation | Yes (once installed) | Yes | Yes (once installed) | Yes, but huge footprint |
| Unicode / umlauts / ß / Swiss | Yes via embedded TTF (also Latin-1 standard fonts cover ä ö ü ß) | Yes via embedded TTF | Yes if fonts present | Yes |
| Font control | Explicit `registerFont(TTFont(...))`, full control | Explicit `add_font(...)`, full control | CSS `@font-face`; more indirection | Toolchain-dependent |
| Page-break / headers / footers / page numbers | **Strong**: Platypus flowables + `onPage` callbacks; `PageBreak`, `KeepTogether` | Basic: manual/`page_break()`, header/footer hooks | Strong (CSS `@page`, running elements) | Strong (CSS/LaTeX) |
| Tables / headings / lists | **Strong**: `Table`, `Paragraph`, list flowables | Adequate: tables in recent versions, cells/multi_cell | Strong (HTML/CSS) | Strong |
| Long paths/hashes (wrap/break) | Controllable (word/char wrap in `Paragraph`, monospace style) | Controllable | Controllable (CSS `word-break`) | Controllable |
| Deterministic / reproducible | **Built-in `invariant` mode** (fixed date + doc ID) → byte-stable output | Possible: pin `creation_date`/metadata manually | Sets metadata; needs pinning; larger surface | Hard to pin fully |
| Testing difficulty | Low: in-memory bytes, `%PDF-` signature, invariant mode for byte-equality | Low | Medium (system libs in CI/Pi) | High |
| License | **BSD-3-Clause** (permissive) | LGPL-3.0-or-later (fine for library use) | BSD-3-Clause | pandoc GPL / LaTeX mixed / wkhtmltopdf LGPL + unmaintained |

### 3.1 Recommended engine: **ReportLab (open-source toolkit)**

Rationale:

1. **No system libraries.** Its core is pure Python; it needs no cairo/pango, so
   it fits a minimal Raspberry Pi and the rsync-only deployment far better than
   WeasyPrint. (Transitive Pillow has ARM wheels via piwheels; we render
   text-only reports, so Pillow is not exercised — logos are out of scope.)
2. **Right layout primitives.** Platypus provides exactly what the brief's layout
   asks for — `Paragraph` (headings, wrapped multiline text, bulleted lists),
   `Table` (key/value grids), `PageBreak`/`KeepTogether` (page-break control),
   and `onPage` callbacks for headers, footers, page numbers, generation
   timestamp, and the confidentiality banner.
3. **Determinism is first-class.** ReportLab's **invariant mode** produces a
   fixed internal date and document ID, enabling byte-stable output for tests
   (see §8) — the single hardest part of testing PDFs. fpdf2 can be pinned
   manually but has no equivalent one-switch guarantee.
4. **Permissive BSD-3-Clause license** — clean for a commercial lab tool.
5. **Offline, mature, well-documented, ARM-friendly.**

**fpdf2** is a reasonable lighter-weight alternative (smaller, MIT-adjacent
mindshare, also pure-Python) and is the recommended fallback if ReportLab's
footprint or Pillow transitive is judged too heavy. **WeasyPrint** is rejected
for its system-library burden on the Pi; **CLI toolchains** (pandoc/LaTeX,
wkhtmltopdf) are rejected as heavy external binaries (and wkhtmltopdf is
effectively unmaintained), conflicting with offline/minimal-footprint goals.

**No dependency is added or installed by this design pass.**

---

## 4. Q3 — Font strategy

### 4.1 Requirements

Cover English, German umlauts (ä ö ü Ä Ö Ü), the Eszett (ß), Swiss spelling
(which simply uses "ss" for "ß" — no extra glyphs), common technical characters
(`/ \ : . _ - + = # @ [ ] ( )`), and long filesystem paths and SHA-256 hashes
rendered legibly and unambiguously.

### 4.2 Decision: **bundle an open, redistributable Unicode font under `Source/`**

Recommended family: **DejaVu Sans** (proportional, for prose) plus **DejaVu Sans
Mono** (monospaced, for paths, hashes, serials, byte sizes, and other technical
tokens). Rationale:

- **Deterministic, identical rendering** on the Mac dev machine and the Pi
  runtime, and reproducible in CI — independent of whatever fonts the host
  happens to have.
- **Covers all required glyphs** (Latin + Latin Extended incl. umlauts and ß)
  and has an excellent monospaced companion that disambiguates `0/O` and `1/l/I`
  in hashes and paths.
- **Freely redistributable license** (Bitstream Vera + DejaVu public-domain
  amendments), so it can be committed to the repository and deployed.
- **Embedded (subset) in the PDF**, so delivered documents render correctly on
  any customer's viewer, offline and long-term.

**Deployment note (from §1.3):** the font files must live **under `Source/`**
(e.g. `Source/assets/fonts/DejaVuSans.ttf`, `DejaVuSansMono.ttf`, plus the
`LICENSE`), because `deploy.sh` only rsyncs `Source/`. The renderer resolves the
font path relative to its own module location (like `translator._i18n_dir()`),
never from an absolute or host path.

### 4.3 Why not the other approaches

- **PDF standard-14 fonts only** (Helvetica/Times/Courier). *Technically viable*
  for EN + DE because WinAnsi/Latin-1 already includes umlauts and ß — this is a
  sound **fallback**. But glyphs are not embedded (rendering depends on the
  viewer's built-in fonts), giving less control over a professional deliverable
  and weaker long-term/archival guarantees. Kept only as a documented fallback,
  not the primary strategy.
- **Discover an approved system font.** Rejected. It "relies blindly on an
  unspecified host font", differs between Mac and Pi, breaks determinism, and can
  silently miss glyphs (boxes/tofu) in a customer document.

**Failure rule:** if the bundled font cannot be loaded, the renderer raises a
clear, typed error (§7) rather than silently substituting a random host font or
producing mis-rendered umlauts.

---

## 5. Q4 — Report layout

The layout is restrained and professional and **renders the existing content and
section order unchanged**. It does not add, remove, reorder, or reword any
section or field; it only lays out the dict HERMES already builds.

### 5.1 Shared page furniture (both reports)

| Element | Design |
|---|---|
| Page | A4, generous margins (~20 mm); single column |
| Header (running, every page) | Left: report title (`report.title.*`, localized). Right: case identifier (the case number already present in the report's Case Information). Thin rule below. |
| Footer (running, every page) | Left: generation timestamp. Center: confidentiality marking (see §5.3). Right: **"Page X of Y"** page numbering. |
| Title block (first page) | Large report title; beneath it the case number and the generation timestamp. The **internal case name is shown on the Technician PDF only** and never on the Customer PDF. |
| Section heading | `report.section.*` value as a styled heading (bold, slightly larger), with space before; `KeepTogether` with at least its first row to avoid orphan headings. |
| Key/value fields | Two-column `Table` (label / value). Labels in the prose font; scalar values in prose font; **technical values (paths, hashes, serials, byte counts, sector sizes, state codes) in the monospaced font**, wrapping/breaking so long tokens never overflow the margin. |
| Multiline text | Wrapped `Paragraph` in the value cell (intake/incident text, etc.). |
| List values (recommendations, disclaimer, output locations, audit events) | Rendered as a bulleted list of `Paragraph`s, mirroring Markdown's `- item` semantics. |
| Page breaks | Automatic via Platypus; `KeepTogether` on small units (a heading + its table). The long **Audit Timeline** flows naturally across pages. |

### 5.2 Per-report specifics (content/order unchanged)

- **Technician Report** — sections and order exactly as
  `build_technician_report()` yields them: Case Information, Customer
  Information, Intake Summary, Device Identity, Assessment Results, Imaging
  Details, Integrity Verification, Recovery Statistics, Audit Timeline. Audit
  Timeline entries are **raw ECHO lines** — rendered as-is in monospace, never
  translated, wrapping long lines.
- **Customer Report** — sections and order exactly as
  `build_customer_report()` yields them: Case Information, Device Received,
  Problem Description, Work Performed, Recovery Outcome, Files Recovered,
  Recommendations, Disclaimer. Recommendations and Disclaimer render as bulleted
  lists; the Policy Version renders as a key/value field, exactly as in Markdown.

### 5.3 Confidentiality / internal-only marking

- **Technician Report** footer (and/or a first-page banner) carries a localized
  **"Internal — Confidential"** marking, reflecting `REPORT_SCHEMA.md`'s
  "Technician Only" / "Internal Only" classification of technician-report
  content (Audit Timeline is Internal Only).
- **Customer Report** carries a neutral footer (no internal marking).
- The marking string is a **new localized `report.*` key** (EN + DE), consistent
  with the localization architecture (§6 of `ReportLocalization.md`). It is
  presentation, not a case fact.

### 5.4 Long paths and hashes

Paths (`images/source.img`, `/dev/sdb`), SHA-256 digests, serials, and byte
counts are rendered in the **monospaced** font with character-level break points
so an unusually long value wraps within the value cell instead of overflowing or
raising. These remain **untranslated data** (consistent with
`ReportLocalization.md` boundary 8).

---

## 6. Q5 — Filename and overwrite behavior

### 6.1 Exact filenames (language-qualified, matching the existing convention)

- `technician_report.en.pdf`
- `technician_report.de.pdf`
- `customer_report.en.pdf`
- `customer_report.de.pdf`

Produced by new helpers that mirror the Markdown ones (same stems
`TECHNICIAN_REPORT_FILENAME_STEM` / `CUSTOMER_REPORT_FILENAME_STEM`):

```python
def technician_report_pdf_filename(language):
    return f"{TECHNICIAN_REPORT_FILENAME_STEM}.{language}.pdf"

def customer_report_pdf_filename(language):
    return f"{CUSTOMER_REPORT_FILENAME_STEM}.{language}.pdf"
```

### 6.2 Overwrite refusal (per report, per language, per format)

`save_technician_pdf()` / `save_customer_pdf()` mirror the Markdown savers:
`reports/` is created if absent; if the **exact** target PDF already exists, they
raise `FileExistsError` and write nothing. Overwrite protection is thus
independent per **(report_type, language, format)** tuple:
`customer_report.de.pdf` never collides with `customer_report.en.pdf` or with
`customer_report.de.md`.

### 6.3 Coexistence and coupling with Markdown

- **Coexistence.** PDFs live alongside the Markdown files in the same
  `reports/` directory; all eight artifacts (4 MD + 4 PDF) can coexist for one
  case.
- **Independent, not coupled.** Markdown and PDF generation are **independent**
  operations. Generating a PDF neither requires nor produces a `.md`, and vice
  versa. Either format may exist without the other.
- **One-format-exists behavior.** Because each format has its own
  refuse-on-overwrite check on its own filename, an existing `.md` never blocks
  the `.pdf` (and never gets touched), and an existing `.pdf` never blocks the
  `.md`. Re-requesting the *same* format+language is still refused.

---

## 7. Q6 — Operator workflow (smallest safe change)

### 7.1 Decision

Keep the existing per-report structure and add **one** minimal step: a **format
selection** after the existing approval and language prompts. No new top-level
prompts, no duplicated approval, and **independent language selection per report
is preserved** exactly as today.

```
Delivery phase (existing)
  Generate Technician Report?  [y/N]                (existing prompt, unchanged)
        │ y
  Select report language  [1] English  [2] Deutsch  (existing, default = UI language; no global mutation)
        │
  Select format  [1] Markdown  [2] PDF  [3] Both     (NEW — one small prompt)
        │
  save_technician_report()  and/or  save_technician_pdf()   → reports/…  (refuse-on-overwrite per file)
  ── same three steps repeated independently for the Customer Report ──
```

### 7.2 Why this shape

- **Avoids excessive prompting.** Exactly one added selection per report. The
  "Both" option lets an operator produce MD + PDF in one pass without a second
  round.
- **Preserves the workflow.** `_offer_report_generation` already takes a
  `save_report(language)` callable; the change is to pass a callable that
  dispatches on the chosen format (MD, PDF, or both) and to add the format prompt
  helper (e.g. `_prompt_report_format()`), reusing the existing prompt/loop
  pattern and localized strings.
- **Independent per report.** Technician and Customer keep separate language
  *and* format choices, so a German customer PDF + an English technician
  Markdown (or any mix) remains possible.
- **Default.** Product decision (§11): default to **Markdown** (exactly today's
  behavior, lowest surprise) or to **Both**. Recommended default: **Markdown**,
  preserving current behavior for operators who just press through; PDF is opt-in.

ECHO logs the chosen format(s), language, and path per generated artifact, as it
already does for Markdown.

---

## 8. Q7 — Failure behavior

The governing invariant: **a PDF failure must never alter case state and never
damage or delete an existing Markdown report.** HERMES stays read-only w.r.t.
`case.json`; the PDF path only ever writes its own `.pdf` file, and only after a
complete successful render.

| Failure | Behavior |
|---|---|
| **Missing PDF dependency** (engine not installed) | The renderer/save method raises a clear, typed error (e.g. a `PdfExportUnavailableError`) on a lazy import failure. The CLI catches it, prints a localized "PDF export unavailable — <engine> is not installed" message, logs a warning via ECHO, and continues. `case.json` untouched; Markdown path entirely unaffected (isolated module, §2.4). |
| **Font unavailable** (bundled font missing/unreadable) | Same clear failure and message; **no** silent fallback to a random host font (which could drop umlaut glyphs). The PDF is not written. |
| **Rendering error** (unexpected engine exception) | Caught at the save boundary; because the document is built into an **in-memory buffer** and only written to disk after a fully successful render, **no partial/corrupt `.pdf` is ever left**. Existing `.md` untouched. |
| **Unwritable report directory** (permissions, read-only mount) | `mkdir`/write raises `OSError`, surfaced with a clear message exactly like the Markdown saver; case state untouched. |
| **Existing output file** | `FileExistsError` refuse-on-overwrite per (report, language, format), identical to Markdown (§6.2). |
| **Malformed / unusually long values** | All values are treated as text; `None` is already coerced to placeholders by HERMES; lists render as bullets. Long unbroken tokens (paths/hashes) wrap via monospaced character-break styling and never raise. No value content can crash the render. |

Render-then-write contract: build `bytes` fully → check target does not exist →
write once. This makes every failure mode above leave the filesystem and case
state exactly as they were.

---

## 9. Q8 — Testing strategy

New file: `Tests/test_pdf_report_export.py`, following the existing conventions
(`unittest`, tempdir case, `case.json` fixture, `Hermes(session, language)`).
Tests avoid fragile pixel/layout assertions and split **deterministic content**
from **inherently variable PDF metadata**.

**Structural / filesystem tests (byte-level, robust):**

1. **EN and DE PDFs for both reports** — `save_technician_pdf()` /
   `save_customer_pdf()` in `en` and `de` each produce a file that exists.
2. **Filenames** — `technician_report_pdf_filename("en") == "technician_report.en.pdf"`,
   and the four saved files are named exactly
   `technician_report.en.pdf`, `technician_report.de.pdf`,
   `customer_report.en.pdf`, `customer_report.de.pdf` (mirrors the existing
   filename test).
3. **Overwrite protection** — re-saving the same (report, language, format)
   raises `FileExistsError`; a different language/format still succeeds; a
   pre-existing `.md` does **not** block the `.pdf` and is not modified, and
   vice versa.
4. **Valid PDF signature** — output starts with `b"%PDF-"` and ends with
   `%%EOF` (tolerant, not pixel-perfect).
5. **Page count where meaningful** — parse the `/Type /Page` count (or the
   engine's page count) and assert `>= 1`; with a large audit-timeline fixture,
   assert multi-page (`> 1`). Assertions are `>=`/`>`, never an exact layout.

**Content tests (data layer — deterministic, not fragile):**

6. **Representative translated text & umlauts** — assert on the **structured
   dict fed to the renderer** (already localized): the DE report contains
   `"Fallinformationen"`, umlaut-bearing recommendation/disclaimer strings
   (`r"[äöüÄÖÜ]"`), etc., and that rendering DE **does not raise** (a glyph-
   coverage smoke test with the bundled font). This tests the same content that
   reaches the PDF without depending on brittle text extraction from compressed
   PDF streams.
7. **Long paths/hashes** — a fixture with an oversized path/hash renders without
   error and yields a sensible page count (wrapping, no overflow crash).

**Safety / invariance tests:**

8. **Missing dependency / font failure** — monkeypatch the engine import to
   raise `ImportError`, and (separately) point the font path at a missing file;
   assert the typed error is raised, **no `.pdf` is written**, and `case.json`
   is byte-identical before/after.
9. **No mutation of `case.json` or UI language** — hash `case.json` before/after
   PDF generation (unchanged); assert `get_language()` is unchanged (mirrors the
   existing localization tests).
10. **Existing Markdown unchanged** — generate `.md`, then generate `.pdf`;
    assert the `.md` bytes are identical afterward.

**Determinism test:**

11. **Reproducible content vs. variable metadata** — render the same case twice
    with (a) a **pinned generation timestamp** (inject a fixed `generated_at`
    instead of `datetime.now()`) and (b) the engine's **invariant/reproducible
    mode** (fixed internal date + document ID); assert the two renders are
    **byte-identical**. Document that the naturally variable parts are the
    "Report Generated" timestamp and the PDF `/CreationDate`/`/ID`, which the
    test pins. This confirms determinism without asserting on volatile metadata
    in normal operation.

To support tests 6–7 and 11, the renderer must accept an injectable
`generated_at` and an option to enable invariant/reproducible output.

Invariant/reproducible mode is **off by default in production** so delivered
PDFs carry honest embedded metadata (real `/CreationDate` and document `/ID`).
It is enabled explicitly (`invariant=True`) only by the determinism tests, which
also pin `generated_at`. The visible "Report Generated" timestamp is always the
injected or real generation time regardless of this setting.

---

## 10. Q9 — Scope

### 10.1 In scope (smallest cohesive milestone)

1. A PDF renderer (`PdfReportFormatter.format_pdf`) that lays out the existing
   localized structured report dict for both report kinds, using the bundled
   font, an in-memory render, and injectable timestamp + invariant mode.
2. HERMES parallel methods: `build_technician_pdf_bytes()`/`save_technician_pdf()`,
   `build_customer_pdf_bytes()`/`save_customer_pdf()`, and the two PDF filename
   helpers — mirroring the Markdown trio, with per-(report, language, format)
   refuse-on-overwrite.
3. Bundled DejaVu Sans / Sans Mono fonts + license under `Source/assets/fonts/`.
4. A minimal Delivery-workflow format prompt (Markdown / PDF / Both), preserving
   independent per-report language selection, with ECHO logging and graceful
   PDF-unavailable handling.
5. New localized `report.*` keys (format prompt, PDF saved/failure lines,
   confidentiality marking) in `en.json` and `de.json`.
6. Declaration of the new dependency and a documented (offline-capable) Pi
   install step.
7. `Tests/test_pdf_report_export.py` and any i18n parity additions.

### 10.2 Explicitly out of scope

- Digital signatures.
- Encryption / password protection.
- Email or any network delivery.
- Customer branding customization.
- Logos / images (text-only reports; keeps Pillow unexercised).
- GUI preview.
- Archival **PDF/A** conformance.
- Partner reports (still a `NotImplementedError` stub).
- Any change to report **content**, field selection, or **section order**.
- Any change to `case.json`, `manifest.py`, `case_loader.py`, or
  `ReportFormatter.format_markdown`.
- Removing or altering the existing Markdown output.

---

## 11. Expected source / dependency files to change during implementation

| File | Expected change |
|---|---|
| `Source/modules/pdf_report_formatter.py` *(new)* | `PdfReportFormatter.format_pdf(...) -> bytes`: layout, bundled-font registration, header/footer/page-number callbacks, confidentiality banner, in-memory render, invariant/reproducible option, long-token wrapping. Lazy-imports the PDF engine. |
| `Source/modules/hermes.py` | Add `build_<type>_pdf_bytes()`, `save_<type>_pdf()`, and `*_report_pdf_filename(lang)` mirroring the Markdown methods; per-(report, language, format) refuse-on-overwrite. No change to report content, sections, or order. |
| `Source/assets/fonts/` *(new)* | Bundled `DejaVuSans.ttf`, `DejaVuSansMono.ttf`, and their `LICENSE` (must be under `Source/` to be deployed by `deploy.sh`). |
| `Source/bin/sentinel` | Add `_prompt_report_format()`; extend `_offer_report_generation` dispatch to save MD/PDF/Both; ECHO the format; catch PDF-unavailable. Independent per-report language preserved. |
| `Source/i18n/en.json`, `Source/i18n/de.json` | New `report.*` keys: format prompt/options, PDF saved line, PDF-unavailable message, confidentiality marking. |
| `pyproject.toml` (and/or a new `requirements.txt`) | Declare the PDF engine dependency (project's first). |
| `Tests/test_pdf_report_export.py` *(new)* | The tests in §9. |
| `Tests/test_i18n.py` | EN/DE parity for the new `report.*` keys (if the parity test enumerates them). |
| Deployment/runtime docs (`EngineeringManual.md`, `Roadmap.md`) | Document the Pi install step (offline-capable) and mark v0.5 "PDF export" progress — updated **at implementation time**, not in this analysis pass. |

**Not changed:** `Source/modules/report_formatter.py`,
`Source/modules/manifest.py`, `Source/modules/case_loader.py`,
`Source/core/session.py`, and the `case.json` schema.

---

## 12. Risks and open product decisions

**Risks**

1. **First third-party dependency.** Introduces a dependency into a currently
   pure-stdlib project (Engineering Value: "avoid unnecessary dependencies").
   Requires architect sign-off. Mitigation: a single, mature, BSD-licensed,
   pure-Python engine; text-only usage; Pillow transitive left unexercised.
2. **Deployment gap.** `deploy.sh` has **no** dependency-provisioning step, so
   the engine (and its Pillow transitive) must be installed on the MiniBerry out
   of band, with an **offline** path for a lab without internet (vendored wheel
   or distribution package). This must be designed and documented before rollout.
3. **Font bundling location.** Fonts must live under `Source/` (not a top-level
   `Assets/`) to deploy; license file must accompany them.
4. **Determinism.** PDF `/CreationDate`/`/ID` and the generation timestamp are
   inherently variable; reproducibility relies on the engine's invariant mode +
   an injectable timestamp (designed for in §9).
5. **Real-hardware validation.** Per the Engineering Manual, the renderer (fonts,
   umlauts, ARM performance) must be validated on the actual Raspberry Pi, not
   only on the Mac.

**Open product decisions**

- **A. Default format** in the workflow prompt: Markdown (current behavior,
  recommended) vs. Both.
- **B. Engine choice confirmation:** ReportLab (recommended) vs. fpdf2
  (lighter fallback).
- **C. Confidentiality wording** for the Technician Report banner (EN + DE) —
  requires the same product/legal sign-off as other customer/legal-adjacent
  policy text.
- **D. Renderer placement:** dedicated `pdf_report_formatter.py` (recommended,
  isolates the dependency) vs. a lazily-imported `ReportFormatter.format_pdf`
  method (strict "extend").
- **E. Offline install mechanism** on the Pi: vendored wheel in the repo vs.
  distribution package vs. piwheels at provisioning time.
- **F. Standard-14 fallback:** whether an engine/font failure should hard-fail
  (recommended) or fall back to non-embedded standard fonts.

---

## 13. Constitutional alignment

- **SL-004 (Explain Every Decision):** the PDF renders the same explainable,
  traceable content already derived from the case; a German customer receives a
  German PDF.
- **SL-006 (The Operator Decides):** PDF generation stays behind explicit
  operator approval and an explicit format choice.
- **AP-002 (No Circular Dependencies):** HERMES reads the case; the PDF renderer
  consumes HERMES output; no module depends on HERMES.
- **AP-003 (Facts Are Immutable):** no facts created or modified; nothing
  rendered is persisted to `case.json`.
- **AP-004 (One Responsibility Per Subsystem):** HERMES selects/formats content;
  the PDF renderer only lays out; `ReportFormatter` keeps owning Markdown; ECHO
  records generation.
- **AP-006 (Workflow Before Features):** the feature is completed by wiring it
  into the Delivery workflow, not just adding a builder.
- **Implementation-Standards / smallest change:** reuse the structured report
  dict, mirror the existing save/overwrite/filename pattern, add one workflow
  prompt, isolate the dependency, and leave the Markdown path untouched.

---

## Related Documents

- `REPORTING.md` — reporting architecture; "Future PDF Generation"
- `REPORT_SCHEMA.md` — report content schema and information classification
- `CustomerReport.md` — Customer Report architecture and provable-fact limits
- `ReportLocalization.md` — EN/DE report localization and the shared structured dict
- `ArchitecturePrinciples.md`, `SentinelLaws.md`, `EngineeringValues.md`
- `Docs/Engineering/EngineeringManual.md` — Mac/Pi roles, deployment, offline runtime
- `Docs/Engineering/Planning_Post_HERMES_Phase2.md` — milestone ordering (M4: PDF export)
- `Docs/Roadmap.md` — v0.5 Reports (PDF export)
