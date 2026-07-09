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
current working directory (or any parent), `NINOVA_*` variables are loaded from it
without overriding variables already set in the environment.

### Optional overrides

```bash
export NINOVA_BASE_URL="https://ninova.itu.edu.tr"   # default
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
