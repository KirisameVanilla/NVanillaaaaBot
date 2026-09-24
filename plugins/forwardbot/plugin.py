import asyncio
import re
import secrets
import time
from dataclasses import dataclass

from ncatbot.core import registrar
from ncatbot.event.qq import GroupMessageEvent
from ncatbot.plugin import NcatBotPlugin

from .rules import ForwardRuleManager


@dataclass
class RecallEntry:
    source_group: str
    message_ids: list[str]
    expires_at: float
    timer: asyncio.TimerHandle | None = None
    recalling: bool = False


class MissingForwardMessageID(RuntimeError):
    """发送可能已经成功，不能因为缺少回执 ID 而再次发送。"""


class ForwardBotPlugin(NcatBotPlugin):
    name = "ForwardBotPlugin"
    version = "0.3.0"
    author = "KirisameVanilla"
    recall_ttl = 120
    recall_command = re.compile(r"撤回\s*([0-9a-fA-F]{8})")
    manager: ForwardRuleManager

    async def on_load(self) -> None:
        self.init_defaults({"send_interval_ms": 500, "rules": []})
        self.manager = ForwardRuleManager(self.config)
        self._action_lock = asyncio.Lock()
        self._last_action_time = None
        self._recall_cache: dict[str, RecallEntry] = {}

    async def on_close(self) -> None:
        for entry in self._recall_cache.values():
            if entry.timer is not None:
                entry.timer.cancel()
        self._recall_cache.clear()

    async def _rate_limited(self, action, *args, **kwargs):
        """转发、回复和撤回共用同一把锁与全局发送间隔。"""
        async with self._action_lock:
            interval = max(0, float(self.get_config("send_interval_ms", 500))) / 1000
            if self._last_action_time is not None:
                remaining = interval - (time.monotonic() - self._last_action_time)
                if remaining > 0:
                    await asyncio.sleep(remaining)
            try:
                return await action(*args, **kwargs)
            finally:
                self._last_action_time = time.monotonic()

    async def _reply(self, event: GroupMessageEvent, text: str) -> None:
        try:
            await self._rate_limited(event.reply, text=text, at_sender=False)
        except Exception:
            # 回复失败不能触发重发，否则会产生不在撤回缓存中的重复消息。
            self.logger.exception("回复源群消息失败: 群%s", event.group_id)

    async def safe_forward_message(
        self, target_group: int, message_id: str, rule_name: str, max_retries: int = 2
    ) -> str | None:
        """转发单条消息并返回目标消息 ID；失败时最多重试两次。"""
        for attempt in range(max_retries + 1):
            try:
                # forward_group_single_msg 不返回 ID；此接口读取原消息段后发送，
                # 每次只传入一个原消息 ID，确保拿到该次发送的唯一消息 ID。
                result = await self._rate_limited(
                    self.api.qq.send_group_forward_msg_by_id,
                    target_group,
                    [message_id],
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
                continue

            forwarded_id = result.get("message_id") if result is not None else None
            if forwarded_id is None or str(forwarded_id) == "":
                raise MissingForwardMessageID(f"群{target_group}未返回转发消息 ID")
            return str(forwarded_id)
        return None

    def _forget_recall(self, recall_id: str, entry: RecallEntry) -> None:
        # 不删除意外碰撞后属于其他消息的条目。
        if self._recall_cache.get(recall_id) is entry:
            self._recall_cache.pop(recall_id)
        if entry.timer is not None:
            entry.timer.cancel()

    def _cache_forwarded(self, source_group: str, message_ids: list[str]) -> str:
        recall_id = secrets.token_hex(4)
        while recall_id in self._recall_cache:
            recall_id = secrets.token_hex(4)
        entry = RecallEntry(
            source_group=str(source_group),
            message_ids=list(message_ids),
            expires_at=time.monotonic() + self.recall_ttl,
        )
        self._recall_cache[recall_id] = entry
        entry.timer = asyncio.get_running_loop().call_later(
            self.recall_ttl, self._forget_recall, recall_id, entry
        )
        return recall_id

    async def recall_messages(self, event: GroupMessageEvent, recall_id: str) -> None:
        entry = self._recall_cache.get(recall_id)
        if entry is not None and time.monotonic() >= entry.expires_at:
            self._forget_recall(recall_id, entry)
            entry = None
        if entry is None or entry.source_group != str(event.group_id):
            await self._reply(event, "撤回码无效、已过期或不属于本群。")
            return
        if entry.recalling:
            await self._reply(event, "这条消息正在撤回，请勿重复操作。")
            return

        # 首次 await 前占用条目，防止多人同时撤回同一批消息。
        entry.recalling = True
        total = len(entry.message_ids)
        try:
            for message_id in list(entry.message_ids):
                try:
                    await self._rate_limited(
                        self.api.qq.messaging.delete_msg, message_id
                    )
                except Exception:
                    self.logger.exception("撤回失败: 消息%s", message_id)
                else:
                    # 只保留失败的 ID；有效期内再次发送同一码可重试。
                    entry.message_ids.remove(message_id)
        finally:
            entry.recalling = False
            if not entry.message_ids:
                self._forget_recall(recall_id, entry)

        failed = len(entry.message_ids)
        text = f"已撤回 {total - failed}/{total} 条转发消息。"
        if failed:
            text += f" {failed} 条撤回失败。"
            if time.monotonic() < entry.expires_at:
                text += "可在原两分钟有效期内使用同一撤回码重试。"
        await self._reply(event, text)

    @registrar.qq.on_group_message()
    async def onGroupMessageReceived(self, event: GroupMessageEvent):
        if str(event.user_id) == str(event.self_id):
            return
        message = "".join(seg.text for seg in event.message.filter_text())
        command = self.recall_command.fullmatch(message.strip())
        if command:
            await self.recall_messages(event, command[1].lower())
            return
        if message.lstrip().startswith("/"):
            return

        source_group = int(event.group_id)
        matching_rules = self.manager.find_matching_rules(message, source_group)
        if not matching_rules:
            return

        forwarded_ids = []
        failed = 0
        missing_ids = 0
        for rule in matching_rules:
            for target_group in rule.target_groups:
                if not rule.can_forward_to(source_group, target_group):
                    continue
                try:
                    forwarded_id = await self.safe_forward_message(
                        target_group, event.message_id, rule.name
                    )
                except MissingForwardMessageID as exc:
                    missing_ids += 1
                    self.logger.error("%s；不重发以免重复发送", exc)
                    continue
                if forwarded_id is not None:
                    forwarded_ids.append(forwarded_id)
                else:
                    failed += 1

        if forwarded_ids:
            recall_id = self._cache_forwarded(str(source_group), forwarded_ids)
            text = (
                f"已转发 {len(forwarded_ids)} 条消息。\n"
                f"2 分钟内在本群发送「撤回{recall_id}」可撤回。"
            )
        elif failed or missing_ids:
            text = "本次没有可缓存的转发消息 ID。"
        else:
            return
        if failed:
            text += f"\n{failed} 条转发失败。"
        if missing_ids:
            text += f"\n{missing_ids} 次转发未返回消息 ID，无法通过撤回码撤回。"
        await self._reply(event, text)
