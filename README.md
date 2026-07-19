# mijia-home-mcp

中文 | [English](README.en.md)

米家 MCP server。`get_home_snapshot` 一次调用批量拉全屋设备状态(65 台约 9s),按家/房间分组,枚举值转成可读文本。默认只读,控制工具要显式开启,锁和摄像头另有一层拦截。

基于 [Do1e/mijia-api](https://github.com/Do1e/mijia-api),共用其登录凭证(`~/.config/mijia-api/auth.json`,扫码一次约一个月)。

## 安装

先登录(终端出二维码,米家 App 扫):

```bash
uvx --from git+https://github.com/jiayunlimailutorontoca/mijia-home-mcp mijia-home-mcp login
```

加到 Claude Code:

```bash
claude mcp add mijia-home -- uvx --from git+https://github.com/jiayunlimailutorontoca/mijia-home-mcp mijia-home-mcp serve
```

问一句"家里现在什么情况"就能用了。

<details>
<summary>让 AI 自己装(粘贴给 Claude Code)</summary>

```text
帮我安装 mijia-home 米家 MCP:
1. uvx --from git+https://github.com/jiayunlimailutorontoca/mijia-home-mcp mijia-home-mcp login
   二维码给我扫;已有有效的 ~/.config/mijia-api/auth.json 就跳过。这步需要我,停下来等。
2. claude mcp add mijia-home -- uvx --from git+https://github.com/jiayunlimailutorontoca/mijia-home-mcp mijia-home-mcp serve
3. 我有多个家庭的话先问我锁哪个,命令加 --env MIJIA_HOME_MCP_HOME_NAME=家庭名
4. 调 get_home_snapshot,报在线设备数
```
</details>

<details>
<summary>让 AI 自己装(粘贴给 OpenClaw)</summary>

```text
帮我安装 mijia-home 米家 MCP:
1. 登录同上,二维码给我扫
2. openclaw mcp add mijia-home --command uvx --arg "--from" --arg "git+https://github.com/jiayunlimailutorontoca/mijia-home-mcp" --arg "mijia-home-mcp" --arg "serve"
3. openclaw mcp probe mijia-home 应显示 14+ tools;会话里看不到就 npm i -g mcporter
4. 调 get_home_snapshot 验证
```
</details>

多家庭账号建议锁定默认家庭,否则每次全家庭拉取:

```json
"env": { "MIJIA_HOME_MCP_HOME_NAME": "我的家" }
```

## 通知

配置通道后多一个 `send_notification` 工具,一次推所有已配置的通道;不配则该工具不出现。

```json
"env": {
  "MIJIA_HOME_MCP_SPEAKER": "auto",
  "MIJIA_HOME_MCP_MEOW": "MeoW昵称",
  "MIJIA_HOME_MCP_BARK": "Bark设备key",
  "MIJIA_HOME_MCP_NTFY": "ntfy主题",
  "MIJIA_HOME_MCP_FEISHU": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
  "MIJIA_HOME_MCP_DINGTALK": "https://oapi.dingtalk.com/robot/send?access_token=xxx"
}
```

各通道申请方式:

- 钉钉:群设置 → 机器人 → 自定义。安全设置选关键词填`米家`,或加签(密钥配 `_DINGTALK_SECRET`)。仅内部群,限流 20 条/分
- 飞书:群设置 → 群机器人 → 自定义机器人。签名密钥配 `_FEISHU_SECRET`
- MeoW(鸿蒙):装 app 注册昵称,昵称即配置
- Bark(iOS):app 里复制 device key;自建填 `https://host/key`
- ntfy(安卓):订阅一个 topic,topic 即配置。公开 topic 谁都能订,名字取随机

## 控制

默认只读。开启:

```bash
mijia-home-mcp serve --enable-control --allow "客厅*" --deny "*camera*"
```

规则:

- 锁/摄像头/燃气水阀/保险柜不吃通配符,必须 `--allow` 精确写设备名或 did,或 `--allow-dangerous`
- `run_speaker_command`(小爱执行语音指令)能触达全屋设备,按危险设备同等门控;经 `run_device_action` 调 `execute-text-directive` 走同一道闸,绕不过去。纯播报用 `speaker_announce`,普通门控
- `run_scene` 不看白名单——场景内容是你在米家 App 定义的,不想让 AI 碰的别做成手动场景
- 所有写操作(含被拒的)记 `~/.config/mijia-home-mcp/audit.log`

## 工具

读:`get_home_snapshot`(home/room 过滤,30s 缓存)、`get_home_changes`(diff)、`query_history`(本地 30 天事件史)、`get_battery_report`、`get_device_statistics`、`list_homes/list_devices/get_device_status/get_device_spec/list_scenes/list_consumables`、`auth_status/login/login_status`、`send_notification`

控制:`turn_on/turn_off`(自动匹配开关属性)、`set_device_property`、`run_device_action`、`run_scene`、`speaker_announce`、`run_speaker_command`

resources:`mijia://devices`、`mijia://homes`、`mijia://snapshot`

耗材:`list_consumables` 返回云端算好的三态(充足/不足/耗尽),该换的进 `needs_attention`,同时出现在快照的 attention 里。

## 命令行

不接 MCP 也能用:

```bash
mijia-home-mcp doctor       # 自检 + 更新检查
mijia-home-mcp snapshot     # --home --room --full --json
mijia-home-mcp devices
mijia-home-mcp battery
mijia-home-mcp say "吃饭了"
mijia-home-mcp watch --speak --only "门锁*" --ignore left-time
```

watch 轮询变化,推送到已配置的通道,每小时查一次耗材,事件写本地历史。`--ignore left-time` 建议加上,洗碗机倒计时一分钟变一次。

## 局域网

```bash
mijia-home-mcp serve --transport http --host 0.0.0.0 --port 8423 --http-token 随机串
claude mcp add --transport http mijia-home http://<host>:8423/mcp --header "Authorization: Bearer 随机串"
```

不设 token 无鉴权,启动会警告。别暴露公网。token 建议走环境变量 `MIJIA_HOME_MCP_HTTP_TOKEN`,命令行参数在进程列表可见。

## 环境变量

| 变量 | 说明 |
|---|---|
| `MIJIA_HOME_MCP_AUTH` | 认证文件,默认 `~/.config/mijia-api/auth.json` |
| `MIJIA_HOME_MCP_HOME_NAME` | 默认家庭 |
| `MIJIA_HOME_MCP_ENABLE_CONTROL` | `1` 开控制 |
| `MIJIA_HOME_MCP_ALLOW` / `_DENY` | 白/黑名单,逗号分隔 |
| `MIJIA_HOME_MCP_ALLOW_DANGEROUS` | 放行危险设备 |
| `MIJIA_HOME_MCP_STATE_DIR` | 状态目录,默认 `~/.config/mijia-home-mcp` |
| `MIJIA_HOME_MCP_SPEAKER` / `_MEOW` / `_BARK` / `_NTFY` / `_FEISHU(_SECRET)` / `_DINGTALK(_SECRET)` / `_WEBHOOK` | 通知通道 |
| `MIJIA_HOME_MCP_HTTP_TOKEN` | http Bearer token |

## 已知问题

- 走小米云端(上游为逆向接口),秒级延迟,无推送,轮询别太密
- 凭证约一个月过期,`login` 重扫
- 红外类设备(空调伴侣的"空调")spec 页缺中文数据,解析失败会进 `attention.spec_errors`,不影响其他设备
- `readOnlyHint` 注解只是提示,真正的门在服务端

## 开发

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest
```

测试离线,不需要米家账号。

## License

GPL-3.0-or-later。上游 mijia-api 为 GPL-3.0 且声明仅供学习交流,本项目相同。
