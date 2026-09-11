# Advanced

Everything beyond the basic one-click / one-command install: running from source,
configuration variables, the remote HTTP server (for ChatGPT and Claude.ai custom
connectors), Docker, the request model, and the full tool reference.

For basic install, see the [README](../README.md) and the [installation guide](installation.md).

## Requirements

- Python 3.11+
- `requests`, `beautifulsoup4`, `lxml`, `mcp`, `starlette`, `uvicorn`
- Optional: `playwright` for a login fallback when Ninova's form flow changes
  (`pipx install "ninova-mcp[playwright]"` then `playwright install chromium`)

The one-click `.mcpb` bundle ships its own Python runtime and all of these, so it
needs nothing preinstalled.

## Configuration

Only two variables are required:

```dotenv
NINOVA_USERNAME=your_username
NINOVA_PASSWORD=your_password
```

The server reads them from the process environment. If a `.env` file exists in the
current working directory (or any parent), `NINOVA_*`, `ITU_*`, and `OBS_*` variables
are loaded from it without overriding variables already set in the environment.

The OBS tools reuse `NINOVA_USERNAME` / `NINOVA_PASSWORD` — Ninova and OBS sit behind
the same `girisv3.itu.edu.tr` single sign-on. Set `ITU_USERNAME` / `ITU_PASSWORD` only
if OBS should use a different account.

### Optional overrides

```bash
export NINOVA_BASE_URL="https://ninova.itu.edu.tr"   # default
export OBS_BASE_URL="https://obs.itu.edu.tr"         # default
export NOTKUTUSU_API_URL="https://mordor-api-production.notkutusu.com"  # default
export NINOVA_STATE_DIR="/absolute/path/to/.ninova_state"
export NINOVA_DISABLE_PLAYWRIGHT_FALLBACK="1"
export NINOVA_ENV_FILE="/absolute/path/to/.env"
```

## Run

After installing the package, start the local stdio server:

```bash
ninova-mcp
```

It waits silently because your MCP client talks to it over stdin/stdout. To run from a
source checkout without installing:

```bash
PYTHONPATH=src python3 -m ninova_mcp
```

## Remote HTTP server (ChatGPT / Claude.ai custom connectors)

ChatGPT (Developer Mode) and Claude.ai custom connectors cannot use a local stdio
server — they need a publicly reachable **Streamable HTTP** endpoint. Run it only on
infrastructure you control:

```bash
export NINOVA_USERNAME="your_username"
export NINOVA_PASSWORD="your_password"
export NINOVA_REMOTE_MCP_PATH="/mcp-choose-a-long-random-secret"
export NINOVA_PUBLIC_BASE_URL="https://ninova-mcp.example.com"
export NINOVA_ALLOWED_HOSTS="ninova-mcp.example.com"
export NINOVA_ALLOWED_ORIGINS="https://claude.ai,https://claude.com"
ninova-mcp-remote
```

Your connector URL is then `https://ninova-mcp.example.com/mcp-choose-a-long-random-secret`.

Endpoints: `GET /healthz` (health check) and `POST/GET <NINOVA_REMOTE_MCP_PATH>` (the MCP
endpoint). This uses an authless remote setup (OAuth is not implemented yet), so choose a
long random path and keep the full URL private.

> **Privacy warning:** a hosted remote server means whoever uses it sends their İTÜ
> credentials to *your* server. Prefer the local stdio setup for anyone but yourself.

## Docker

```bash
docker build -t ninova-mcp .
docker run --rm -p 8000:8000 \
  -e NINOVA_USERNAME="your_username" \
  -e NINOVA_PASSWORD="your_password" \
  -e NINOVA_REMOTE_MCP_PATH="/mcp-choose-a-long-random-secret" \
  -e NINOVA_PUBLIC_BASE_URL="https://your-public-domain.example.com" \
  -e NINOVA_ALLOWED_HOSTS="your-public-domain.example.com" \
  ninova-mcp
```

## Request model

