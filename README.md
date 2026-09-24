# NVanillaaaaBot

基于 [NcatBot 5](https://docs.ncatbot.xyz/) 的 QQ 群消息转发机器人。已移除 ghbot、GitHub Webhook 通知和 Flask 依赖。

## 安装与运行

使用 Python 3.12.11 和 uv。从项目根目录运行：

```bash
git submodule update --init plugins/forwardbot
uv sync --locked
uv run python main.py
```

也可以用 `uv run ncatbot run` 启动。先在 `config.yaml` 填写 `bot_uin`（机器人 QQ）和 `root`（超级管理员 QQ），并设置 `adapters` 中 NapCat 的 WebSocket 地址和 Token。

`plugin.load_plugin` 必须为 `true`。外部已部署的 NapCat 可设置 `adapters[].config.skip_setup: true`，由 NcatBot 直接连接；否则使用框架的 NapCat 初始化流程。

## 从 NcatBot 4 升级

停止旧进程，更新代码和依赖，再对部署环境原有的配置执行：

```bash
uv sync --locked
uv run python migrate_config.py
```

迁移脚本将 `bt_uin` 改为 `bot_uin`、`napcat` 改为 `adapters`、`skip_plugin_load` 改为 `load_plugin`，并转换 NapCat 更新检查及远程连接选项。原文件保存为 `config.yaml.v4.bak`，不会覆盖已有备份。

如果全局配置的 `ForwardBotPlugin` 缺失或为空，脚本会从 `data/ForwardBotPlugin/ForwardBotPlugin.yaml` 导入规则、管理员和开关；`send_interval` 改为 `send_interval_ms`。已经存在的新版插件配置优先，重复运行不覆盖它。旧版仅通过 RBAC 命令授予、未写入 `admins` 的管理员，需要在新版配置的 `admins` 中补充。

ghbot 不再被加载，也不再监听 Webhook 端口；旧部署里的 `plugins/ghbot` 和 `gh_config.yaml` 可移除。不要重新初始化旧的 ghbot 子模块。

## 转发配置

编辑全局 `config.yaml` 的 `plugin.plugin_configs.ForwardBotPlugin`：

```yaml
plugin:
  load_plugin: true
  plugin_configs:
    ForwardBotPlugin:
      enabled: true
      send_interval_ms: 500
      admins: ["123456789"]
      rules:
        - name: 重要通知
          enabled: true
          type: prefix
          source_groups: [123456789]
          target_groups: [987654321]
          keywords: ["重要", "紧急"]
          forward_prefix: "[转发]"
```

`type` 支持 `prefix`（前缀）或 `keyword`（包含关键词）。未覆盖的值来自插件目录的 `config.yaml`。转发使用 QQ 单条消息转发接口，保留原消息内容；`forward_prefix` 为兼容旧规则而保留，不会添加到原消息中。

命令仍使用 `/forward ...`：

- `/forward help`：帮助。
- `/forward stats [-v|--verbose]`：统计。
- `/forward rules list [-d|--detailed]`：列出规则。
- `/forward rules enable|disable <规则名>`：切换规则状态。
- `/forward rules delete <规则名> [-f|--force]`：先显示确认信息，带 `-f` 才删除。
- `/forward admins add <QQ号>`：添加管理员。

含空格的规则名称用引号包裹。规则管理需要转发管理员或 root 权限，管理员添加仅限 root；命令仅响应 QQ 群聊。规则修改和管理员添加会立即保存到全局配置。

## 验证

```bash
uv run python -m unittest discover -s tests -v
```

测试使用 NcatBot 5 的 `PluginTestHarness` 和 Mock QQ 适配器，覆盖真实插件发现/加载、事件路由、转发、权限、命令选项、配置保存/重载和失败重试。测试仅在临时目录写入数据，不连接真实 QQ/NapCat。

## 子模块维护

`plugins/forwardbot` 仍是独立 Git 子模块。发布迁移时先提交并推送该子模块的修改，再在本仓库提交新的子模块引用，否则其他机器仍会获取旧版插件。
