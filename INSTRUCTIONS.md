# Slacksor Setup Instructions

## Prerequisites

- Python 3.11+
- `cursor` CLI installed and authenticated
- Slack workspace where you can create/install apps

## Create The Slack App

1. Open [api.slack.com/apps](https://api.slack.com/apps)
2. Click **Create New App** -> **From scratch**
3. Choose a name like `slacksor` and select your workspace

## Configure Bot Token Scopes

In **OAuth & Permissions** add these Bot Token Scopes:

- `chat:write`
- `channels:history`
- `channels:read`
- `channels:manage`
- `channels:join`
- `reactions:write`
- `users:write`

Install the app to workspace.

After install, copy **Bot User OAuth Token** (starts with `xoxb-`)
Set this value as `SLACK_BOT_TOKEN`.

## Configure Socket Mode

1. Open **Socket Mode**
2. Enable Socket Mode
3. Create an app-level token with scope `connections:write`
4. Copy token (starts with `xapp-`)
   Set this value as `SLACK_APP_TOKEN`

## Configure Event Subscriptions

1. Open **Event Subscriptions**
2. Enable Events
3. Add bot event: `message.channels`

No public URL is required because Slacksor uses Socket Mode.

## Install Dependencies

```bash
cd /path/to/slacksor
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configure Environment

```bash
cp .env.example .env
```

Edit `.env`:

- `SLACK_BOT_TOKEN=xoxb-...`
- `SLACK_APP_TOKEN=xapp-...`

Optional runtime knobs:

- `SLACKSOR_DB_PATH=./slacksor.db`
- `SLACKSOR_SESSION_TIMEOUT_SECONDS=300`
- `SLACKSOR_KEEPALIVE_SECONDS=30`
- `SLACKSOR_POST_CHUNK_SIZE=3500`
- `SLACKSOR_POLLING_INTERVAL_SECONDS=1.0`
- `SLACKSOR_ENABLE_IDE_TRANSCRIPT_MIRROR=true`
- `SLACKSOR_ENABLE_CURSOR_HOOKS_SYNC=true`

## Run Slacksor

TUI mode (default):

```bash
python src/slacksor.py
```

Headless mode:

```bash
python src/slacksor.py serve
```

Bundled launcher script:

```bash
./start.sh
```

It prompts:

- `Y` or `y` -- runs TUI mode
- `N` or `n` -- runs headless mode (`serve`)

Project management:

```bash
python src/slacksor.py add-project ~/Desktop/MyProject
python src/slacksor.py add-project ~/Desktop/MyProject --channel myproject
python src/slacksor.py list-projects
python src/slacksor.py remove-project ~/Desktop/MyProject
python src/slacksor.py help
python src/slacksor.py model
python src/slacksor.py model gpt-5
python src/slacksor.py stop --workspace ~/Desktop/MyProject
python src/slacksor.py stop
python src/slacksor.py exit
python src/slacksor.py clear-db
python src/slacksor.py clear-db --workspace ~/Desktop/MyProject
python src/slacksor.py clear-db --all
```

## Bridge Commands (Slack)

These commands are intercepted by slacksor and not sent to Cursor Agent:

- `help` -- show bridge capabilities and usage
- `ping` -- check bridge status, uptime, and queue depth
- `model` -- show current default model and options
- `model <name>` -- set default model for new requests (`auto` by default)
- `model-override <name>` -- set per-project model override (use `clear` to remove)
- `branch` -- show git branches with current branch highlighted
- `status` -- show `git status` for the workspace
- `diff` -- show git diff summary with changed lines per file
- `stop` / `exit` -- stop active session processing
- `!<command>` -- run a shell command in the workspace (e.g. `!git log --oneline -5`)
- `/<command>` -- use a Cursor command (e.g. `/review`, `/tests`). Looks up `.cursor/commands/<command>.md` in workspace, then `~/.cursor/commands/`

## TUI Key Bindings

- `h` -- help
- `m` -- cycle default model
- `s` -- stop selected workspace session
- `k` -- kill selected running session row
- `q` -- exit TUI

## How To Get Tokens Quickly

- `xoxb` bot token: Slack App -> **OAuth & Permissions** -> **Install to Workspace** -> Bot User OAuth Token
- `xapp` app token: Slack App -> **Socket Mode** -> **App-Level Tokens** (scope `connections:write`)

## Optional Auto Start (macOS launchd)

1. Update placeholders in `com.slacksor.plist`:
   - python executable path
   - working directory
2. Install:

```bash
cp com.slacksor.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.slacksor.plist
launchctl start com.slacksor
```

3. Verify:

```bash
launchctl list | grep slacksor
```

4. Stop/unload:

```bash
launchctl stop com.slacksor
launchctl unload ~/Library/LaunchAgents/com.slacksor.plist
```
