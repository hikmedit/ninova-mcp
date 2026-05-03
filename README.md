# Ninova MCP

Ninova MCP is a credential-based Model Context Protocol server for ITU Ninova. It lets MCP-compatible AI clients such as Claude, OpenClaw, Cursor, and Codex read your own Ninova courses, announcements, files, assignments, grades, message boards, attendance, and deadlines.

It logs in with `NINOVA_USERNAME` and `NINOVA_PASSWORD`, opens its own temporary Ninova session, and exposes tools for:

- reading the dashboard
- listing courses
- resolving a course by code, title, path, or URL
- listing a course's direct Ninova sections/routes
- getting a course's announcements
- getting a course's class files
- getting a course's lesson files
- getting a course's assignments with detail pages
- getting a course's class information
- getting a course's grades
- getting a course's message board topics and threads
- getting a course's attendance
- getting a course's remote learning page
- building a combined course overview
- getting dashboard announcements
- getting dashboard assignments with detail pages
- reading any Ninova page
- crawling a course tree
- downloading files
- snapshotting pages
- comparing snapshots to track changes
- syncing all courses into a local tracking state
- listing detected updates
- listing upcoming deadlines

It does not read Chrome cookies or reuse browser profiles.

## Quick Start

```bash
git clone https://github.com/hikmedit/ninova-mcp.git
cd ninova-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

Edit `.env` with your own Ninova credentials:

```dotenv
NINOVA_USERNAME=your_itu_username
NINOVA_PASSWORD=your_itu_password
```

Then add the server to your MCP client. See the full guide:

- [Installation guide](docs/installation.md)
- [Security notes](docs/security.md)
- [Example configs](examples/)

Supported priority clients:

- Claude Desktop / Claude Code
- OpenClaw
- Cursor
- Codex CLI

## Security Notice

This project uses credentials that you provide locally. Never commit `.env`, downloads, course files, submissions, screenshots, or tracking state. Prefer local stdio MCP. If you deploy the remote HTTP server for Claude.ai custom connectors, use HTTPS and a long random private MCP path.

## Request Model

Ninova appears to be classic ASP.NET WebForms. In practice this server now prefers request-level access to Ninova's own routes such as:

- `/Sinif/<id>.<id>/Notlar`
- `/Sinif/<id>.<id>/MesajPanosu`
- `/Sinif/<id>.<id>/Yoklama`
- `/Sinif/<id>.<id>/UzaktanEgitim`

For interactive actions Ninova uses same-page form `POST` requests with `__VIEWSTATE`, `__EVENTTARGET`, and related WebForms fields rather than a clean public JSON API.

## Requirements

- Python 3.11+
- `requests`
- `beautifulsoup4`
- `lxml`
- `mcp`
- `uvicorn`
- Optional: `playwright` for login fallback when the form flow changes

## Configuration

Set credentials in the environment before starting the server:

```bash
export NINOVA_USERNAME="your_username"
export NINOVA_PASSWORD="your_password"
```

If a `.env` file exists in the current working directory or one of its parents, the server will load `NINOVA_*` variables from there automatically without overriding variables that are already set in the process environment:

```dotenv
NINOVA_USERNAME=your_username
NINOVA_PASSWORD=your_password
```

Optional environment variables:

```bash
export NINOVA_BASE_URL="https://ninova.itu.edu.tr"
export NINOVA_STATE_DIR="/absolute/path/to/.ninova_state"
export NINOVA_DISABLE_PLAYWRIGHT_FALLBACK="1"
export NINOVA_ENV_FILE="/absolute/path/to/.env"

# Remote HTTP server settings for Claude Cowork / Claude.ai connectors
export NINOVA_REMOTE_HOST="0.0.0.0"
export NINOVA_REMOTE_PORT="8000"
export NINOVA_REMOTE_MCP_PATH="/mcp-choose-a-long-random-secret"
export NINOVA_PUBLIC_BASE_URL="https://your-public-domain.example.com"
export NINOVA_ALLOWED_HOSTS="your-public-domain.example.com"
export NINOVA_ALLOWED_ORIGINS="https://claude.ai,https://claude.com"
```

## Run

From the repository root:

```bash
PYTHONPATH=src python3 -m ninova_mcp
```

Or, after installing the package:

```bash
ninova-mcp
```

## Run Remote HTTP Server

To expose the server for Claude Cowork / Claude.ai, run the remote Streamable HTTP transport:

```bash
ninova-mcp-remote
```

By default this starts an HTTP server on `0.0.0.0:8000`.

Useful endpoints:

- `GET /healthz`: health check
- `POST/GET <NINOVA_REMOTE_MCP_PATH>`: MCP endpoint for remote connectors

Example:

```bash
export NINOVA_REMOTE_MCP_PATH="/mcp-6f8f94c5b6bb4b8f8f6d2f8b20f28e9f"
export NINOVA_PUBLIC_BASE_URL="https://ninova-mcp.example.com"
export NINOVA_ALLOWED_HOSTS="ninova-mcp.example.com"
export NINOVA_ALLOWED_ORIGINS="https://claude.ai,https://claude.com"
ninova-mcp-remote
```

Then your connector URL is:

```text
https://ninova-mcp.example.com/mcp-6f8f94c5b6bb4b8f8f6d2f8b20f28e9f
```

This server currently uses an authless remote MCP setup, so for personal use you should choose a long random `NINOVA_REMOTE_MCP_PATH` and keep the full URL private. OAuth is not implemented yet.

## Docker

Build and run:

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

## Claude Cowork / Claude.ai

Anthropic's remote MCP support requires a publicly reachable HTTP server; local stdio servers are not available in Cowork.

To use this connector in Claude Cowork:

1. Deploy this repo to a public host.
2. Set `NINOVA_USERNAME`, `NINOVA_PASSWORD`, `NINOVA_REMOTE_MCP_PATH`, `NINOVA_PUBLIC_BASE_URL`, and `NINOVA_ALLOWED_HOSTS`.
3. Confirm `https://your-domain/healthz` returns `200`.
4. In Claude, add a custom connector and paste the full remote MCP URL:
   `https://your-domain<your-random-mcp-path>`
