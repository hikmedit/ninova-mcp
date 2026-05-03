# Security Notes

This project logs in to ITU Ninova with the username and password supplied by the user.

## Do

- Use only your own Ninova account.
- Keep `.env` private.
- Prefer local stdio MCP for Claude Desktop, Claude Code, Cursor, Codex, and OpenClaw.
- If you expose the remote HTTP transport, use HTTPS, a long random MCP path, and a private deployment.
- Rotate your Ninova password if you accidentally commit or share credentials.

## Do not

- Commit `.env`, cookies, downloaded course files, submissions, screenshots, or state folders.
- Deploy a public remote server with a predictable MCP path.
- Share your remote MCP URL publicly.
- Use this to access accounts, courses, or files you are not authorized to access.

## Data stored locally

Depending on the tools you call, the server may create:

- `~/.ninova_state/` for snapshots, tracking state, and downloads (override with `NINOVA_STATE_DIR`)
- any explicit output directory you request through tools

These paths are ignored by git by default.
