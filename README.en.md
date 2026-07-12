# mijia-home-mcp

[中文](README.md) | English

MCP server for Xiaomi Home. The itch it scratches: asking Claude "what's going on at home" shouldn't take fifteen tool calls.

`get_home_snapshot` pulls every device state in one call (batched API, ~9 s for 65 devices), grouped by home/room, enum values translated to labels, offline and low-battery devices surfaced separately. `get_home_changes` tells you what changed since you last asked.

Read-only by default. Control tools only register with `--enable-control`, and locks / cameras / gas valves get an extra gate on top — I don't trust a model with those.

Auth goes through [Do1e/mijia-api](https://github.com/Do1e/mijia-api) and shares its credential file (`~/.config/mijia-api/auth.json`). One QR scan lasts about a month.

## Install

No clone needed:

```bash
# scan the QR code printed in the terminal
uvx --from git+https://github.com/jiayunlimailutorontoca/mijia-home-mcp mijia-home-mcp login

claude mcp add mijia-home -- uvx --from git+https://github.com/jiayunlimailutorontoca/mijia-home-mcp mijia-home-mcp serve
```

Then just ask "what's happening at home".

### Updating

uvx caches git builds, so your local copy won't follow the repo automatically. Refresh manually:

```bash
uvx --refresh --from git+https://github.com/jiayunlimailutorontoca/mijia-home-mcp mijia-home-mcp --version
```

then restart your MCP client. `doctor` also checks GitHub for a newer release. To pin a version instead, append `@v0.10.0` to the git URL. Bugfix releases show up under [Releases](https://github.com/jiayunlimailutorontoca/mijia-home-mcp/releases) — watch those if you want notifications.

## Notification channels

Configure channels in env and a `send_notification` tool appears, pushing to all of them in one call (the tool doesn't exist if nothing is configured):

```json
"env": {
  "MIJIA_HOME_MCP_SPEAKER": "auto",
  "MIJIA_HOME_MCP_FEISHU": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
  "MIJIA_HOME_MCP_DINGTALK": "https://oapi.dingtalk.com/robot/send?access_token=xxx"
}
```

Channels: Xiaomi speaker TTS, DingTalk bot (keyword or signed), Feishu/Lark bot (keyword or signed), MeoW (HarmonyOS push), generic webhook. Results are reported per channel; one failing doesn't block the rest.

## Enabling control

```bash
mijia-home-mcp serve --enable-control --allow "living room*" --deny "*camera*"
```

The rules, all deliberate:

- Dangerous devices (locks, cameras, gas/water valves, safes) don't match wildcards. `--allow "*"` won't unlock the door lock — you need the exact device name or did in `--allow`, or `--allow-dangerous`.
- `run_speaker_command` hands a natural-language command to the speaker, which can reach any device in the house and bypass the allowlist entirely. So it's gated like a dangerous device. For plain TTS use `speaker_announce`, which only makes sound and is gated as a normal device.
- `run_scene` ignores the device allowlist — scenes are whatever you defined in the Mi Home app. Don't make a manual scene out of anything you wouldn't want a model to trigger.
- Every write, including denied ones, is appended to `~/.config/mijia-home-mcp/audit.log`.

## LAN deployment

```bash
mijia-home-mcp serve --transport http --host 0.0.0.0 --port 8423 --http-token <random>
claude mcp add --transport http mijia-home http://<host>:8423/mcp --header "Authorization: Bearer <random>"
```

Without `--http-token` there is no auth and a warning is printed at startup. Don't expose it to the internet either way.

## Tools

Read (always on): `get_home_snapshot` (home/room filter, compact/full, 30 s cache), `get_home_changes`, `query_history` (local 30-day event log, populated while `watch` runs), `get_battery_report`, `get_device_statistics`, `list_homes` / `list_devices` / `get_device_status` / `get_device_spec` / `list_scenes` / `list_consumables`, `auth_status` / `login` / `login_status`, `send_notification` (when channels configured). Plus a `home_briefing` prompt.

Control (with `--enable-control`): `set_device_property`, `run_device_action`, `run_scene`, `speaker_announce`, `run_speaker_command`.

## CLI

Works standalone, no MCP client required:

```bash
mijia-home-mcp doctor        # auth / connectivity self-check
mijia-home-mcp snapshot      # whole home at a glance
mijia-home-mcp devices
mijia-home-mcp battery
mijia-home-mcp say "dinner is ready"
mijia-home-mcp watch --speak --only "door*" --ignore left-time
```

`watch` polls, prints changes, optionally pushes them (speaker / DingTalk card / Feishu card / MeoW / webhook), and records everything into the local history that `query_history` reads. `--ignore left-time` matters — a running dishwasher counts down every minute.

## Known limits

- Everything goes through Xiaomi's cloud (reverse-engineered upstream). Second-ish latency, no push events, keep poll intervals sane.
- Credentials expire after roughly a month; re-scan with `login`.
- IR-based devices (e.g. the "AC" behind an AC partner) have broken spec pages upstream; they show up under `attention.spec_errors` with no state. Other devices are unaffected.
- `readOnlyHint` annotations are hints for clients; the actual enforcement is server-side.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest   # fully offline, no Xiaomi account needed
```

## License

GPL-3.0-or-later. Upstream mijia-api is GPL-3.0 and declares itself learning/non-commercial use only; same applies here.
