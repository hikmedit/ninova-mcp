# Security Notes

This project logs in to ITU Ninova and ITU OBS with the username and password supplied by the user. Both sit behind İTÜ's own `girisv3.itu.edu.tr` single sign-on, and that is the only place the password is ever sent.

All OBS access is read-only. The course-registration, draft, contact-edit, and graduation-ceremony endpoints of OBS are deliberately not exposed, so the server cannot register or drop a course or change anything in the student record.

The instructor ratings ("hocametre") are read from notkutusu.com's public API, which requires no login. No notkutusu account is used, and no credential or personal data is sent to notkutusu. Note that notkutusu has its own, separate account system: never reuse your İTÜ password there.

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
