# Installation Guide

Ninova MCP is a local MCP server that lets AI assistants read your own ITU Ninova account through the normal username/password login flow.

## 1. Install

### Option A: from GitHub

```bash
git clone https://github.com/hikmedit/ninova-mcp.git
cd ninova-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Option B: with uv

```bash
git clone https://github.com/hikmedit/ninova-mcp.git
cd ninova-mcp
uv venv
source .venv/bin/activate
uv pip install -e .
```

## 2. Configure credentials

Create a `.env` file in the repo root:

```bash
cp .env.example .env
```

Then edit `.env`:

```dotenv
NINOVA_USERNAME=your_itu_username
NINOVA_PASSWORD=your_itu_password
```

The real `.env` file is ignored by git. Do not share your credentials.

## 3. Smoke test

```bash
ninova-mcp
```

The command starts a stdio MCP server. It is normal for it to wait silently because your AI client talks to it over stdin/stdout.

For a quick client-side check, add it to one of the MCP clients below and call the `auth_status` tool.

## Claude Desktop / Claude Code

Add this to your Claude MCP config, replacing the path with your local clone path:

```json
{
  "mcpServers": {
    "ninova": {
      "command": "python3",
      "args": ["-m", "ninova_mcp"],
      "cwd": "/absolute/path/to/ninova-mcp",
      "env": {
        "PYTHONPATH": "/absolute/path/to/ninova-mcp/src",
        "NINOVA_USERNAME": "your_itu_username",
        "NINOVA_PASSWORD": "your_itu_password"
      }
    }
  }
}
```

If you installed with `pip install -e .`, you can also use:

```json
{
  "mcpServers": {
    "ninova": {
      "command": "ninova-mcp",
      "cwd": "/absolute/path/to/ninova-mcp",
      "env": {
        "NINOVA_USERNAME": "your_itu_username",
        "NINOVA_PASSWORD": "your_itu_password"
      }
    }
  }
}
```

## Cursor

Create or edit `.cursor/mcp.json` in your project:

```json
{
  "mcpServers": {
    "ninova": {
      "command": "python3",
      "args": ["-m", "ninova_mcp"],
      "cwd": "/absolute/path/to/ninova-mcp",
      "env": {
        "PYTHONPATH": "/absolute/path/to/ninova-mcp/src",
        "NINOVA_USERNAME": "your_itu_username",
        "NINOVA_PASSWORD": "your_itu_password"
      }
    }
  }
}
```

Restart Cursor, then ask it to use the Ninova tools.

## Codex CLI

Add this to your Codex config, usually `~/.codex/config.toml`:

```toml
[mcp_servers.ninova]
command = "python3"
args = ["-m", "ninova_mcp"]
cwd = "/absolute/path/to/ninova-mcp"

[mcp_servers.ninova.env]
PYTHONPATH = "/absolute/path/to/ninova-mcp/src"
NINOVA_USERNAME = "your_itu_username"
NINOVA_PASSWORD = "your_itu_password"
```

## OpenClaw

OpenClaw can store MCP servers in its MCP registry:

```bash
openclaw mcp set ninova --json '{
  "command": "python3",
  "args": ["-m", "ninova_mcp"],
  "cwd": "/absolute/path/to/ninova-mcp",
  "env": {
    "PYTHONPATH": "/absolute/path/to/ninova-mcp/src",
    "NINOVA_USERNAME": "your_itu_username",
    "NINOVA_PASSWORD": "your_itu_password"
  }
}'
```

Then verify:

```bash
openclaw mcp show ninova
```

If your OpenClaw setup uses a JSON/JSON5 config directly, the equivalent shape is:

```json5
{
  mcp: {
    servers: {
      ninova: {
        command: "python3",
        args: ["-m", "ninova_mcp"],
        cwd: "/absolute/path/to/ninova-mcp",
        env: {
          PYTHONPATH: "/absolute/path/to/ninova-mcp/src",
          NINOVA_USERNAME: "your_itu_username",
          NINOVA_PASSWORD: "your_itu_password",
        },
      },
    },
  },
}
```

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
