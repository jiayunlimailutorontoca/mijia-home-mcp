# mijia-home-mcp

[中文](README.md) | English

Whole-home state snapshot MCP server for Xiaomi Home (Mijia) — **read-only by default, see your entire home in one call**.

Lets Claude / Cursor / any MCP client safely "watch your home": a single `get_home_snapshot` tool fetches all device states concurrently and returns a structured `home → room → device → humanized state` result; `get_home_changes` answers "what changed since last time". Control abilities (switches / properties / scenes) are off by default and gated by allowlists and a dangerous-device policy when enabled.

Built on [Do1e/mijia-api](https://github.com/Do1e/mijia-api) (GPL-3.0), sharing its login credentials (scan the QR code once, stays valid for about a month).

## Why another Mijia MCP

| | Typical Mijia MCP | mijia-home-mcp |
|---|---|---|
| "What's going on at home?" | N+1 per-device queries | one `get_home_snapshot` call, batched & concurrent (65 devices in ~9 s) |
| Change awareness | none | `get_home_changes` diff + 30-day local event history (`query_history`) |
| Safety model | full control out of the box | **read-only by default**; control requires `--enable-control` + allowlist; locks/cameras/gas valves blocked unless explicitly released; every write audited |
| Output | raw siid/piid passthrough | humanized (enums → labels, booleans → on/off), offline/low-battery/fault devices highlighted |
| Deployment | clone + venv, Unix-first | one `uvx` line straight from GitHub, first-class Windows support |

## Quick start

1. Log in (a QR code prints in the terminal; scan with the Mi Home app):

```bash
uvx --from git+https://github.com/jiayunlimailutorontoca/mijia-home-mcp mijia-home-mcp login
```

2. Add to Claude Code (read-only):

```bash
claude mcp add mijia-home -- uvx --from git+https://github.com/jiayunlimailutorontoca/mijia-home-mcp mijia-home-mcp serve
```

3. Ask "what's happening at home?" — the model calls `get_home_snapshot`.

### Configure notification channels at install time

Declare channels in `.mcp.json` env; a `send_notification` tool then appears and pushes to **all configured channels in one call** (Xiaomi speaker TTS + DingTalk + Feishu/Lark + MeoW + generic webhook):

```json
{
  "mcpServers": {
    "mijia-home": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/jiayunlimailutorontoca/mijia-home-mcp", "mijia-home-mcp", "serve"],
      "env": {
        "MIJIA_HOME_MCP_SPEAKER": "auto",
        "MIJIA_HOME_MCP_FEISHU": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
        "MIJIA_HOME_MCP_DINGTALK": "https://oapi.dingtalk.com/robot/send?access_token=xxx"
      }
    }
  }
}
```

## Tools

**Read (always available):** `get_home_snapshot` (compact/full, home/room filter, 30 s cache), `get_home_changes`, `query_history` (30-day local event log), `get_battery_report`, `get_device_statistics`, `list_homes`, `list_devices`, `get_device_status`, `get_device_spec`, `list_scenes`, `list_consumables`, `auth_status` / `login` / `login_status`, plus `send_notification` when channels are configured and a `home_briefing` prompt.

**Control (requires `--enable-control`):** `set_device_property`, `run_device_action`, `run_scene`, `speaker_announce` (pure TTS), `run_speaker_command` (voice-command passthrough, gated as dangerous since it can reach any device).

Safety details: dangerous devices (locks / cameras / gas & water valves / safes) require the exact device name or did in `--allow` (wildcards don't count) or `--allow-dangerous`; all writes — including denied attempts — are appended to `~/.config/mijia-home-mcp/audit.log`.

## Terminal usage (no MCP client needed)

```bash
mijia-home-mcp doctor        # self-check: auth / cloud connectivity / caches
mijia-home-mcp snapshot      # whole home at a glance (--home/--room/--full/--json)
mijia-home-mcp devices       # device list
mijia-home-mcp battery       # battery census, lowest first
mijia-home-mcp say "dinner"  # make the Xiaomi speaker talk
mijia-home-mcp watch --speak --meow nick --ignore left-time   # live monitor + notify
```

`watch` records every change into a local 30-day JSONL history, which powers `query_history` ("how many times did the door open today?").

## HTTP transport (LAN)

```bash
mijia-home-mcp serve --transport http --host 0.0.0.0 --port 8423 --http-token <random-string>
claude mcp add --transport http mijia-home http://<host>:8423/mcp --header "Authorization: Bearer <random-string>"
```

Without `--http-token` the HTTP transport has no auth (a warning is printed). Never expose it to the public internet.

## Known limits

- Goes through Xiaomi's cloud API (reverse-engineered upstream): second-level latency, no local push events — keep polling intervals sane.
- Credentials need a QR re-scan roughly monthly (`mijia-home-mcp login`, or the `login` tool in-chat).
- Tool annotations like `readOnlyHint` are hints; the real security boundary is server-side (read-only default + allowlists + dangerous-device gate).

## Development

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest   # fully offline, no Xiaomi account needed
```

## License

[GPL-3.0-or-later](LICENSE). Depends on [mijia-api](https://github.com/Do1e/mijia-api) (GPL-3.0, whose README declares non-commercial / learning use only); this project follows the same terms.
