# HERMES Report Localization

Version: 0.1
Status: Design (analysis only)
Author role: Cursor (implementation assistant)
Scope: Render the HERMES Technician and Customer Reports in English **and**
German, reusing the existing Translator, without changing report architecture,
without making `ReportFormatter` responsible for translation, and without
storing translated prose in `case.json`.

---

## Path correction (inspected reality)

The request referenced `Source/modules/translator.py`, `Source/locales/en.json`,
and `Source/locales/de.json`. Those paths **do not exist**. The actual
translation layer is:

- `Source/i18n/translator.py` (implementation)
- `Source/i18n/__init__.py` (public re-exports)
- `Source/i18n/en.json`, `Source/i18n/de.json` (catalogs)

This document uses the real paths.

This is a design document only. It authorises no implementation and modifies no
source, tests, or existing localization files. It is subordinate to and
consistent with the Sentinel Constitution and the reporting documents
(`REPORTING.md`, `REPORT_SCHEMA.md`, `CustomerReport.md`,
`RecoveryOperationReporting.md`).

---

## 1. Current state (what was inspected)

### 1.1 Translator (`Source/i18n/translator.py`)

- `SUPPORTED_LANGUAGES = ("en", "de")`, `DEFAULT_LANGUAGE = "en"`.
- A **process-global** active language `_language`, plus per-language `_catalogs`
  loaded lazily from `i18n/<lang>.json`.
- `tr(key, **kwargs)` looks up `key` in the **current global** language, falls
  back to English, then to `"[" + key + "]"` if absent. `.format(**kwargs)` is
  applied when kwargs are supplied.
- Language resolution (`init_language`): `SENTINEL_LANG` env →
  `state/sentinel_config.json` → English. `set_language(lang, persist=True)`
  mutates the global and (optionally) persists it.
- Domain display helpers (`display_aegis_reason`, `display_janus_reason`,
  `display_oracle_step`, …) map hard-coded English **fact strings** to keys via
  the global language. **HERMES does not use these** today.

**Key constraint discovered:** there is currently **no way to render a key in an
explicit language** without calling `set_language(...)` and mutating global
state. Report localization needs exactly that capability (a German lab may
deliver an English customer report, or vice versa), so the Translator must gain
an explicit-language lookup (see §3.1).

### 1.2 HERMES (`Source/modules/hermes.py`)

- **Does not import `i18n` at all.** Every human string is a hard-coded English
  literal:
  - Report titles passed to the formatter: `"Technician Report"`,
    `"Customer Report"`.
  - Section titles: `TECHNICIAN_REPORT_SECTIONS`, `CUSTOMER_REPORT_SECTIONS`
    (these are used **both** as the section dict keys and as the `section_order`
    passed to the formatter).
  - Field labels: the **keys** of each section dict (e.g. `"Case Number"`,
    `"Recovery Attempt Recorded"`, `"Recovered File Count"`).
  - Customer sentences/policy: `CUSTOMER_OUTCOME_WORDING`,
    `CUSTOMER_IMAGING_*`, `CUSTOMER_RECOMMENDATIONS`, `CUSTOMER_DISCLAIMER`,
    `CUSTOMER_POLICY_VERSION`.
  - Placeholders/values: `"Not recorded"`, `"Not reported"`, `"None recorded"`,
    `"No audit events recorded"`, `"Present but unreadable"`, `"Yes"`/`"No"`.
- Report data is assembled as ordered dicts of `{label: value}` and handed to
  `ReportFormatter`.

### 1.3 ReportFormatter (`Source/modules/report_formatter.py`)

- Pure structural renderer: emits `# {title}`, `## {section_title}`, and
  `{key}: {value}` (or bulleted lists). **No i18n import; no lookups.** It
  renders whatever strings it is given, in whatever order.

### 1.4 Generation workflow (`Source/bin/sentinel`)

- `_run_delivery_workflow` → `_offer_report_generation(...)` twice: Technician
  then Customer. Each prompts (`report.prompt.generate*`), calls
  `Hermes(session).save_technician_report()` / `save_customer_report()`, prints
  a localized "saved" line, and logs to ECHO.
- Filenames are fixed: `reports/technician_report.md`,
  `reports/customer_report.md`; both **refuse to overwrite** (`FileExistsError`).
- The CLI already has a global language selector (`_prompt_language_selection`
  → `set_language`), and report **operator prompts** are already localized
  (`report.*` keys in `en.json`/`de.json`). Only the **report file content** is
  English-only.

