# NVanillaaaaBot

基于 [NcatBot 5](https://docs.ncatbot.xyz/) 的 QQ 群消息转发机器人。支持按规则转发，以及通过临时撤回码批量撤回。

## 安装与运行

使用 Python 3.12.11 和 uv，从项目根目录运行：

```bash
uv sync --locked
uv run python main.py
```

也可以用 `uv run ncatbot run` 启动。先在 `config.yaml` 填写 `bot_uin`（机器人 QQ）和 `root`，并设置 `adapters` 中 NapCat 的 WebSocket 地址和 Token。`plugin.load_plugin` 必须为 `true`。

外部已部署的 NapCat 可设置 `adapters[].config.skip_setup: true`，由 NcatBot 直接连接；否则使用框架的 NapCat 初始化流程。

## 转发配置

用户配置位于全局 `config.yaml` 的 `plugin.plugin_configs.ForwardBotPlugin`，未覆盖的值来自 `plugins/forwardbot/config.yaml`：

```yaml
plugin:
  load_plugin: true
  plugin_configs:
    ForwardBotPlugin:
      send_interval_ms: 500
      rules:
        - name: 重要通知
          type: prefix
          source_groups: [123456789]
          target_groups: [987654321, 555666777]
          keywords: ["重要", "紧急"]
          forward_prefix: "[转发]"
```

`type` 支持 `prefix`（前缀）或 `keyword`（包含关键词）。忽略机器人自己的消息、以 `/` 开头的消息和来源群自身作为转发目标的情况。`forward_prefix` 仅保留配置兼容性，不会追加到消息内容中。

## 消息撤回

一条源消息转发完毕后，机器人引用回复这条源消息，提供一个随机的 8 位十六进制撤回码，例如：

```text
已转发 2 条消息。
2 分钟内在本群发送「撤回a1b2c3d4」可撤回。
```

源群内任何成员均可发送 `撤回a1b2c3d4`。机器人根据缓存中该撤回码对应的所有目标消息 ID，逐条撤回，然后回复结果。撤回码不能在其他群使用，撤回指令本身不参与规则转发。

- 转发、源群回复及撤回共用 `send_interval_ms`，包括多个消息并发处理时。
- 两分钟从本批转发完成、生成缓存时开始计算；缓存到期自动清理。有效期内已受理的撤回会继续处理完全部目标。
- 多人同时使用同一撤回码时，只执行一批撤回。
- 部分撤回失败时，只保留失败的消息 ID，可在原有效期内使用同一码重试。不会延长有效期，也不会重复撤回已成功的消息。
- 缓存仅保存在内存中，重启或重载插件后失效。目标消息可能因平台限制或网络错误撤回失败，以返回结果为准。

为了获取目标消息 ID，使用 NcatBot 的 `send_group_forward_msg_by_id`：读取原消息的消息段，再发送到目标群，取得 `SendMessageResult.message_id`。原来的 `forward_group_single_msg` [不返回消息 ID](https://doc.napneko.icu/develop/api/doc)，因此不能用于建立可靠的撤回映射。实际显示和可发送的消息类型由 NapCat 决定；不再使用 emoji 确认转发。

转发失败最多重试两次；若接口未返回消息 ID，则报告无法通过撤回码撤回，不会因此重复发送。源群回执发送失败也不会重发目标消息。

## 验证

```bash
uv run python -m unittest discover -s tests -v
```

测试在临时目录中使用 NcatBot 5 的 `PluginTestHarness` 和 Mock QQ 适配器，覆盖事件分发、随机码与消息 ID 映射、源群限制、过期清理、部分失败重试、并发撤回、共享限速，以及 NapCat 响应模型和撤回请求的序列化。测试不连接真实 QQ/NapCat。
