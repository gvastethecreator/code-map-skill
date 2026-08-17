# Workplan — generator pipeline and fingerprints

Date: 2026-08-16
Language: Spanish
Mode: Execution — approved
Constraint: public skill; any Git repository; no machine-local paths

Frozen contract:
- PERF-01 parse source language files, not wiki dumps
- PERF-02 fingerprint binaries by path+size, not full bytes
- ARC-01 in-process `build`
- ARC-02 `render_html()` with no output-path policy
- ARC-03 tests on temporary Git repos only

Status: done
Verification: `python scripts/validate_package.py`; `python -B -m unittest SKILLS/maintain-code-map/scripts/test_codemap_tool.py` (10 tests, OK)

Target artifacts stay under `docs/codemap/` in the repository being mapped.
Docs-only repositories fall back to text files so the generator still runs.