**Conclusion:** localization is entirely a HERMES concern. `ReportFormatter`,
`case.json`, and the workflow shape stay as they are; the Translator gains one
capability; the CLI gains a per-generation language choice.

---

## 2. Design decisions

### 2.1 Language ownership (Question 1)

| Question | Decision |
|---|---|
| Where does the report language come from? | Resolved by HERMES from an **explicit report-language argument**, defaulting to the operator UI language (`get_language()`), defaulting in turn to English. |
| Operator UI language, case-level language, or explicit choice? | **Explicit per-generation choice, defaulting to the operator UI language.** The Technician Report is internal (lab language); the Customer Report may need the *customer's* language, which can differ from the UI. A single global cannot express "German UI, English customer report", so the choice is explicit at generation time. |
| Case-level language field? | **Not in this milestone.** Storing a customer-language *code* (not prose) in `case.json` is a defensible future case fact, but it adds a schema field and intake business logic. The smallest safe milestone resolves language at generation time instead. (Kept as an open product decision — §8.) |
| Legacy cases with no language field? | **No field is introduced, so there is nothing to be missing.** Language is chosen at generation; absence of any choice → operator UI language → English. Existing English behavior is preserved. |

### 2.2 Translation boundaries (Question 2)

Each boundary maps to its own key namespace and localization rule. The guiding
line (AP-003, `REPORT_SCHEMA.md`): **labels and prose are localized; recorded
facts and technical tokens are data and are rendered as-is.**

| # | Boundary | Localize? | Namespace | Notes |
|---|---|---|---|---|
| 1 | Section headings | Yes | `report.section.*` | Same key set drives dict keys **and** `section_order` (§3.2). |
| 2 | Field labels | Yes | `report.field.*` | Shared labels (e.g. Case Number) reuse one key across both reports. |
| 3 | Enum / display **values** | Yes (only presentation tokens) | `report.value.*` | `Yes`/`No`, SMART `Available` Yes/No, "Not reported". **Not** acquisition state *codes*, SHA-256 digests, sizes, paths, serials → those are data. |
| 4 | Customer-facing sentences | Yes | `report.customer.imaging.*`, `report.customer.outcome.*` | The three imaging statements and the three outcome statements. |
| 5 | Recommendations | Yes (versioned policy) | `report.customer.recommendation.*` | Versioned HERMES-owned content; DE requires product/legal sign-off (§8). |
| 6 | Disclaimer | Yes (versioned policy) | `report.customer.disclaimer.*` | Same as recommendations. |
| 7 | Placeholders | Yes | `report.placeholder.*` | "Not recorded", "None recorded", "No audit events recorded", "Present but unreadable". |
| 8 | Internal-only Technician wording | Labels yes, **raw values no** | (uses 1/2/3/7) | Acquisition state codes, digests, paths, sector sizes, byte counts remain untranslated data. The **Audit Timeline** content is raw ECHO lines — never translated. |

Report **titles** ("Technician Report"/"Customer Report") are boundary 1's
siblings: `report.title.technician`, `report.title.customer`.

### 2.3 Architecture (Question 3)

- **Translator gains explicit-language lookup** (§3.1); no second translation
  system is created (per the request).
- **HERMES resolves the language once and threads it** (§3.2); it becomes the
  sole translation consumer for report content.
- **`ReportFormatter` stays language-agnostic** — confirmed: it keeps taking
  already-localized strings and rendering them. No i18n import, no change.
- **`case.json` stays the source of case facts, not prose** — confirmed:
  localization happens only at render time; facts are read unchanged.
- **No rendered report text is stored in `case.json`** — confirmed: reports are
  written only to `reports/…md`, exactly as today.

### 2.4 Scope boundary

In scope: EN + DE rendering of both reports, Translator extension, report keys,
CLI language selection at Delivery, language-suffixed filenames.
**Out of scope:** PDF export, Partner Report, case-level language field, any
`ReportFormatter` redesign, any `case.json`/persistence change, translating
stored fact strings (e.g. AEGIS reason prose) — see §8.

---

## 3. Proposed architecture

### 3.1 Translator extension (smallest change)

Add an **explicit-language** lookup that reuses the existing catalog cache and
fallback chain, without mutating the global `_language`:

```python
def translate(key, language=None, **kwargs):
    lang = _resolve_language(language) if language else _language
    _ensure_catalog(lang)
    template = _catalogs.get(lang, {}).get(key)
    if template is None:
        template = _catalogs.get(DEFAULT_LANGUAGE, {}).get(key)
    if template is None:
        return f"[{key}]"
    return template.format(**kwargs) if kwargs else template
```

