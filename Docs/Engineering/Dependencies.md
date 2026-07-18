# Runtime Dependencies

Version: 0.1
Status: Active
Scope: Third-party Python runtime dependencies and how to install them on the
MiniBerry (Raspberry Pi) runtime. This note is intentionally narrow. It does not
redesign the deployment system defined in `EngineeringManual.md`.

---

## Policy

Project Sentinel deliberately minimizes third-party dependencies. `deploy.sh`
copies the `Source/` tree to the MiniBerry via `rsync` and **does not install or
modify the Python environment** on the runtime. Installing a dependency on the
MiniBerry is therefore a **separate, explicit, one-time provisioning step**, not
part of ordinary deployment.

Dependencies are declared in `pyproject.toml` under `[project].dependencies`
with explicit, compatible version ranges.

---

## ReportLab (HERMES PDF export)

- **Package:** `reportlab`
- **Declared range:** `reportlab>=4.0,<5` (pinned to the 4.x line)
- **Purpose:** renders the existing HERMES Technician and Customer reports to
  PDF (`Source/modules/pdf_report_formatter.py`). It is the first third-party
  runtime dependency in Sentinel.
- **Why it is safe on the Pi:** the ReportLab core is pure Python and needs no
  system libraries (unlike WeasyPrint). Its transitive `pillow` dependency has
  prebuilt ARM wheels via piwheels and is not exercised by text-only reports.
- **Fonts:** no system fonts are used. The bundled DejaVu fonts under
  `Source/assets/fonts/` are deployed with `Source/` and resolved relative to
  the installed module, so no extra font provisioning is required.

Sentinel startup, case handling, and **Markdown** report generation work with
or without ReportLab installed. Only **PDF** export requires it; when it is
absent, PDF generation reports a clear, PDF-specific message and the workflow
continues.

### Online installation (MiniBerry has internet)

Run once on the MiniBerry, in the same interpreter that runs Sentinel
(Python 3.12+):

```bash
python3 -m pip install "reportlab>=4.0,<5"
```

On Raspberry Pi OS this resolves prebuilt ARM wheels (piwheels) for `reportlab`
and its `pillow`/`charset-normalizer` dependencies.

### Offline installation (air-gapped MiniBerry)

Data-recovery labs may run the MiniBerry without internet. Stage the wheels on
an internet-connected machine, transfer them, then install with no index.

1. On a **matching** machine (same OS/arch/Python minor version — ideally an
   identical Pi), download the dependency and its transitive wheels:

   ```bash
   python3 -m pip download "reportlab>=4.0,<5" -d sentinel-wheels
   ```

2. Copy the `sentinel-wheels/` directory to the MiniBerry (USB, `scp`, etc.).

3. On the MiniBerry, install strictly from the local directory (no network):

   ```bash
   python3 -m pip install --no-index --find-links sentinel-wheels "reportlab>=4.0,<5"
   ```

If an exact-match staging machine is not available, run the `pip download` step
directly on a networked MiniBerry once to capture the correct ARM wheels, then
reuse that `sentinel-wheels/` directory for offline installs on identical units.

### Verifying the installation

```bash
python3 -c "import reportlab; print(reportlab.Version)"
```

### Distribution package alternative

`sudo apt-get install python3-reportlab` also works but may lag the pinned
range; prefer the `pip` install above so the runtime matches
`pyproject.toml`.

---

## Related Documents

- `EngineeringManual.md` — machine roles, `deploy.sh`, runtime layout
- `Docs/Architecture/PdfReportExport.md` — PDF export design and rationale
- `pyproject.toml` — authoritative dependency declaration
