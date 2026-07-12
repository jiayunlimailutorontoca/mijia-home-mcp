# mijia-home-mcp

米家全屋状态快照 MCP server —— **默认只读,一次调用看清全家**。

让 Claude / Cursor 等 MCP 客户端安全地"看家":一个 `get_home_snapshot` 工具并发拉取全屋设备状态,返回 `家 → 房间 → 设备 → 语义化状态` 的结构化结果;`get_home_changes` 回答"上次以来家里变了什么"。控制能力(开关/属性/场景)默认关闭,需要显式开启并受白名单与危险设备策略约束。

基于 [Do1e/mijia-api](https://github.com/Do1e/mijia-api)(GPL-3.0),与其共用登录凭证(扫码一次约保活一个月)。

## 为什么不是又一个米家 MCP

| | 常见米家 MCP | mijia-home-mcp |
|---|---|---|
| 问"家里什么情况" | N+1 轮逐设备查询 | `get_home_snapshot` 一次调用,批量接口并发拉取 |
| 变化感知 | 无 | `get_home_changes` 返回自上次快照的 diff |
| 安全模型 | 开箱即可控制所有家电 | **默认只读**;控制需 `--enable-control` + 白名单;锁/摄像头/燃气与水阀默认拦截;写操作全部落审计日志 |
| 输出 | 原始 siid/piid 透传 | 语义化(枚举→中文描述、bool→开启/关闭),离线/低电量/故障设备置顶提醒 |
| 部署 | clone + venv,Unix-first | `uvx` 直接从 GitHub 一行运行,Windows/macOS/Linux 一等支持 |

## 快速开始

无需 clone,`uvx` 直接从 GitHub 运行。

1. 扫码登录(终端会打印二维码,用米家 App 扫描,凭证约一个月有效):

```bash
uvx --from git+https://github.com/jiayunlimailutorontoca/mijia-home-mcp mijia-home-mcp login
```

2. 添加到 Claude Code(只读模式):

```bash
claude mcp add mijia-home -- uvx --from git+https://github.com/jiayunlimailutorontoca/mijia-home-mcp mijia-home-mcp serve
```

或者写入项目 `.mcp.json` / Claude Desktop 配置:

```json
{
  "mcpServers": {
    "mijia-home": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/jiayunlimailutorontoca/mijia-home-mcp",
        "mijia-home-mcp",
        "serve"
      ]
    }
  }
}
```

3. 问一句"家里现在什么情况",模型会调用 `get_home_snapshot`。

### 安装时配置通知通道(推荐)

在 `.mcp.json` 的 `env` 里声明通知通道,server 启动即常驻,Claude 里多出一个 `send_notification` 工具——一次调用**统一推送到所有已配置的通道**:

```json
{
  "mcpServers": {
    "mijia-home": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/jiayunlimailutorontoca/mijia-home-mcp",
        "mijia-home-mcp",
        "serve"
      ],
      "env": {
        "MIJIA_HOME_MCP_SPEAKER": "auto",
        "MIJIA_HOME_MCP_MEOW": "你的MeoW昵称",
        "MIJIA_HOME_MCP_FEISHU": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
        "MIJIA_HOME_MCP_DINGTALK": "https://oapi.dingtalk.com/robot/send?access_token=xxx",
        "MIJIA_HOME_MCP_DINGTALK_SECRET": "SECxxx"
      }
    }
  }
}
```

只配需要的通道即可(都不配则不出现 `send_notification` 工具)。之后在 Claude 里说"提醒我半小时后关火""通知家里人我到楼下了",消息会同时到达音箱、手机推送和群机器人,返回值逐通道报告成败:

```json
{"小爱音箱(Xiaomi Smart Speaker)": "ok", "MeoW": "ok", "飞书": "ok"}
```

命令行等价参数:`serve --speaker auto --meow 昵称 --feishu URL --dingtalk URL --dingtalk-secret SEC`。`watch` 未显式传通道参数时也自动复用这套配置。

> 下文示例统一用简写 `mijia-home-mcp serve ...`,实际命令替换为上面的 `uvx --from git+... mijia-home-mcp serve ...`;本地 clone 后 `uv run mijia-home-mcp ...` 也等价。

### 开启控制(可选)

```bash
# 允许控制普通设备(锁/摄像头/燃气与水阀/保险柜仍被拦截)
claude mcp add mijia-home -- uvx --from git+https://github.com/jiayunlimailutorontoca/mijia-home-mcp mijia-home-mcp serve --enable-control

# 只允许控制名单内设备(glob 匹配设备名/did/model,可多次传入)
mijia-home-mcp serve --enable-control --allow "客厅*" --allow "*台灯*"

# 黑名单优先于白名单
mijia-home-mcp serve --enable-control --deny "*camera*"

# 明确允许危险设备(不推荐)
mijia-home-mcp serve --enable-control --allow-dangerous
```

安全策略细则:

- **危险设备只接受精确放行**:`--allow "*"` 这类通配白名单会放行普通设备,但锁/摄像头/燃气与水阀/保险柜必须把**完整设备名或 did** 精确写进 `--allow`(或使用 `--allow-dangerous`)才可控制。
- **`run_speaker_command` 按危险通道对待**:小爱语音指令可以触达全屋任意设备(包括门锁),会绕过设备白名单,因此默认拦截——需要把音箱名精确加入 `--allow` 或使用 `--allow-dangerous`。
- **`run_scene` 不受设备白名单约束**:场景内容是你在米家 App 预定义的动作组合,开启控制后即可执行;不想让 AI 碰的动作不要做成手动场景。
- 所有写操作(含被拒绝的尝试)都会追加到 `~/.config/mijia-home-mcp/audit.log`。

### 局域网部署(可选)

```bash
mijia-home-mcp serve --transport http --host 0.0.0.0 --port 8423
```

```bash
claude mcp add --transport http mijia-home http://<host>:8423/mcp
```

> ⚠️ http 传输当前没有内置鉴权:监听 `0.0.0.0` 意味着同网段任何人都能读取(开启控制时还能操作)你的米家设备。只在可信局域网内使用并配合防火墙,切勿暴露公网。

## 工具一览

**读(始终可用):**

| 工具 | 用途 |
|---|---|
| `get_home_snapshot` | 全屋状态快照(compact/full 两档,支持 home/room 过滤),附离线/低电量/故障提醒 |
| `get_home_changes` | 与上次快照对比,返回变化列表 |
| `get_battery_report` | 全屋电量普查,按电量升序,低电量置顶 |
| `get_device_statistics` | 设备历史统计(耗电量/使用时长,时/日/周/月粒度) |
| `list_homes` / `list_devices` | 家庭/房间/设备清单,支持过滤 |
| `get_device_status` | 单设备详细状态(批量拉取) |
| `get_device_spec` | 设备支持的属性/动作(名称、类型、范围、枚举值) |
| `list_scenes` / `list_consumables` | 手动场景 / 耗材状态 |
| `auth_status` / `login` / `login_status` | 认证状态与会话内扫码续期 |
| `send_notification` | 统一推送到安装时配置的全部通知通道(配置了通道才出现) |

另有 MCP prompt `home_briefing`:一键生成管家式全屋简报。

**控制(需 `--enable-control`):**

| 工具 | 用途 |
|---|---|
| `set_device_property` | 设置属性(开关/亮度/模式…) |
| `run_device_action` | 执行动作(喂食/启动清扫…) |
| `run_scene` | 运行米家手动场景 |
| `speaker_announce` | 小爱音箱播报一句话(纯 TTS,无执行能力) |
| `run_speaker_command` | 让小爱音箱执行自然语言指令(按危险通道门控) |

## 终端直用(不需要 MCP 客户端)

装好之后 CLI 本身就是个小工具箱:

```bash
mijia-home-mcp doctor              # 自检:认证/云端连通性/缓存
mijia-home-mcp snapshot            # 全屋状态一屏看完(--home/--room/--full/--json)
mijia-home-mcp devices             # 设备清单(名称/位置/model/did)
mijia-home-mcp battery             # 电量普查,低电量置顶
mijia-home-mcp watch --interval 60 # 持续监控变化,实时打印(Ctrl-C 退出)
```

`watch` 实测效果:

```text
[14:26:04] 基线已建立,开始监控…
[14:26:38] Mijia Smart Tabletop Dishwasher S2: left-time 159 → 158
```

### watch 通知:小爱播报 / 钉钉 / 飞书 / MeoW / webhook

变化不只打印在终端,还可以推出去,通道可任意组合:

```bash
# 小爱音箱播报(play-text 纯 TTS,不会触发指令执行)
mijia-home-mcp watch --speak --speaker-name "Xiaomi Smart Speaker"

# 钉钉机器人(安全设置选「自定义关键词」填 米家;或用加签)
mijia-home-mcp watch --dingtalk "https://oapi.dingtalk.com/robot/send?access_token=xxx"
mijia-home-mcp watch --dingtalk "https://oapi.dingtalk.com/robot/send?access_token=xxx" --dingtalk-secret SECxxx

# 飞书自定义机器人
mijia-home-mcp watch --feishu "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"

# MeoW(鸿蒙系统级推送,传注册的昵称即可)
mijia-home-mcp watch --meow 你的MeoW昵称

# 通用 webhook:完整 diff JSON POST 到你的服务
mijia-home-mcp watch --webhook https://example.com/hook

# 组合 + 噪音控制:只盯门锁和传感器,忽略倒计时属性,同时推三处
mijia-home-mcp watch --speak --meow 昵称 --feishu https://... \
  --only "门锁*" --only "*传感器*" --ignore left-time
```

钉钉/飞书/MeoW 收到的是"米家提醒 + 一句话摘要"的文本消息;通用 webhook 收到完整 diff JSON(含 `text` 摘要字段):

```json
{
  "source": "mijia-home-mcp",
  "title": "米家提醒",
  "text": "客厅台灯的on从True变为False",
  "change_count": 1,
  "changes": [
    {"type": "prop_changed", "device": "客厅台灯", "prop": "on", "from": true, "to": false}
  ]
}
```

单通道失败(音箱离线/网络抖动/机器人限流)只打日志,互不影响,也不中断监控。

### 让小爱说话

独立的 `say` 命令,配合系统计划任务就是个极简提醒器;MCP 侧对应工具 `speaker_announce`(需 `--enable-control`,受普通设备门控,比 `run_speaker_command` 宽松,因为纯 TTS 无执行能力):

```bash
mijia-home-mcp say "下楼取快递" --speaker-name "Xiaomi Smart Speaker 2"
```

## 配置

CLI 参数优先,也支持环境变量(适合写进 `.mcp.json` 的 `env`):

| 环境变量 | 对应参数 |
|---|---|
| `MIJIA_HOME_MCP_AUTH` | `--auth` 认证文件路径(默认 `~/.config/mijia-api/auth.json`,与 mijiaAPI 共用) |
| `MIJIA_HOME_MCP_ENABLE_CONTROL` | `--enable-control`(`1`/`true` 开启) |
| `MIJIA_HOME_MCP_ALLOW` | `--allow`(逗号分隔) |
| `MIJIA_HOME_MCP_DENY` | `--deny`(逗号分隔) |
| `MIJIA_HOME_MCP_ALLOW_DANGEROUS` | `--allow-dangerous` |
| `MIJIA_HOME_MCP_STATE_DIR` | 状态目录(快照基线/审计日志,默认 `~/.config/mijia-home-mcp`) |
| `MIJIA_HOME_MCP_SPEAKER` | 通知通道:小爱音箱名称,`auto` 用第一台 |
| `MIJIA_HOME_MCP_MEOW` | 通知通道:MeoW 昵称或完整 URL |
| `MIJIA_HOME_MCP_FEISHU` | 通知通道:飞书机器人 webhook |
| `MIJIA_HOME_MCP_DINGTALK` / `_DINGTALK_SECRET` | 通知通道:钉钉机器人 webhook 与加签密钥 |
| `MIJIA_HOME_MCP_WEBHOOK` | 通知通道:通用 webhook |

## 已知边界

- 走小米云端接口(上游 mijia-api 逆向实现),状态读取有秒级延迟,无本地直连与事件推送;请控制轮询频率。
- 凭证约一个月需重新扫码一次(`mijia-home-mcp login`,或对话里直接调 `login` 工具)。
- 工具的 `readOnlyHint` 等注解只是提示;真正的安全边界在服务端(只读默认 + 白名单 + 危险设备拦截)。
- 个别设备的规格页缺少中文 i18n 数据(常见于红外遥控类设备,如空调伴侣里的"空调"),上游 spec 解析会失败;这类设备会出现在快照的 `attention.spec_errors` 里,状态为空但不影响其他设备。
- `get_home_changes` 的对比基线按 `home` 参数分开存储;跨口径调用(这次传 home、下次不传)各自维护基线。

## 开发

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest
```

测试完全离线(FakeAPI + 磁盘 spec 缓存),不需要米家账号。

## 许可证

[GPL-3.0-or-later](LICENSE)。依赖 [mijia-api](https://github.com/Do1e/mijia-api)(GPL-3.0,其 README 声明仅供学习交流、禁止商用),本项目同样仅供学习交流使用。