- `tr(key, **kwargs)` becomes a thin wrapper (`translate(key, None, **kwargs)`),
  so its behavior and every existing caller are unchanged.
- Fallback order is preserved: report-language → English → `[key]`. This makes a
  missing DE key **degrade to English**, never to a broken `[key]` in a
  delivered document (guarded by a parity test — §5).
- Exported from `i18n/__init__.py` alongside `tr`.

This is the single mechanism HERMES uses; no per-module string tables.

### 3.2 HERMES threading

- `Hermes.__init__(self, session, language=None)` stores
  `self.language = language` (resolved lazily; `None` → operator UI language via
  `get_language()`), keeping the existing `Hermes(session)` call sites valid.
- Section titles and field labels are produced from `report.section.*` /
  `report.field.*` via `translate(key, self.language)`. Because the section dict
  keys **and** the `section_order` are both built from the **same** localized
  strings, `ReportFormatter` still receives a consistent (title-keyed dict +
  matching order) pair — no formatter change.
- Values, placeholders, and customer sentences are localized at the point they
  are set (e.g. a `_localized_yes_no(bool)` helper; `_customer_imaging(state)`
  returns `translate("report.customer.imaging.<state-class>", self.language)`).
- Recommendations/disclaimer are read as an **ordered list of keys** from the
  catalog and rendered in the report language, retaining `CUSTOMER_POLICY_VERSION`
  (now per-language versioned content).

Representative flow (unchanged shape, localized strings):

```
Hermes(session, language)               # language resolved once
  build_technician_report()             # dict keyed by localized section titles,
                                        #   fields keyed by localized labels,
                                        #   values localized where presentational
  build_technician_markdown()           # ReportFormatter.format_markdown(
                                        #   translate("report.title.technician", language),
                                        #   report,
                                        #   section_order=<localized titles, same order>)
  save_technician_report()              # writes reports/technician_report.<lang>.md
```

### 3.3 What must NOT change

- `ReportFormatter` — no i18n, no signature change.
- `case.json` schema, `manifest.py`, `case_loader.py` — untouched.
- The read-only, owner-API discipline of HERMES.
- The set and order of report sections (localization renames the *display*, not
  the structure).

---

## 4. Report-generation workflow (Question 4)

```
Delivery phase (recovery + verification complete, operator present)
        │
        ▼
Operator approves Technician Report?  [y/N]      (existing prompt)
        │ y
        ▼
Select report language  [1] English  [2] Deutsch   (default = current UI language)
        │
        ▼
Hermes(session, language).save_technician_report()
        → reports/technician_report.<lang>.md   (refuse-on-overwrite, per language)
        │
        ▼
Operator approves Customer Report?    [y/N]      (existing prompt)
        │ y
        ▼
Select report language  [1] English  [2] Deutsch   (default = current UI language)
        │
        ▼
Hermes(session, language).save_customer_report()
        → reports/customer_report.<lang>.md      (refuse-on-overwrite, per language)
        │
        ▼
ECHO records generation (including language + path)
```

- **How the operator selects/confirms language:** a small selection step reusing
  the existing language-selection *pattern* (`language.option.en/de`) but with
  **report-scoped prompt keys** (e.g. `report.prompt.language`). Pressing the
  default accepts the current UI language, so an English operator sees no new
  friction and unchanged content. The selection **does not** call
  `set_language` — it does not change the operator UI language (no global side
  effect); it only passes a `language` argument to `Hermes`.
- **Same behavior for both reports?** Same *mechanism*, **independent choices**.
  The reports are already generated independently; keeping the choice per report
  lets the lab produce a German technician file and an English customer file (or
  any mix). The default for each is the current UI language.
- **Filename language identifier?** **Yes.** Files become
  `technician_report.<lang>.md` and `customer_report.<lang>.md`
  (e.g. `technician_report.de.md`). This lets multiple language versions of the
  same case coexist and makes the artifact self-describing.
- **Overwrite protection with multiple languages:** the existing
  refuse-on-exists check now applies **per (report_type, language)** file. A
  German report never collides with an existing English one; regenerating the
  *same* language still raises `FileExistsError`. No content is silently
  overwritten.

---

## 5. Compatibility (Question 5)

