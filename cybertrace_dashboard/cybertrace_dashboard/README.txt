CyberTrace status/freshness fix based on the uploaded app.py and app.js.

1. Stop Flask with Ctrl+C.
2. Back up app.py, static/app.js, and data/cybertrace.sqlite3.
3. Replace app.py and static/app.js with these files. Keep the rest of the project unchanged.
4. Restart Flask and log into the admin panel.
5. In /share, click the location button and grant browser permission.
6. Admin polls status every 5 seconds; it shows active only while location updates arrive within 30 seconds. Stopped or old sessions show as inactive.

This patch creates a new share_sessions table and keeps the existing locations/events tables. Camera remains local preview only. Do not expose Flask's development server directly to the public internet.
