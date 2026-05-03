# Installation Guide

Ninova MCP is a local MCP server that lets AI assistants read your own ITU Ninova account through the normal username/password login flow.

## 1. Requirements

- Python 3.11 or newer (`python3 --version` on macOS/Linux, `python --version` on Windows)
- An MCP-compatible client (Claude Desktop, Claude Code, Cursor, Codex CLI, OpenClaw, etc.)

## 2. Install

### Option A: pipx (recommended)

[pipx](https://pipx.pypa.io) installs Python CLI tools in isolated environments and exposes their commands on your PATH globally.

```bash
pipx install ninova-mcp
```

If you do not have pipx yet:

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
```

On Windows, replace `python3` with `python` or `py -3`.

### Option B: uv

[uv](https://docs.astral.sh/uv/) is a fast Python package manager.

```bash
uv tool install ninova-mcp
```

### Option C: pip

```bash
pip install ninova-mcp
```

### Option D: from source

```bash
git clone https://github.com/hikmedit/ninova-mcp.git
cd ninova-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

After any option, `ninova-mcp` should be available on your PATH:

```bash
ninova-mcp --help    # not implemented; the command starts the stdio server
which ninova-mcp     # macOS / Linux
where ninova-mcp     # Windows
```

## 3. Smoke test

Run the server directly:

```bash
ninova-mcp
```

It is normal for it to wait silently because your AI client talks to it over stdin/stdout. Press Ctrl+C to exit.

## 4. Configure your MCP client

The credentials live in the client's MCP config so the server is launched with them as environment variables. You do not need a `.env` file unless you prefer one.

### Claude Desktop / Claude Code

Add this to your Claude MCP config:

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

### Cursor

Create or edit `.cursor/mcp.json` in your project:

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

Restart Cursor, then ask it to use the Ninova tools.

### Codex CLI

Add this to your Codex config, usually `~/.codex/config.toml`:

```toml
[mcp_servers.ninova]
command = "ninova-mcp"

[mcp_servers.ninova.env]
NINOVA_USERNAME = "your_itu_username"
NINOVA_PASSWORD = "your_itu_password"
```

### OpenClaw

OpenClaw can store MCP servers in its MCP registry:

```bash
openclaw mcp set ninova --json '{
  "command": "ninova-mcp",
  "env": {
    "NINOVA_USERNAME": "your_itu_username",
    "NINOVA_PASSWORD": "your_itu_password"
  }
}'
```

Then verify:

```bash
openclaw mcp show ninova
```

## 5. Verify the connection

In your MCP client, ask the model to call the `auth_status` tool. It should report that credentials are present and a Ninova session can be created.

## Troubleshooting

- **`ninova-mcp: command not found`**: Run `pipx ensurepath` (or restart the shell). If you used a venv, point the MCP config to its absolute script path: `/path/to/.venv/bin/ninova-mcp` (or `\.venv\Scripts\ninova-mcp.exe` on Windows).
- **Login fails**: Confirm your credentials are correct and that you can sign in to https://ninova.itu.edu.tr in a browser. If Ninova changes its login form, install the optional Playwright fallback: `pipx install "ninova-mcp[playwright]"` and run `playwright install chromium`.
- **Want a `.env` file instead of putting credentials in the MCP config**: Create a `.env` in your client's working directory with `NINOVA_USERNAME=...` and `NINOVA_PASSWORD=...`. The server auto-loads it.

## Remote Claude.ai custom connector

Claude.ai custom connectors require a publicly reachable HTTP MCP server. Run the remote transport only on infrastructure you control:

```bash
export NINOVA_USERNAME="your_itu_username"
export NINOVA_PASSWORD="your_itu_password"
export NINOVA_REMOTE_MCP_PATH="/mcp-choose-a-long-random-secret"
export NINOVA_PUBLIC_BASE_URL="https://your-domain.example.com"
export NINOVA_ALLOWED_HOSTS="your-domain.example.com"
export NINOVA_ALLOWED_ORIGINS="https://claude.ai,https://claude.com"
ninova-mcp-remote
```

Connector URL:

```text
https://your-domain.example.com/mcp-choose-a-long-random-secret
```

Important: this project does not implement OAuth yet. Keep the remote URL private and use a long random path.
