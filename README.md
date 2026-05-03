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

You need Python 3.11+.

### Step 1: Install the server

Pick one:

```bash
pipx install ninova-mcp        # recommended
uv tool install ninova-mcp     # if you use uv
pip install --user ninova-mcp  # plain pip
```

All three give you a `ninova-mcp` command on your PATH. Verify with `ninova-mcp` (it will wait silently — that's the stdio server running; press Ctrl+C).

### Step 2: Tell your MCP client about it

Open your client's MCP config file (paths below) and add the `ninova` entry. If `mcpServers` already exists, merge — do not replace it.

**Claude Desktop**

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "ninova": {
      "command": "ninova-mcp",
      "env": {
        "NINOVA_USERNAME": "your_itu_username",
        "NINOVA_PASSWORD": "your_itu_password"
      }
    }
  }
}
```

**Claude Code** — one shell command, no manual editing:

```bash
claude mcp add ninova ninova-mcp \
  -e NINOVA_USERNAME=your_itu_username \
  -e NINOVA_PASSWORD=your_itu_password
```

**Cursor**

- Per project: `.cursor/mcp.json` in the repo root
- Global: `~/.cursor/mcp.json`

Use the same JSON block as Claude Desktop above.

**Codex CLI** — `~/.codex/config.toml`:

```toml
[mcp_servers.ninova]
command = "ninova-mcp"

[mcp_servers.ninova.env]
NINOVA_USERNAME = "your_itu_username"
NINOVA_PASSWORD = "your_itu_password"
```

### Step 3: Restart the client and try it

Restart your MCP client. Ask the model to call `auth_status`. It should report that credentials are present and a Ninova session works.

---

### Installing via your AI assistant (copy-paste prompt)

If you'd rather have ChatGPT, Claude, Cursor, or any AI assistant set this up for you, paste the prompt below into the chat. It walks the model through detecting your OS, installing pipx + ninova-mcp, and writing the right config file.

````text
Install the Ninova MCP server on my machine. Project: https://github.com/hikmedit/ninova-mcp · PyPI: https://pypi.org/project/ninova-mcp/

Do this:

1. Ask me my OS (macOS / Windows / Linux) and which MCP client I use
   (Claude Desktop, Claude Code, Cursor, Codex CLI). If I already told you, skip.

2. Make sure Python 3.11+ is installed. If not, tell me how to install it and stop.

3. Make sure pipx is installed. If not:
   - macOS / Linux: `python3 -m pip install --user pipx && python3 -m pipx ensurepath`
   - Windows: `python -m pip install --user pipx && python -m pipx ensurepath`

4. Run: `pipx install ninova-mcp`

5. Ask me for my ITU Ninova username and password. Use them only to write
   the MCP config below — do not echo, log, or paste them back to me.

6. Write or update the right config file for my client. If `mcpServers`
   (or `[mcp_servers]`) already exists in the file, MERGE the `ninova`
   entry into it; do not replace existing servers.

   Config locations:
   - Claude Desktop macOS:   ~/Library/Application Support/Claude/claude_desktop_config.json
   - Claude Desktop Windows: %APPDATA%\Claude\claude_desktop_config.json
   - Claude Desktop Linux:   ~/.config/Claude/claude_desktop_config.json
   - Claude Code: run `claude mcp add ninova ninova-mcp -e NINOVA_USERNAME=<user> -e NINOVA_PASSWORD=<pass>`
   - Cursor (project): .cursor/mcp.json
   - Cursor (global):  ~/.cursor/mcp.json
   - Codex CLI:        ~/.codex/config.toml

   JSON block to add (TOML form for Codex):
   {
     "mcpServers": {
       "ninova": {
         "command": "ninova-mcp",
         "env": {
           "NINOVA_USERNAME": "<my_username>",
           "NINOVA_PASSWORD": "<my_password>"
         }
       }
     }
   }

7. Tell me to restart the client.

8. After restart, tell me how to verify by calling the `auth_status` tool.

If you cannot edit files directly, just print the exact commands and the
exact JSON I should paste, with the right config path for my OS + client.
````

Supported priority clients: Claude Desktop / Claude Code, Cursor, Codex CLI, OpenClaw.

See also: [Installation guide](docs/installation.md) · [Security notes](docs/security.md) · [Example configs](examples/)

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

Only two variables are required:

```dotenv
NINOVA_USERNAME=your_username
NINOVA_PASSWORD=your_password
```

The server reads them from the process environment. If a `.env` file exists in the current working directory (or any parent), `NINOVA_*` variables are loaded from it without overriding variables already set in the environment.

### Advanced (optional)

These are not needed for normal local use. Set them only if you need to override defaults or run the remote HTTP transport.

Local overrides:

```bash
export NINOVA_BASE_URL="https://ninova.itu.edu.tr"   # default
export NINOVA_STATE_DIR="/absolute/path/to/.ninova_state"
export NINOVA_DISABLE_PLAYWRIGHT_FALLBACK="1"
export NINOVA_ENV_FILE="/absolute/path/to/.env"
```

Remote HTTP server (only for self-hosted Claude.ai custom connectors):

```bash
export NINOVA_REMOTE_HOST="0.0.0.0"
export NINOVA_REMOTE_PORT="8000"
export NINOVA_REMOTE_MCP_PATH="/mcp-choose-a-long-random-secret"
export NINOVA_PUBLIC_BASE_URL="https://your-public-domain.example.com"
export NINOVA_ALLOWED_HOSTS="your-public-domain.example.com"
export NINOVA_ALLOWED_ORIGINS="https://claude.ai,https://claude.com"
```

## Run

After installing the package:

```bash
ninova-mcp
```

This starts the stdio MCP server. It is normal for it to wait silently because your MCP client talks to it over stdin/stdout.

To run from a source checkout without installing:

```bash
PYTHONPATH=src python3 -m ninova_mcp
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

After `pipx install ninova-mcp`, the minimal local stdio entry is:

```json
{
  "mcpServers": {
    "ninova": {
      "command": "ninova-mcp",
      "env": {
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