| Concern | Behavior |
|---|---|
| **Existing English report behavior** | Identical *content* (labels/sentences EN unchanged, sourced from `report.*` keys whose EN values equal today's literals). The one deliberate change is the **filename** gaining a language suffix (§8 open decision on whether EN keeps the bare name). |
| **Legacy cases** | No language field is added; any case can be reported in either language. No migration, no back-fill. |
| **Missing translation keys** | Fallback chain renders the English string (never `[key]`). A **DE-parity test** (every `report.*` key present in both catalogs) prevents silent English leakage into a German document. |
| **Unsupported languages** | `_resolve_language` clamps to `SUPPORTED_LANGUAGES` → English; the CLI only offers EN/DE. An explicit unknown `language` argument resolves to English. |
| **Existing tests and APIs** | `Hermes(session)` keeps working (`language` defaults to UI/EN). `tr(...)` unchanged. `build_report`, `build_technician_report`, `build_customer_report`, markdown/save methods keep their signatures with an optional language. `test_hermes.py` needs updates **only** for localized labels/values it asserts and for the new filenames; `test_i18n.py` gains the parity assertion. |

---

## 6. Proposed translation-key categories and representative examples

Namespace prefix: `report.`. Representative (illustrative; DE policy/legal text
requires sign-off — §8):

| Category | Key | English | German (illustrative) |
|---|---|---|---|
| Title | `report.title.technician` | Technician Report | Technikerbericht |
| Title | `report.title.customer` | Customer Report | Kundenbericht |
| Section | `report.section.case_information` | Case Information | Fallinformationen |
| Section | `report.section.recovery_statistics` | Recovery Statistics | Wiederherstellungsstatistik |
| Section | `report.section.work_performed` | Work Performed | Durchgeführte Arbeiten |
| Field | `report.field.case_number` | Case Number | Fallnummer |
| Field | `report.field.recovery_attempt_recorded` | Recovery Attempt Recorded | Wiederherstellungsversuch erfasst |
| Field | `report.field.recovered_file_count` | Recovered File Count | Anzahl wiederhergestellter Dateien |
| Value | `report.value.yes` / `report.value.no` | Yes / No | Ja / Nein |
| Placeholder | `report.placeholder.not_recorded` | Not recorded | Nicht erfasst |
| Placeholder | `report.placeholder.none_recorded` | None recorded | Keine erfasst |
| Placeholder | `report.placeholder.unreadable` | Present but unreadable | Vorhanden, aber nicht lesbar |
| Customer sentence | `report.customer.imaging.completed` | A complete forensic image of the device was created. | Es wurde ein vollständiges forensisches Abbild des Geräts erstellt. |
| Customer sentence | `report.customer.outcome.partial` | Some of the requested data was recovered. | Ein Teil der angeforderten Daten wurde wiederhergestellt. |
| Recommendation | `report.customer.recommendation.1` | Verify that the recovered data is complete… | (DE, sign-off) |
| Disclaimer | `report.customer.disclaimer.1` | This report summarizes the data recovery work… | (DE, sign-off) |

Notes:
- Shared labels (e.g. `report.field.case_number`) are reused by both reports.
- Recommendations/disclaimer are **ordered lists** of keys
  (`report.customer.recommendation.1..N`) so the formatter's list rendering is
  unchanged; the count stays HERMES-owned and versioned per language.
- **Not keyed (rendered as data):** acquisition state codes
  (`ACQUISITION_*`), SHA-256 digests, byte sizes, sector sizes, paths, serials,
  SMART health tokens (`PASSED`), and raw Audit Timeline lines.

---

## 7. Expected filename behavior

- New: `reports/technician_report.<lang>.md`, `reports/customer_report.<lang>.md`
  where `<lang> ∈ {en, de}`.
- Each `(report_type, language)` is independently refuse-on-overwrite.
- Multiple language versions of one case coexist
  (`customer_report.en.md` + `customer_report.de.md`).
- **Open decision (§8):** whether English keeps the historical bare name
  (`technician_report.md`) for backward compatibility, or also gains `.en`. The
  recommended default is to suffix **all** languages (uniform, self-describing),
  accepting that the two filename constants and their tests change.

---

## 8. Risks and open product decisions

**Risks**

1. **DE policy/legal wording.** Recommendations and disclaimer are
   customer-facing legal-adjacent text. German versions must be authored/approved
   by the product architect (and, ideally, legally reviewed), not machine
   translated. Versioning (`CUSTOMER_POLICY_VERSION`) must track per-language
   revisions.
2. **Localized-label-as-dict-key coupling.** Section/field labels double as dict
   keys; two labels that translate to the same string within one section would
   collide. Mitigation: keep keys distinct per section and add a test asserting
   unique localized labels per section in both languages.
3. **Filename change.** Suffixing English changes existing artifact expectations
   and tests; a deliberate, tested change rather than silent.
4. **Global-state avoidance.** Report rendering must use the explicit-language
   `translate(...)`, never `set_language(...)`, to avoid mutating the operator UI
   language mid-session and to stay reproducible/testable.
5. **Mixed-language leakage.** If HERMES ever routes a stored fact string through
   a global-language display helper, labels (report language) and that value
   (UI language) could disagree. Today HERMES uses no such helper; keep it that
   way (or pass the report language explicitly if one is introduced).
6. **Scope volume.** Localizing the Technician Report multiplies key count. If
   effort must be reduced, Customer-Report-first is a safe partial (see below),
   but the request asks for both.

**Open product decisions**

- **A. English filename:** bare `technician_report.md` (back-compat) vs uniform
  `technician_report.en.md` (recommended).
- **B. Case-level customer language:** persist a customer-language *code* in
  `case.json` later (so the default customer report language is remembered), or
  keep generation-time selection only (this milestone).
- **C. DE policy text:** approval of German recommendations/disclaimer wording.
- **D. Technician Report localization now vs Customer-first:** the request
  implies both; confirm.
- **E. Per-report vs single language prompt:** independent per report
  (recommended, flexible) vs one prompt for the delivery step (simpler).

---

## 9. Smallest safe implementation milestone (Question 6)

1. **Translator:** add `translate(key, language=None, **kwargs)` (+ export);
   re-express `tr` on top of it. No behavior change for existing callers.
2. **Catalogs:** add `report.*` keys (all categories in §6) to **both**
   `en.json` and `de.json`, EN values equal to today's literals.
3. **HERMES:** thread an optional `language` (default = UI language); replace
   hard-coded titles/sections/labels/values/placeholders/customer
   sentences/recommendations/disclaimer with `translate(..., self.language)`;
   keep raw facts as data. No structural change.
4. **Filenames:** language-suffixed report files; per-language
   refuse-on-overwrite.
5. **CLI:** at Delivery, offer a report-language choice per report (default = UI
   language) and pass it to `Hermes`; **do not** change the global UI language;
   log the chosen language via ECHO.
6. **Tests:** update `test_hermes.py` (localized labels/values, filenames); add a
   DE-parity test in `test_i18n.py`; optionally a focused
   `test_report_localization.py` (render both languages, assert no `[key]`, EN
   unchanged).

Explicitly **excluded:** PDF (deferred), Partner Report, `case.json` fields,
`ReportFormatter` changes, translation of stored fact prose.

---

## 10. Exact source files expected to change during implementation

| File | Change |
|---|---|
| `Source/i18n/translator.py` | Add `translate(key, language=None, **kwargs)`; base `tr` on it. |
| `Source/i18n/__init__.py` | Export `translate`. |
| `Source/i18n/en.json` | Add `report.*` keys (EN = current literals). |
| `Source/i18n/de.json` | Add `report.*` keys (DE, policy text pending sign-off). |
| `Source/modules/hermes.py` | Thread `language`; localize all report strings via `translate`; language-suffixed filenames. |
| `Source/bin/sentinel` | Report-language selection at Delivery; pass to `Hermes`; ECHO the language; localized "saved" line already exists. |
| `Tests/test_hermes.py` | Update localized-label/value assertions and filenames. |
| `Tests/test_i18n.py` | Add EN/DE `report.*` key-parity test. |
| `Tests/test_report_localization.py` (new, optional) | Render both languages; assert EN unchanged and no `[key]`. |

**Not changed:** `Source/modules/report_formatter.py`, `Source/modules/manifest.py`,
`Source/modules/case_loader.py`, `Source/core/session.py`, `case.json` schema.

---

## 11. Constitutional alignment

- **SL-004 (Explain Every Decision):** a German customer receives an
  understandable German report — explanation in the reader's language.
- **SL-006 (The Operator Decides):** the operator approves generation and now
  also selects the report language.
- **AP-002 / AP-004:** HERMES still only presents; the Translator only
  translates; `ReportFormatter` only formats. No new dependency, no circularity.
- **AP-003 (Facts Are Immutable):** facts stay in `case.json`; only presentation
  is localized; no translated prose is persisted.
- **Implementation-Standards / smallest change:** one Translator function, added
  keys, HERMES string routing, and a CLI prompt — extending the existing i18n
  and HERMES architecture rather than building a second translation system.

---

## Related Documents

- `CustomerReport.md` (§8 localization scope note)
- `RecoveryOperationReporting.md`
- `REPORTING.md`
- `REPORT_SCHEMA.md`
- `ArchitecturePrinciples.md`
- `SentinelLaws.md`
- `EngineeringValues.md`