Ninova is classic ASP.NET WebForms. This server prefers request-level access to Ninova's
own routes such as `/Sinif/<id>.<id>/Notlar`, `/MesajPanosu`, `/Yoklama`, and
`/UzaktanEgitim`. For interactive actions Ninova uses same-page form `POST` requests with
`__VIEWSTATE`, `__EVENTTARGET`, and related WebForms fields rather than a clean JSON API.

## Exposed tools

- `auth_status` — check whether credentials exist and a fresh Ninova session can be created.
- `refresh_session` — force a new login with the configured credentials.
- `get_dashboard` — read `/Kampus1` and summarize sections, recent items, and courses.
- `list_courses` / `get_courses` — return the discovered courses from the dashboard.
- `get_course_announcements` — announcements for a course (code, title, path, or URL).
- `get_course_class_files` — structured entries from `Sınıf Dosyaları`, optionally recursive.
- `get_course_lesson_files` — structured entries from `Ders Dosyaları`, optionally recursive.
- `get_course_assignments` — assignments plus each assignment's detail page.
- `get_course_info` — structured data from `Sınıf Bilgileri`.
- `get_course_sections` — the course routes exposed on the course home page.
- `get_course_grades` — structured data from `Notlar`.
- `get_course_message_board` — topics from `Mesaj Panosu`, optionally with thread details.
- `get_course_attendance` — structured data from `Yoklama`.
- `get_course_remote_learning` — structured data from `Uzaktan Eğitim`.
- `get_course_overview` — combined live or tracked overview of a course.
- `get_dashboard_announcements` — announcements from `/Kampus?1/Duyurular`.
- `get_dashboard_assignments` — assignments from `/Kampus?1/Odevler`, with detail pages.
- `sync_all_courses` — snapshot every visible course and detect changes since last sync.
- `get_updates` — recently detected changes from the local tracking state.
- `get_upcoming_deadlines` — upcoming assignment deadlines from the tracking state.
- `read_page` — read and structure any Ninova HTML page.
- `crawl_course` — inventory internal pages and downloadable resources inside a course.
- `download_resource` — download a file or page response to disk.
- `snapshot_page` — save a structured snapshot of a page for later tracking.
- `diff_snapshot` — compare the current page against a stored snapshot.

### OBS tools (student record, same İTÜ credentials)

The student portal at `obs.itu.edu.tr/ogrenci/` is a SPA backed by a JSON API. The server
signs in through girisv3, collects the OBS session cookies, exchanges them for a bearer
token at `/ogrenci/auth/jwt`, and calls `/api/ogrenci/...` directly. Every OBS tool is
read-only: nothing registers, drops, or edits anything.

- `obs_auth_status` / `obs_refresh_session` — check or force the OBS login.
- `obs_get_profile` — personal details, student number, faculty, department, class level, GPA, advisors, programs.
- `obs_list_semesters` — every semester with its internal id and term code.
- `obs_get_graduation_progress` — `Mezuniyetime Ne Kaldı`: plan requirements, credits earned vs. required, GPA and internship requirements, completed courses, and the courses still missing.
- `obs_get_academic_standing` / `obs_get_semester_status` — semester-by-semester GPA, credits, class level.
- `obs_get_grades` / `obs_get_interim_grades` — final letter grades, and published in-term grades with class mean, standard deviation, rank, and weight.
- `obs_get_course_history` — every graded course across every semester, plus failed courses.
- `obs_get_registered_courses` / `obs_get_schedule` / `obs_get_exam_schedule` — registrations, weekly timetable, and final exam schedule for a semester.
- `obs_get_attendance` — attendance for one registered class.
- `obs_get_registration_status`, `obs_get_internships`, `obs_get_announcements`.
- `obs_get_transcript` — download the official transcript preview PDF (`tr` or `en`). Local only.
- `obs_api_get` — read any `/api/ogrenci/...` endpoint directly. Local only.

Semester arguments accept an internal semester id, a term code such as `202710`, or a name
fragment such as `2026-2027 - Güz`; omit them for the current semester.

