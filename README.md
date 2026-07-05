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
| 部署 | clone + venv,Unix-first | PyPI + `uvx` 一行运行,Windows/macOS/Linux 一等支持 |

## 快速开始

1. 扫码登录(终端会打印二维码,用米家 App 扫描,凭证约一个月有效):

```bash
uvx mijia-home-mcp login
```

2. 添加到 Claude Code(只读模式):

```bash
claude mcp add mijia-home -- uvx mijia-home-mcp serve
```

或者写入项目 `.mcp.json` / Claude Desktop 配置:

```json
{
  "mcpServers": {
    "mijia-home": {
      "command": "uvx",
      "args": ["mijia-home-mcp", "serve"]
    }
  }
}
```

3. 问一句"家里现在什么情况",模型会调用 `get_home_snapshot`。

### 开启控制(可选)

```bash
# 允许控制普通设备(锁/摄像头/燃气与水阀/保险柜仍被拦截)
claude mcp add mijia-home -- uvx mijia-home-mcp serve --enable-control

# 只允许控制名单内设备(glob 匹配设备名/did/model,可多次传入)
uvx mijia-home-mcp serve --enable-control --allow "客厅*" --allow "*台灯*"

# 黑名单优先于白名单
uvx mijia-home-mcp serve --enable-control --deny "*camera*"

# 明确允许危险设备(不推荐)
uvx mijia-home-mcp serve --enable-control --allow-dangerous
```

安全策略细则:

- **危险设备只接受精确放行**:`--allow "*"` 这类通配白名单会放行普通设备,但锁/摄像头/燃气与水阀/保险柜必须把**完整设备名或 did** 精确写进 `--allow`(或使用 `--allow-dangerous`)才可控制。
- **`run_speaker_command` 按危险通道对待**:小爱语音指令可以触达全屋任意设备(包括门锁),会绕过设备白名单,因此默认拦截——需要把音箱名精确加入 `--allow` 或使用 `--allow-dangerous`。
- **`run_scene` 不受设备白名单约束**:场景内容是你在米家 App 预定义的动作组合,开启控制后即可执行;不想让 AI 碰的动作不要做成手动场景。
- 所有写操作(含被拒绝的尝试)都会追加到 `~/.config/mijia-home-mcp/audit.log`。

### 局域网部署(可选)

```bash
uvx mijia-home-mcp serve --transport http --host 0.0.0.0 --port 8423
```

```bash
claude mcp add --transport http mijia-home http://<host>:8423/mcp
```

> ⚠️ http 传输当前没有内置鉴权:监听 `0.0.0.0` 意味着同网段任何人都能读取(开启控制时还能操作)你的米家设备。只在可信局域网内使用并配合防火墙,切勿暴露公网。

## 工具一览

**读(始终可用):**

| 工具 | 用途 |
|---|---|
| `get_home_snapshot` | 全屋状态快照(compact/full 两档),附离线/低电量/故障提醒 |
| `get_home_changes` | 与上次快照对比,返回变化列表 |
| `list_homes` / `list_devices` | 家庭/房间/设备清单,支持过滤 |
| `get_device_status` | 单设备详细状态(批量拉取) |
| `get_device_spec` | 设备支持的属性/动作(名称、类型、范围、枚举值) |
| `list_scenes` / `list_consumables` | 手动场景 / 耗材状态 |
| `auth_status` / `login` / `login_status` | 认证状态与会话内扫码续期 |

**控制(需 `--enable-control`):**

| 工具 | 用途 |
|---|---|
| `set_device_property` | 设置属性(开关/亮度/模式…) |
| `run_device_action` | 执行动作(喂食/启动清扫…) |
| `run_scene` | 运行米家手动场景 |
| `run_speaker_command` | 让小爱音箱执行自然语言指令(默认静默) |

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

## 已知边界

- 走小米云端接口(上游 mijia-api 逆向实现),状态读取有秒级延迟,无本地直连与事件推送;请控制轮询频率。
- 凭证约一个月需重新扫码一次(`uvx mijia-home-mcp login`,或对话里直接调 `login` 工具)。
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