5. Enable the tools you want to expose.

Anthropic references:

- Remote connectors overview: [Anthropic Help](https://support.anthropic.com/en/articles/11175166-about-custom-integrations-using-remote-mcp)
- Remote MCP transport support: [Anthropic Help](https://support.anthropic.com/en/articles/11503834-building-custom-connectors-via-remote-mcp-servers)
- Streamable HTTP requirement: [Anthropic MCP connector docs](https://docs.anthropic.com/en/docs/agents-and-tools/mcp-connector)

## Example MCP Config

Example local stdio entry:

```json
{
  "mcpServers": {
    "ninova": {
      "command": "python3",
      "args": ["-m", "ninova_mcp"],
      "cwd": "/Users/hikmedit/ninova-mcp",
      "env": {
        "PYTHONPATH": "/Users/hikmedit/ninova-mcp/src",
        "NINOVA_USERNAME": "your_username",
        "NINOVA_PASSWORD": "your_password"
      }
    }
  }
}
```

## Exposed Tools

- `auth_status`: Check whether credentials exist and whether a fresh Ninova session can be created.
- `refresh_session`: Force a new login with the configured credentials.
- `get_dashboard`: Read `/Kampus1` and return dashboard sections, recent items, and course list.
- `list_courses`: Return the discovered courses from the dashboard.
- `get_courses`: Alias for `list_courses`.
- `get_course_announcements`: Return announcements for a given course. Accepts course code, title, path, or full URL.
- `get_course_class_files`: Return structured entries from `Sınıf Dosyaları`, optionally recursively.
- `get_course_lesson_files`: Return structured entries from `Ders Dosyaları`, optionally recursively.
- `get_course_assignments`: Return assignments for a course and fetch each assignment's detail page.
- `get_course_info`: Return structured data from `Sınıf Bilgileri`.
- `get_course_sections`: Return the course routes exposed on the course home page.
- `get_course_grades`: Return structured data from `Notlar`.
- `get_course_message_board`: Return structured topics from `Mesaj Panosu`, optionally with thread details.
- `get_course_attendance`: Return structured data from `Yoklama`.
- `get_course_remote_learning`: Return structured data from `Uzaktan Eğitim`.
- `get_course_overview`: Return a combined live or tracked overview of a course.
- `get_dashboard_announcements`: Return the announcements listed in `/Kampus?1/Duyurular`.
- `get_dashboard_assignments`: Return the assignments listed in `/Kampus?1/Odevler` and fetch each detail page.
- `sync_all_courses`: Save a local snapshot for every visible course and detect changes since the previous sync.
- `get_updates`: Return recently detected changes from the local tracking state.
- `get_upcoming_deadlines`: Return upcoming assignment deadlines from the local tracking state.
- `read_page`: Read and structure any Ninova HTML page.
- `crawl_course`: Inventory internal pages and downloadable resources inside a course.
- `download_resource`: Download a file or page response to disk.
- `snapshot_page`: Save a structured snapshot of a page for later tracking.
- `diff_snapshot`: Compare the current page against a stored snapshot.

## Notes

- Sessions are created by normal login flow and held in process memory.
- The server automatically retries once with a fresh login if a session expires.
- Downloaded files are stored inside the requested output directory. The default is `./downloads`.
- Snapshots are stored under `./.ninova_state/snapshots` unless `NINOVA_STATE_DIR` is set.
- Course tracking state is stored under `./.ninova_state/tracking-state.json` unless `NINOVA_STATE_DIR` is set.
- The remote HTTP entrypoint is implemented with the official Python MCP SDK's Streamable HTTP server support.

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
