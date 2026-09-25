# Public feedback implementation plan

**Goal:** Signed-in visitors submit bug reports/features to private project Intake; staff enable the form and moderate with existing tools.
**Architecture:** Reuse published board anchors, Space session auth and Intake. Restrict public visibility to accepted intake issues and owner/admin submission reads. Keep internal fields out of public responses.
**Spec:** ../product-hub/docs/superpowers/specs/2026-09-25-product-hub-design.md
**Stack:** Django/DRF, PostgreSQL, React, Propel.

- [x] Add contract tests for anonymous denial, cross-board IDs, invalid payloads, private submission reads, rejected/pending issue visibility and disabled boards; run on codex_feedback scratch only.
- [x] Add feedback_type to IntakeIssue (migration0146 depending0143); sanitize and allowlist submission data; throttle writes; owner/admin read scope and pending-only reporter edits.
- [x] Add submissions_enabled publish setting backed by default project Intake; public-safe settings serializer and admin validation.
- [x] Add Space Submit feedback dialog with sign-in link, type/title/description, error/success state; connect publish modal toggle.
- [x] Run focused real DB tests, affected UI type/build checks, format/lint. Commit locally and hand browser gates to coordinator. Browser validation remains a coordinator integration gate, not a source-only completion claim.

Constraints: No live DB migrations, server restarts, push or deploy. Only external task boundaries mocked. Existing regular published issues stay visible. Intake submissions become public only after staff acceptance; rejected/duplicate/pending stay private.