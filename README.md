# mijia-home-mcp

中文 | [English](README.en.md)

米家的 MCP server,主要解决一个问题:想让 Claude 知道家里什么情况,不该需要十几轮工具调用。

`get_home_snapshot` 一次调用把全屋设备状态都拉回来(走批量接口,65 台设备大概 9 秒),按 家/房间/设备 组织,枚举值翻译成人话,离线和低电量的设备单独列出来。`get_home_changes` 告诉你上次问过之后家里变了什么。

默认只读。控制设备的工具要加 `--enable-control` 才会注册,而且锁、摄像头、燃气阀这类东西额外拦了一道。我不太放心让模型直接碰这些。

登录用的是 [Do1e/mijia-api](https://github.com/Do1e/mijia-api),凭证文件也跟它共用(`~/.config/mijia-api/auth.json`),扫一次码大概管一个月。

## 安装

不用 clone,uvx 直接跑:

```bash
# 先扫码登录,终端会打印二维码
uvx --from git+https://github.com/jiayunlimailutorontoca/mijia-home-mcp mijia-home-mcp login

# 加到 Claude Code
claude mcp add mijia-home -- uvx --from git+https://github.com/jiayunlimailutorontoca/mijia-home-mcp mijia-home-mcp serve
```

或者 `.mcp.json`:

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

装完问一句"家里现在什么情况"就能用了。

### 多家庭账号

账号下有多个家庭的话,默认所有工具都是全家庭一起拉,又慢又费 token。锁定一个:

```json
"env": { "MIJIA_HOME_MCP_HOME_NAME": "我的家" }
```

或 `serve --home 我的家`。锁定后所有工具不传 `home` 参数时只看这个家;对话里显式说"看看另一个家"仍然可以覆盖。家庭名可以用 `list_homes` 或 `mijia-home-mcp devices` 确认。

后面的示例统一简写成 `mijia-home-mcp ...`,实际替换成上面 `uvx --from git+...` 那串,或者 clone 之后 `uv run mijia-home-mcp ...`。

### 更新

uvx 会缓存 git 构建,仓库更新了你本地不会自动跟上。手动刷一下:

```bash
uvx --refresh --from git+https://github.com/jiayunlimailutorontoca/mijia-home-mcp mijia-home-mcp --version
```

刷完重启 MCP client(或 `claude mcp` 里重连)就是新版。`doctor` 会顺带检查 GitHub 上有没有新版本:

```text
  [!!] 版本 — 当前 v0.10.0,最新 v0.11.0
       更新: uvx --refresh --from git+...
```

不想被更新影响的话,锁死某个 tag:

```bash
uvx --from git+https://github.com/jiayunlimailutorontoca/mijia-home-mcp@v0.10.0 mijia-home-mcp serve
```

`.mcp.json` 里同理,`args` 的 git 地址后面加 `@v0.10.0`。修 bug 的版本会发 [Releases](https://github.com/jiayunlimailutorontoca/mijia-home-mcp/releases),想收更新通知就 Watch 仓库的 Releases。

## 通知通道

在 env 里配好通道,Claude 里会多一个 `send_notification` 工具,一次调用推所有配置的通道。什么都不配的话这个工具不会出现。

```json
"env": {
  "MIJIA_HOME_MCP_SPEAKER": "auto",
  "MIJIA_HOME_MCP_MEOW": "你的MeoW昵称",
  "MIJIA_HOME_MCP_BARK": "你的Bark设备key",
  "MIJIA_HOME_MCP_NTFY": "你的ntfy主题",
  "MIJIA_HOME_MCP_FEISHU": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
  "MIJIA_HOME_MCP_DINGTALK": "https://oapi.dingtalk.com/robot/send?access_token=xxx",
  "MIJIA_HOME_MCP_DINGTALK_SECRET": "SECxxx"
}
```

返回值逐通道报成败,坏一个不影响其他的:

```json
{"小爱音箱(Xiaomi Smart Speaker)": "ok", "MeoW": "ok", "飞书": "ok"}
```

`watch`(见下文)没显式传通道参数时也用这套配置。

### 通道怎么申请

钉钉([文档](https://open.dingtalk.com/document/robots/custom-robot-access)):群设置 → 机器人 → 添加自定义机器人。安全设置选"自定义关键词"填`米家`就行(消息标题固定是"米家提醒"),或者选加签、把 SEC 开头的密钥配到 `DINGTALK_SECRET`。只能加内部群,限流每分钟 20 条。

飞书([文档](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot?lang=zh-CN)):群设置 → 群机器人 → 自定义机器人,拿到 webhook 地址。签名校验密钥配 `FEISHU_SECRET`,或者关键词同样填`米家`。webhook 地址别提交到公开仓库,泄露了谁都能往群里发。

MeoW([文档](https://www.chuckfang.com/MeoW/api_doc.html)):鸿蒙手机装 MeoW,注册个昵称,昵称直接填 `MEOW` 就行。自建服务的话填完整 URL。

Bark([文档](https://bark.day.app/)):iPhone 装 Bark,app 里复制 device key 填 `BARK`;自建服务器把 key 拼在 URL 里,填 `https://你的服务器/你的key`。

ntfy([文档](https://docs.ntfy.sh/)):安卓/桌面装 ntfy,订阅一个自取的 topic 名,topic 填 `NTFY`;自建填 `https://你的服务器/topic`(必须带 topic 段)。注意 ntfy.sh 的公开 topic 谁都能订阅,名字取长取随机。

鸿蒙/苹果/安卓三端都要收的话,MeoW + Bark + ntfy 三个都配上,`send_notification` 一次全推。

## 开控制

```bash
# 普通设备可控,锁/摄像头/燃气阀还是拦着
mijia-home-mcp serve --enable-control

# 白名单,glob 匹配设备名/did/model
mijia-home-mcp serve --enable-control --allow "客厅*" --allow "*台灯*"

# 黑名单优先
mijia-home-mcp serve --enable-control --deny "*camera*"
```

几条规则,都是故意的:

- 危险设备(锁/摄像头/燃气与水阀/保险柜)不吃通配符。`--allow "*"` 放行不了门锁,必须把设备名或 did 完整写进 `--allow`,或者 `--allow-dangerous`。
- `run_speaker_command` 是让小爱执行自然语言指令,等于能操作全屋任何设备,绕过白名单,所以按危险设备同样对待。纯播报用 `speaker_announce`,那个只出声音,按普通设备管。
- `run_scene` 不看设备白名单——场景是你自己在米家 App 里定义的,内容管不着。不想让模型碰的操作别做成手动场景。
- 所有写操作(包括被拒的)追加到 `~/.config/mijia-home-mcp/audit.log`。

## 局域网部署

```bash
mijia-home-mcp serve --transport http --host 0.0.0.0 --port 8423 --http-token 随机串
claude mcp add --transport http mijia-home http://<host>:8423/mcp --header "Authorization: Bearer 随机串"
```

不设 `--http-token` 就没有鉴权,同网段谁都能连,启动时会警告。别暴露公网。

## 工具

读(始终有):

- `get_home_snapshot` — 全屋快照,可按 home/room 过滤,detail=full 给原始值和更新时间。30 秒内重复调用直接吃缓存(结果里带 `cached: true`)
- `get_home_changes` — 距上次调用的变化 diff
- `query_history` — 查本地事件历史,"今天门开过几次"这种。数据来自 watch 运行期间的记录,保留 30 天
- `get_battery_report` — 电量普查,低的排前面
- `get_device_statistics` — 耗电量/使用时长这类历史统计
- `list_homes` / `list_devices` / `get_device_status` / `get_device_spec` / `list_scenes` / `list_consumables`
- `auth_status` / `login` / `login_status` — 凭证过期时对话里就能重新扫码
- `send_notification` — 见上文,配了通道才有

控制(要 `--enable-control`):`set_device_property`、`run_device_action`、`run_scene`、`speaker_announce`、`run_speaker_command`。

还有个 prompt `home_briefing`,生成一份全屋简报。

### Resources

除了工具,还挂了三个 MCP resource,适合支持 resource 的客户端(OpenClaw 会转成 `resources_read` 工具,Claude Code 里 `@` 引用):

- `mijia://devices` — 设备清单(名/did/model/在线/位置),几乎不变,随便读
- `mijia://homes` — 家庭与房间结构
- `mijia://snapshot` — 实时快照,读取会拉云端(几秒),30s 缓存兜底

配置了默认家庭的话 resources 同样只看那个家。

## 命令行

CLI 本身能单独用,不需要 MCP 客户端:

```bash
mijia-home-mcp doctor        # 自检
mijia-home-mcp snapshot      # 全屋状态,--home/--room/--full/--json
mijia-home-mcp devices
mijia-home-mcp battery
mijia-home-mcp say "下楼取快递"
mijia-home-mcp watch         # 持续监控,变化实时打印
```

watch 长这样:

```text
[14:26:04] 基线已建立,开始监控…
[14:26:38] Mijia Smart Tabletop Dishwasher S2: left-time 159 → 158
```

加通知参数可以边监控边推送:

```bash
mijia-home-mcp watch --speak --meow 昵称 \
  --only "门锁*" --only "*传感器*" --ignore left-time
```

`--ignore left-time` 这种很有必要,不然洗碗机倒计时每分钟推一条。钉钉/飞书收到的是卡片(逐条变化列表),MeoW 收到一句话摘要,`--webhook` 收到完整 diff JSON(带 `text` 字段,接 Bark/ntfy 可以直接用)。watch 的变化同时会写进本地历史,供 `query_history` 查。

watch 还会每小时查一次耗材,状态跃迁(充足→不足→耗尽,以及换新后的恢复)当成普通变化事件走同一套通知/历史管道——"扫地机的耗材滤网不足了"会推到你手机上。`--only`/`--ignore` 对耗材事件同样生效。

## 环境变量

CLI 参数和环境变量等价,参数优先:

| 环境变量 | 说明 |
|---|---|
| `MIJIA_HOME_MCP_AUTH` | 认证文件路径,默认 `~/.config/mijia-api/auth.json` |
| `MIJIA_HOME_MCP_ENABLE_CONTROL` | `1`/`true` 开控制 |
| `MIJIA_HOME_MCP_ALLOW` / `_DENY` | 白/黑名单,逗号分隔 |
| `MIJIA_HOME_MCP_ALLOW_DANGEROUS` | 放行危险设备 |
| `MIJIA_HOME_MCP_STATE_DIR` | 状态目录(diff 基线/历史/审计日志),默认 `~/.config/mijia-home-mcp` |
| `MIJIA_HOME_MCP_HOME_NAME` | 默认家庭(`--home`),多家庭账号用 |
| `MIJIA_HOME_MCP_SPEAKER` | 小爱音箱名,`auto` 用第一台 |
| `MIJIA_HOME_MCP_MEOW` | MeoW 昵称或完整 URL(鸿蒙) |
| `MIJIA_HOME_MCP_BARK` | Bark device key 或自建 URL(iOS) |
| `MIJIA_HOME_MCP_NTFY` | ntfy topic 或自建 URL(安卓/桌面) |
| `MIJIA_HOME_MCP_FEISHU` / `_FEISHU_SECRET` | 飞书 webhook / 签名密钥 |
| `MIJIA_HOME_MCP_DINGTALK` / `_DINGTALK_SECRET` | 钉钉 webhook / 加签密钥 |
| `MIJIA_HOME_MCP_WEBHOOK` | 通用 webhook |
| `MIJIA_HOME_MCP_HTTP_TOKEN` | http 传输的 Bearer token |

## 已知问题

- 全部走小米云端(上游是逆向的接口),读状态有秒级延迟,没有推送,别把轮询间隔调太小。
- 凭证大约一个月过期,重新扫码(`login` 命令或对话里调 `login` 工具)。
- 红外类设备(比如空调伴侣里的"空调")的 spec 页面缺中文数据,上游解析会挂,这类设备在快照的 `attention.spec_errors` 里,状态拿不到,其他设备不受影响。
- `get_home_changes` 的基线按 home 参数分开存,这次传 home 下次不传的话是两条独立的基线。
- 工具上的 `readOnlyHint` 注解只是给客户端的提示,真正的门在服务端。

## 开发

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest
```

测试全离线(假 API + 磁盘 spec 缓存),不需要米家账号。

## 许可

GPL-3.0-or-later。上游 mijia-api 是 GPL-3.0 且声明仅供学习交流,本项目一样。
