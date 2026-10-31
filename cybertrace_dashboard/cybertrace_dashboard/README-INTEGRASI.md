# CyberTrace integration patch

Files:
- `app.py`: based on the uploaded app.py, adds explicit-consent endpoints `/api/share/consent`, `/api/share/location`, `/api/share/stop`.
- `templates/user_portal.html`, `static/user_portal.js`, `static/user_portal.css`: user portal; JS updated to use the new endpoints.
- `static/app.js`: admin JS supplied by user, unchanged.

## Apply safely
1. Back up the current project folder and SQLite database (`data/cybertrace.sqlite3`).
2. Copy these files into the matching paths in the existing project. Replace `app.py` only after backing it up; keep all other templates/assets/database files from the existing project.
3. Make sure `templates/user_portal.html` uses the provided JS/CSS via Flask `url_for`.
4. Restart Flask.
5. Open `/share`, click "Izinkan lokasi", allow the browser permission. The portal should show "terkirim" after successful POST.
6. Open admin `/` in a separate tab and refresh the page. The current admin dashboard queries the same `locations` table on page load; the new location appears as the latest point/history.

## Important limitations
- This uses the existing shared `locations` table, so the admin can see submitted locations. It does not yet provide per-user identity management or an invite/token flow; don't publish a public link widely before adding those controls.
- Camera is local preview only. It does not stream video to the admin.
- Use HTTPS in deployment. The Flask dev server is local development only; don't expose it directly to the public internet.
- Before production, add rate limiting, CSRF/origin checks suited to deployment, privacy/retention controls, and a real share-session/invite system.
