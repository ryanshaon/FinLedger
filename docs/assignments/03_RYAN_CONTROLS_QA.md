# Ryan — Short owner checklist

Updated 2026-10-22. Your previous coding, UI-fix, regression-test, Auth/MFA verification
and detailed end-to-end QA tasks have moved to Ruthvik's
[release-integration assignment](RELEASE_INTEGRATION.md). Harshith owns the separate
[backend handover](HARSHITH_PLATFORM_HANDOVER.md). You do not need to duplicate their tests.

## Your remaining actions

- [ ] Approve proposed hosting and AI account/model, processing location, retention controls
  and budget when the contributor presents specific choices and costs.
- [ ] Supply approved access and enter private credentials securely yourself; never share
  your login, `.env`, keys or passwords in Git or chat. Delegate setup through approved access.
- [ ] After passing evidence and a staging demo are provided, do one short synthetic user
  walkthrough: sign in, open an invoice, review/correct it, then sign out.
- [ ] Accept the result or report what feels wrong; decide whether and when to release.

Ruthvik and Harshith own implementation, tests, failure/retry/security checks,
deployment proofs, fixes and release documentation within their assignments. Request independent review for
security-sensitive changes rather than personally implementing or auditing them.

Read root HANDOVER for mandatory rules and unresolved gates. Approval does not mean the app
is production-ready; use synthetic data until a separate customer release is approved.
Person 4 remains separately owned and untouched.
