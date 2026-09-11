# Ninova MCP

Connect your **İTÜ Ninova and OBS** accounts to AI assistants like Claude. Ask about your courses, announcements, assignments, grades, files, attendance, and upcoming deadlines — and about your transcript, GPA, what is left until graduation, and which courses you can register for next term — in plain language. The assistant reads Ninova and OBS for you.

It logs in with your own İTÜ username and password, opens its own temporary sessions, and never touches your browser or sends your password anywhere except İTÜ's own login (`ninova.itu.edu.tr` and `obs.itu.edu.tr` through `girisv3.itu.edu.tr`).

## What you can ask

- *"Bu hafta hangi ödevlerimin teslimi var?"*
- *"X dersinde yeni duyuru veya ders dosyası var mı?"*
- *"Notlarımı ve ağırlıklı ortalamamı göster."*
- *"Tüm derslerimdeki son değişiklikleri özetle."*
- *"Mezuniyete kaç kredim kaldı, hangi dersler eksik?"*
- *"Gelecek dönem hangi dersleri alabilirim? Önşartlarım tutuyor mu?"*
- *"Şu CRN'lerle çakışmasız bir program çıkar."*
- *"Veritabanı dersinin şubelerini hocametre puanlarına göre karşılaştır."*
- *"Bu dönem hepsinden BB alırsam ortalamam ne olur?"*

## Install — pick one

### 1. Easiest: Claude Desktop, one click (no Python, no terminal)

1. Download your platform's file from the **[latest release](https://github.com/hikmedit/ninova-mcp/releases/latest)**:
   - macOS (Apple Silicon / M1–M4): `ninova-mcp-*-darwin-arm64.mcpb`
   - Windows: `ninova-mcp-*-windows-amd64.mcpb`
2. **Double-click the file.** Claude Desktop opens an install dialog.
3. Enter your **İTÜ username and password**, click **Install**. Done.

The bundle ships its own Python runtime, so there is nothing else to install. Your password is stored in your operating system's secure keychain.

### 2. Let your AI set it up (Claude Code, Cursor, Codex, and others)

Paste this to your AI assistant — it installs the server and configures your client end-to-end:

```text
Install the ninova-mcp MCP server (PyPI: ninova-mcp, https://github.com/hikmedit/ninova-mcp).
1. Install it: `pipx install ninova-mcp` (or `pip install --user ninova-mcp`) — both put a
   `ninova-mcp` command on my PATH.
2. Register a `ninova` MCP server (command `ninova-mcp`, env NINOVA_USERNAME and
   NINOVA_PASSWORD) in whichever MCP client I use — detect it and edit the right config,
   merging into any existing servers without overwriting them. Leave the credentials as
   placeholders unless I already pasted them here.
3. Tell me to fill in my İTÜ credentials, restart the client, and call the `auth_status`
   tool to verify.
```

### 3. One command (if you prefer the terminal)

After `pipx install ninova-mcp`:

```bash
# Claude Code
claude mcp add ninova ninova-mcp -e NINOVA_USERNAME=itu_username -e NINOVA_PASSWORD=itu_password

# Codex CLI
codex mcp add ninova --env NINOVA_USERNAME=itu_username --env NINOVA_PASSWORD=itu_password -- ninova-mcp
```

Other clients (Claude Desktop config file, Cursor, manual TOML) are in the **[installation guide](docs/installation.md)**.

To confirm it works, ask the assistant to run the `auth_status` tool.

## Is it safe?

Yes — it runs entirely on your machine. Your İTÜ password stays local (in your OS keychain when installed as the extension) and is only ever sent to İTÜ's own single sign-on for `ninova.itu.edu.tr` and `obs.itu.edu.tr`. Nothing is uploaded to any third-party server, and it never reads your browser cookies. Everything is read-only: it cannot register you for a course, drop one, or change anything in OBS. The instructor ratings come from notkutusu.com's public "hocametre" pages, which need no login, so nothing about you is sent there either. Details: [docs/security.md](docs/security.md).

## What it can do

**Ninova.** Reads your dashboard and course list, announcements, class and lesson files, assignments (with detail pages and deadlines), grades, message boards, attendance, and remote-learning sessions — plus a combined per-course overview. It can also sync all courses, track what changed since last time, and list upcoming deadlines.

**OBS.** Reads your profile, semester history and GPA, final and in-term grades, the full course history, registered courses, weekly and exam schedules, internships, announcements, "Mezuniyetime Ne Kaldı" (graduation progress), and the official transcript PDF.

**Course-registration planning.** Reads the public OBS course schedule of the upcoming term (every section with CRN, day/time, instructor, room, quota, and remaining seats — no login needed), evaluates each course's published prerequisites against *your* passed courses through your cohort's course-plan equivalences (so renamed course codes are matched correctly), lists which required and elective courses you can actually take this term, checks a candidate CRN set for time clashes, and projects your GPA under hypothetical grades.

**Hocametre.** Looks up instructors on notkutusu.com's anonymous student ratings (note sharing, helpfulness, homework load, attendance strictness, teaching) and attaches them to the sections of a course so you can compare instructors before picking a CRN. Duplicate profiles of the same instructor are merged with vote-weighted averages.

Full tool reference and self-hosting (remote HTTP server for ChatGPT / Claude.ai connectors, Docker, environment variables, running from source): **[docs/advanced.md](docs/advanced.md)**.

## License

[MIT](LICENSE). Not affiliated with İTÜ; use with your own account.
