# CyberTrace user consent portal (frontend starter)

## Contents
- `templates/user_portal.html`
- `static/user_portal.js`
- `static/user_portal.css`

## Flask integration
Copy the template and static files into the corresponding folders of your Flask app, then add a route such as:

```python
@app.get("/share")
@login_required
def user_portal():
    return render_template("user_portal.html")
```

Adapt `@login_required` to your actual authentication setup. Serve only over HTTPS in production.

## Backend endpoints required
The JavaScript posts to:
- `POST /api/location`: accept location only for the authenticated user/session, validate consent and numeric ranges, record timestamp and accuracy, and authorize only the relevant admin to view it.
- `POST /api/location/stop`: mark sharing as stopped for that user's active session.

Implement server-side authentication and authorization, CSRF protection (the JS will send `X-CSRFToken` if a `meta[name="csrf-token"]` tag is present), rate limiting, input validation, retention limits, and audit logging. Never trust a user ID supplied by the browser to determine ownership; derive it from the authenticated session.

## Important behavior
- Location permission is requested by the user's explicit button click. Data is posted only after the browser returns a location.
- Camera permission is requested by a separate button click. This starter only shows a local preview; it does NOT upload or stream video to the server.
- Stop clears the geolocation watch and stops camera tracks locally.
- Browser location/camera APIs require a secure context (HTTPS, or localhost during development) and user permission.
- A browser permission is not a substitute for informed consent to share data with an administrator. Explain who can see data, purpose, duration, and how to revoke access before asking.
- Add a visible privacy notice and a real CSRF token for production. The sample is a frontend starter; the API endpoints must be implemented and secured before deployment.
