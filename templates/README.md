# templates/

Flask HTML templates.

## index.html

Single-page app. Served at `/`. No build step — plain HTML, CSS, and vanilla JS.

**Auth:** On load, checks localStorage for a JWT token. If present, calls the API directly. If not, shows the login screen. Token is obtained via `POST /api/login`.

**Structure:**
- Login screen — password input, calls `/api/login`
- App screen — rendered after login
  - *Scheduled Jobs* — lists all jobs from `GET /api/jobs`; each row has a toggle (`PATCH /api/jobs/:id`) and a Run button (`POST /api/jobs/:id/run`)

**Adding a new UI section:** Add a `<section>` block inside `#app-screen > main` and a corresponding JS function. The API is already available via the `api(method, path, body)` helper.
