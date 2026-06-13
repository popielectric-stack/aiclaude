# AI DevOps & Coding Agent

A single, long-lived Python 3.11+ process that runs continuously on an Ubuntu
VPS and is controlled exclusively by one Telegram user (the Owner). The Owner
sends natural-language instructions (in **Indonesian** or **English**); the
agent forwards them to a Claude Opus 4.8 model exposed through an
OpenAI-compatible endpoint, then carries out the resulting plan by driving an
internal **agent loop** that selects and executes tools (files, terminal, Git,
SSH, Pterodactyl, cPanel, Docker, browser automation, and deployment).

The agent keeps persistent memory (conversations, registered servers, projects,
and task history) in SQLite, logs all activity under `logs/`, enforces
owner-only access, encrypts stored credentials, and asks for explicit
confirmation before destructive actions.

## Requirements

- Python **3.11 or later** (startup halts on older runtimes).
- An Ubuntu VPS (designed for 4 GB RAM / 2 vCPU).
- A Telegram bot token and your Telegram user id.
- An OpenAI-compatible model endpoint (optional — without it, slash commands
  still work but natural-language instructions are disabled).

## Environment Variables

All runtime configuration is supplied through environment variables. Absent,
empty, or whitespace-only values are treated as **not provided**.

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | Telegram Bot API token. If missing, startup is aborted before the bot connects. |
| `OWNER_ID` | Yes (for control) | Your numeric Telegram user id. Only this user may control the agent; if absent, all messages are denied. |
| `API_KEY` | For LLM | API key used to authenticate requests to the model endpoint. |
| `BASE_URL` | For LLM | Base URL of the OpenAI-compatible endpoint. |
| `MODEL` | For LLM | Model identifier to request (e.g. the Claude Opus 4.8 deployment name). |
| `ENCRYPTION_SECRET` | Recommended | Secret used to derive the credential-encryption key (Fernet). If unset, a key is derived from `TELEGRAM_BOT_TOKEN`; set an explicit value for stable, independent credential storage. |

If any of `API_KEY`, `BASE_URL`, or `MODEL` is not provided, the agent starts
with language-model features **disabled**: it logs which variables are missing,
and replies to natural-language instructions that the model is unavailable while
keeping slash commands working.

Optional variables for external panels (used by the corresponding tools):

| Variable | Description |
|----------|-------------|
| `PTERODACTYL_BASE_URL`, `PTERODACTYL_API_KEY` | Pterodactyl panel endpoint and API key. |
| `CPANEL_BASE_URL`, `CPANEL_USERNAME`, `CPANEL_API_TOKEN` | cPanel endpoint, username, and API token. |
| `AGENT_DB_PATH` | SQLite database path (defaults to `agent.db`). |

## Installation

```bash
# 1. Create and activate a virtual environment.
python3.11 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies.
pip install -r requirements.txt

# 3. Install the Playwright browser binaries (required for browser automation).
playwright install
# On a fresh Ubuntu host you may also need the system libraries:
playwright install-deps
```

## Running

Export the configuration and launch the agent through `main.py`:

```bash
export TELEGRAM_BOT_TOKEN="123456:your-telegram-token"
export OWNER_ID="123456789"
export API_KEY="your-api-key"
export BASE_URL="https://your-endpoint.example/v1"
export MODEL="claude-opus-4.8"
export ENCRYPTION_SECRET="a-long-random-secret"

python -m agent.main
```

### Running as a systemd service on an Ubuntu VPS

To run the agent continuously (24/7) and restart it automatically, create a
systemd unit at `/etc/systemd/system/ai-devops-agent.service`:

```ini
[Unit]
Description=AI DevOps & Coding Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=agent
WorkingDirectory=/opt/ai-devops-coding-agent
Environment=TELEGRAM_BOT_TOKEN=123456:your-telegram-token
Environment=OWNER_ID=123456789
Environment=API_KEY=your-api-key
Environment=BASE_URL=https://your-endpoint.example/v1
Environment=MODEL=claude-opus-4.8
Environment=ENCRYPTION_SECRET=a-long-random-secret
ExecStart=/opt/ai-devops-coding-agent/.venv/bin/python -m agent.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then enable and start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ai-devops-agent.service
sudo journalctl -u ai-devops-agent.service -f
```

The agent maintains its Telegram connection, logging a timestamped reconnection
record and retrying at intervals of at most 10 seconds if the connection drops.

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Show the help message listing all available commands. |
| `/status` | Show uptime, registered-server count, project count, and in-progress tasks. |
| `/memory` | Show the 10 most recent conversations and tasks. |
| `/clear` | Delete the stored conversation history (servers, projects, and task history are retained). |
| `/projects` | List the stored projects. |
| `/servers` | List the registered servers (credential secrets are never shown). |
| `/deploy <id>` | Begin a deployment task for the stored project with the given identifier. |
| `/logs` | Show the 20 most recent activity log records. |

Any other text is treated as a natural-language instruction and routed to the
model (when language-model features are enabled). Destructive actions
(deleting data, dropping databases, stopping/restarting services, overwriting
files) require an explicit `yes`/`no` confirmation in chat.

## Project Structure

```
agent/
  main.py            # startup, configuration loading, and component wiring
  config.py          # Pydantic-validated configuration loader (Config_Loader)
  logger.py          # file-based activity Logger (writes under logs/)
  models.py          # shared data models (ToolResult, Decision, Task)
  security.py        # Security_Manager: access control, crypto, confirmation
  agent_loop.py      # Agent_Loop: task orchestration
  database/          # SQLite connection, schema, and transactions
  memory/            # Memory_Store and Server_Registry
  llm/               # LLM_Client and the OpenAI-style tool catalog
  telegram/          # Telegram_Interface and slash-command dispatcher
  tools/             # ssh, git, files, terminal, pterodactyl, cpanel,
                     # docker, browser, deployment
logs/                # activity log files (created at startup if absent)
tests/               # property, unit, integration, and smoke tests
requirements.txt     # runtime and development dependencies
README.md            # this file
```

## Testing

```bash
pytest
```

Property-based tests (using `hypothesis`) verify the system's correctness
properties; unit, integration, and smoke tests cover specific examples,
external-service behavior, and startup. Tests that require a live browser are
skipped automatically when Playwright browser binaries are not installed.
