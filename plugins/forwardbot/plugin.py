import asyncio
import time

from ncatbot.core import registrar
from ncatbot.event.qq import GroupMessageEvent
from ncatbot.plugin import NcatBotPlugin

from .rules import ForwardRuleManager


class ForwardBotPlugin(NcatBotPlugin):
    name = "ForwardBotPlugin"
    version = "0.2.0"
    author = "KirisameVanilla"
    manager: ForwardRuleManager

    async def on_load(self) -> None:
        self.init_defaults(
            {
                "send_interval_ms": 500,
                "rules": [],
            }
        )
        self.manager = ForwardRuleManager(self.config)
        self._forward_lock = asyncio.Lock()
        self._last_forward_time = None
        if self.rbac is None:
            raise RuntimeError("转发插件需要 RBAC 服务")
        
    async def safe_forward_message(
        self, target_group: int, message_id: str, rule_name: str, max_retries: int = 2
    ) -> bool:
        """异步转发并限速；表情回应失败不会重发已成功的消息。"""
        async with self._forward_lock:
            for attempt in range(max_retries + 1):
                interval = (
                    max(0, float(self.get_config("send_interval_ms", 500))) / 1000
                )
                if self._last_forward_time is not None:
                    remaining = interval - (time.monotonic() - self._last_forward_time)
                    if remaining > 0:
                        await asyncio.sleep(remaining)
                try:
                    await self.api.qq.messaging.forward_group_single_msg(
                        target_group, message_id
                    )
                except Exception as exc:
                    self.logger.warning(
                        "转发失败: 群%s, 规则%s (尝试 %s/%s): %s",
                        target_group,
                        rule_name,
                        attempt + 1,
                        max_retries + 1,
                        exc,
                    )
                    if attempt < max_retries:
                        await asyncio.sleep(0.5)
                else:
                    try:
                        await self.api.qq.messaging.set_msg_emoji_like(
                            message_id, "124"
                        )
                    except Exception as exc:
                        self.logger.warning("消息已转发，但表情回应失败: %s", exc)
                    return True
                finally:
                    self._last_forward_time = time.monotonic()
        return False

    @registrar.qq.on_group_message()
    async def onGroupMessageReceived(self, event: GroupMessageEvent):
        message_id = event.message_id
        message = "".join(seg.text for seg in event.message.filter_text())

        if message.lstrip().startswith("/"):
            return

        sender_uin = event.sender.user_id
        if str(sender_uin) == str(event.self_id):
            self.logger.info(f"🟢 过滤掉来自自身的消息: {sender_uin}")
            return
        source_group = int(event.group_id)

        # 查找匹配的规则
        matching_rules = self.manager.find_matching_rules(message, source_group)

        if not matching_rules:
            # 只在调试模式下记录无匹配规则的消息
            self.logger.debug(f"📝 群 {source_group} 消息无匹配规则: {message[:50]}")
            return

        self.logger.info(
            f"📝 群 {source_group} 消息匹配到 {len(matching_rules)} 条规则: {message[:50]}"
        )

        forward_tasks = []
        for rule in matching_rules:
            for target_group in rule.target_groups:
                if rule.can_forward_to(source_group, target_group):
                    self.logger.info(
                        f"🚀 开始转发: {source_group} -> {target_group} (规则: {rule.name})"
                    )
                    success = await self.safe_forward_message(
                        target_group, message_id, rule.name
                    )
                    forward_tasks.append((target_group, success))
                else:
                    self.logger.debug(
                        f"🚫 规则 {rule.name} 不允许从 {source_group} 转发到 {target_group}"
                    )
