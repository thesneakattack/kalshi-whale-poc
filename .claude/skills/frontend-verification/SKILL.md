---
name: frontend-verification
description: Use for changes to frontend/src/js, API consumption, UI state, user-visible errors, generated dashboard.bundle.js, browser behavior, frontend/backend contracts, or frontend CI. Enforces source/bundle discipline plus lint, build, contract, browser, and console-error verification.
---

# Frontend Verification

1. Treat `frontend/src/js` as source and
   `static/js/dashboard.bundle.js` as generated output.
2. Never make the generated bundle the sole source edit.
3. Inspect the actual frontend API helper and view-refresh conventions before
   adding new fetch timers or state.
4. After source edits:
   ```bash
   cd frontend
   npm run lint
   npm run build
   cd ..
   ```
5. Verify rebuilding leaves the committed bundle synchronized.
6. For API changes, verify method/path/response expectations against current
   backend routes.
7. For user-visible behavior, run browser E2E/smoke when available and inspect
   severe browser console errors.
8. Browser tests use isolated temp DB/config state, no real credentials, and
   no real trading actions.
9. GitHub browser CI must not depend on DDEV-only DNS.
10. Backend pytest alone is not sufficient proof for user-visible changes.