### Course-registration planning

Three facts have to be combined before you can tell whether a student may register for a
course, and OBS keeps them in three places: the **course plan** is per cohort,
**prerequisites** are published only for the programme's *current* definitions, and
**equivalences** are per plan and reconcile renamed course codes across cohorts
(e.g. `YZV 231` is now taught as `BBF 201`). These tools combine them:

- `obs_check_prerequisites` — evaluate each course's prerequisite expression (`Ve` = AND, `Veya` = OR) against the student's passed courses, expanded through the equivalence map. Defaults to every course still required for graduation. Turkish/English code variants (`BBF 201` / `BBF 201E`) are collapsed; minimum-credit requirements are checked too.
- `obs_get_registration_options` — for every remaining plan course: which codes satisfy it, whether any is offered this term (CRNs, times), and whether prerequisites are met. The single call for registration planning.
- `obs_get_elective_pool` / `obs_get_elective_options` — the pool for an elective slot, and for every unfilled slot which pool courses run this term with prerequisites checked.
- `obs_project_gpa` — cumulative GPA under hypothetical grades such as `["YZV 201E:CC"]`, modelling İTÜ's last-attempt rule and course equivalences.

`obs_check_prerequisites`, `obs_get_registration_options`, and `obs_get_elective_options`
accept `assume_passed` for courses passed in a term OBS has not posted yet
(`["YZV 201E"]`, or `["YZV 201E:BB"]` to name the grade; the default assumption is `DD`).
Assumed passes are echoed back in the response.

### Public OBS tools (no login)

`https://obs.itu.edu.tr/public/DersProgram` publishes every section of the upcoming term
and needs no session, so these use an anonymous client.

- `obs_public_active_term` — the term whose schedule is published (normally the *upcoming* one), read from the page's hidden `#baslik1` heading.
- `obs_public_registration_term` — the term the registration system still reports as current; routinely lags the published schedule.
- `obs_public_list_branches` — every branch code (`BLG`, `YZV`, `MAT`, ...) for a program level.
- `obs_public_get_schedule` — every section of a branch: CRN, meeting days and times, instructor, building, room, quota, remaining seats; filter by course, day, instructor, or availability.
- `obs_public_find_courses` — several courses at once by code.
- `obs_public_get_prerequisites` — published prerequisite expressions and minimum credits for a branch.
- `obs_public_list_plans` / `obs_public_get_equivalences` — course plans of a programme, and which courses count in place of a plan course.
- `obs_public_check_conflicts` — check a candidate set of `{code, crn}` selections for time clashes and return the weekly timetable.

### Hocametre tools (notkutusu.com, no login)

notkutusu.com's "hocametre" is anonymous student rating of instructors on five criteria
(note sharing, helpfulness, homework load, attendance strictness, teaching skills), each
1–5 with its own vote count. All read endpoints of its API are public, so no account is
used and nothing about the user is sent there. Voting and commenting are not implemented.

- `hocametre_search_instructors` — search profiles by (partial) name.
- `hocametre_get_instructor` — one profile's ratings and newest comments, by slug.
- `hocametre_lookup_instructors` — resolve several names (as printed in the OBS schedule) to ratings. Returns every matching profile plus a vote-weighted `combined` score, because the same instructor is often split across duplicate or misspelled profiles.
- `hocametre_rate_course_sections` — every published section of the given courses with its instructor's ratings attached, for comparing sections of one course by instructor.

These are unofficial, self-selected student opinions; treat them as one input, not a fact.

## Notes

- Sessions are created by the normal login flow and held in process memory; the server
  retries once with a fresh login if a session expires.
- Downloads default to `~/.ninova_state/downloads`; snapshots and tracking state live under
  `~/.ninova_state/` unless `NINOVA_STATE_DIR` is set.
- The remote HTTP entrypoint uses the official Python MCP SDK's Streamable HTTP support.

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## Building the .mcpb bundle

See [installation.md](installation.md#building-the-mcpb-bundle-maintainers).
