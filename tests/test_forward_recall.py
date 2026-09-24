import asyncio
import copy
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml

ROOT = Path(__file__).resolve().parents[1]


class ForwardRecallTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(os.chdir, Path.cwd())
        os.chdir(self.temp.name)
        source = ROOT / "plugins/forwardbot"
        target = Path("plugins/forwardbot")
        target.mkdir(parents=True)
        for path in source.iterdir():
            if path.suffix in (".py", ".yaml") or path.name == "manifest.toml":
                shutil.copy2(path, target / path.name)
        config_path = Path(self.temp.name) / "config.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "bot_uin": "10001",
                    "root": "42",
                    "check_ncatbot_update": False,
                    "plugin": {
                        "load_plugin": True,
                        "hot_reload": False,
                        "plugin_configs": {
                            "ForwardBotPlugin": {
                                "send_interval_ms": 0,
                                "rules": [
                                    {
                                        "name": "test",
                                        "type": "keyword",
                                        "source_groups": [100200],
                                        "target_groups": [100200, 200300, 300400],
                                        "keywords": ["重要", "撤回"],
                                    }
                                ],
                            },
                        },
                    },
                }
            )
        )
        from ncatbot.testing import PluginTestHarness
        from ncatbot.testing.factories import qq
        from ncatbot.types.napcat import MessageData, SendMessageResult
        from ncatbot.utils import get_config_manager

        self.qq = qq
        self.Result = SendMessageResult
        get_config_manager(str(config_path))
        self.harness = PluginTestHarness(["ForwardBotPlugin"], Path("plugins"))
        self.addAsyncCleanup(self.harness.stop)
        await self.harness.start()
        self.plugin = self.harness.get_plugin("ForwardBotPlugin")
        self.assertIsNotNone(self.plugin)
        self.module = sys.modules[type(self.plugin).__module__]
        self.api = self.harness.mock_api
        self.api.set_response(
            "get_msg",
            MessageData(
                message_id="123",
                message=[{"type": "text", "data": {"text": "重要通知"}}],
            ),
        )
        self.next_id = 1000
        self.sent = []
        self.action_times = []

        async def send_group_msg(group_id, message, **kwargs):
            self.next_id += 1
            message_id = str(self.next_id)
            self.api._record(
                "send_group_msg", group_id=group_id, message=message, **kwargs
            )
            self.sent.append((str(group_id), message_id, copy.deepcopy(message)))
            self.action_times.append(time.monotonic())
            return SendMessageResult(message_id=message_id)

        self.api.send_group_msg = send_group_msg

    def event(self, text, *, group_id="100200", user_id="99999", message_id="123"):
        from ncatbot.event.qq import GroupMessageEvent

        data = self.qq.group_message(
            text, group_id=group_id, user_id=user_id, message_id=message_id
        )
        return GroupMessageEvent(data, self.plugin.api.qq)

    async def inject(self, text, **kwargs):
        await self.harness.inject(self.qq.group_message(text, **kwargs))
        await self.harness.settle()

    def receipt(self):
        return self.sent[-1][2]

    def receipt_text(self):
        return "".join(
            seg["data"]["text"] for seg in self.receipt() if seg["type"] == "text"
        )

    def token(self):
        self.assertEqual(len(self.plugin._recall_cache), 1)
        return next(iter(self.plugin._recall_cache))

    async def test_real_dispatch_receipt_and_recall_by_another_member(self):
        await self.inject("重要通知", message_id="123")
        token = self.token()
        self.assertRegex(token, r"^[0-9a-f]{8}$")
        self.assertIn(f"撤回{token}", self.receipt_text())
        self.assertIn({"type": "reply", "data": {"id": "123"}}, self.receipt())
        self.assertEqual(
            [item[0] for item in self.sent], ["200300", "300400", "100200"]
        )
        entry = self.plugin._recall_cache[token]
        forwarded_ids = [item[1] for item in self.sent[:2]]
        self.assertEqual(entry.message_ids, forwarded_ids)
        self.assertEqual(entry.source_group, "100200")
        self.assertGreater(entry.expires_at - time.monotonic(), 119)
        self.assertFalse(self.api.called("set_msg_emoji_like"))
        await self.inject(f"撤回{token}", user_id="54321")
        self.assertEqual(
            [call.params["message_id"] for call in self.api.get_calls("delete_msg")],
            forwarded_ids,
        )
        self.assertEqual(self.plugin._recall_cache, {})
        self.assertIn("已撤回 2/2", self.receipt_text())
        # 撤回指令即使命中关键词，也不会被再次转发。
        self.assertEqual(sum(group != "100200" for group, _, _ in self.sent), 2)

    async def test_wrong_group_unknown_code_and_expiry_cannot_recall(self):
        await self.inject("重要通知")
        token = self.token()
        await self.inject(f"撤回{token}", group_id="55555")
        self.assertFalse(self.api.called("delete_msg"))
        self.assertIn(token, self.plugin._recall_cache)
        await self.inject("撤回ffffffff")
        self.assertFalse(self.api.called("delete_msg"))
        self.plugin._recall_cache[token].expires_at = time.monotonic() - 1
        await self.inject(f"撤回{token}")
        self.assertFalse(self.api.called("delete_msg"))
        self.assertNotIn(token, self.plugin._recall_cache)
        self.assertIn("已过期", self.receipt_text())

    async def test_idle_expiry_and_close_clear_memory(self):
        self.plugin.recall_ttl = 0.02
        token = self.plugin._cache_forwarded("100200", ["9"])
        await asyncio.sleep(0.04)
        self.assertNotIn(token, self.plugin._recall_cache)
        token = self.plugin._cache_forwarded("100200", ["10"])
        handle = self.plugin._recall_cache[token].timer
        await self.plugin.on_close()
        self.assertEqual(self.plugin._recall_cache, {})
        self.assertTrue(handle.cancelled())

    async def test_partial_forward_and_partial_recall_preserve_only_failed_ids(self):
        original_send = self.api.send_group_msg

        async def send(group_id, message, **kwargs):
            if str(group_id) == "200300":
                raise RuntimeError("target unavailable")
            return await original_send(group_id, message, **kwargs)

        with patch.object(self.api, "send_group_msg", send):
            await self.plugin.onGroupMessageReceived(self.event("重要通知"))
        token = self.token()
        ids = self.plugin._recall_cache[token].message_ids.copy()
        self.assertEqual(len(ids), 1)
        self.assertIn("1 条转发失败", self.receipt_text())
        # 额外模拟同一原消息还有一个成功的转发。
        entry = self.plugin._recall_cache[token]
        entry.message_ids.append("extra-id")
        deadline = entry.expires_at
        original_delete = self.api.delete_msg

        async def delete(message_id):
            if message_id == "extra-id":
                raise RuntimeError("temporary failure")
            await original_delete(message_id)

        with patch.object(self.api, "delete_msg", delete):
            await self.plugin.recall_messages(self.event(f"撤回{token}"), token)
        self.assertEqual(entry.message_ids, ["extra-id"])
        self.assertEqual(entry.expires_at, deadline)
        await self.plugin.recall_messages(self.event(f"撤回{token}"), token)
        self.assertEqual(
            [c.params["message_id"] for c in self.api.get_calls("delete_msg")],
            ids + ["extra-id"],
        )
        self.assertNotIn(token, self.plugin._recall_cache)

    async def test_missing_id_and_failed_receipt_do_not_resend(self):
        original_send = self.api.send_group_msg

        async def no_id(group_id, message, **kwargs):
            result = await original_send(group_id, message, **kwargs)
            return self.Result(message_id="") if str(group_id) != "100200" else result

        with patch.object(self.api, "send_group_msg", no_id):
            await self.plugin.onGroupMessageReceived(self.event("重要通知"))
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(self.plugin._recall_cache, {})
        self.assertIn("未返回消息 ID", self.receipt_text())
        self.sent.clear()

        async def no_receipt(group_id, message, **kwargs):
            if str(group_id) == "100200":
                raise RuntimeError("cannot reply")
            return await original_send(group_id, message, **kwargs)

        with patch.object(self.api, "send_group_msg", no_receipt):
            await self.plugin.onGroupMessageReceived(self.event("重要通知"))
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(len(self.plugin._recall_cache[self.token()].message_ids), 2)

    async def test_concurrent_commands_do_not_delete_twice(self):
        await self.inject("重要通知")
        token = self.token()
        started = asyncio.Event()
        release = asyncio.Event()
        original_delete = self.api.delete_msg

        async def delete(message_id):
            started.set()
            await release.wait()
            await original_delete(message_id)

        with patch.object(self.api, "delete_msg", delete):
            first = asyncio.create_task(
                self.plugin.recall_messages(self.event("recall"), token)
            )
            await asyncio.wait_for(started.wait(), timeout=1)
            second = asyncio.create_task(
                self.plugin.recall_messages(
                    self.event("recall", user_id="54321"), token
                )
            )
            await asyncio.sleep(0)
            release.set()
            await asyncio.wait_for(asyncio.gather(first, second), timeout=1)
        self.assertEqual(self.api.call_count("delete_msg"), 2)
        self.assertTrue(any("正在撤回" in str(message) for _, _, message in self.sent))

    async def test_all_outbound_actions_share_interval(self):
        self.plugin.config["send_interval_ms"] = 20
        original_delete = self.api.delete_msg

        async def delete(message_id):
            self.action_times.append(time.monotonic())
            await original_delete(message_id)

        with patch.object(self.api, "delete_msg", delete):
            await self.plugin.onGroupMessageReceived(self.event("重要通知"))
            await self.plugin.recall_messages(self.event("recall"), self.token())
        self.assertEqual(len(self.action_times), 6)  # 两次转发、回执、两次撤回、回执
        for before, after in zip(self.action_times, self.action_times[1:]):
            self.assertGreaterEqual(after - before, 0.018)

    async def test_self_private_commands_and_unmatched_messages_are_ignored(self):
        for text, user in (
            ("重要通知", "10001"),
            ("普通消息", "99999"),
            ("/重要", "99999"),
        ):
            await self.inject(text, user_id=user)
        await self.harness.inject(self.qq.private_message("撤回1234abcd"))
        await self.harness.settle()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.plugin._recall_cache, {})

    async def test_collision_and_separate_sources_never_share_entries(self):
        with patch.object(
            self.module.secrets,
            "token_hex",
            side_effect=["1234abcd", "1234abcd", "5678abcd"],
        ):
            first = self.plugin._cache_forwarded("100200", ["1"])
            second = self.plugin._cache_forwarded("300400", ["2"])
        self.assertNotEqual(first, second)
        self.assertEqual(self.plugin._recall_cache[first].message_ids, ["1"])
        self.assertEqual(self.plugin._recall_cache[second].message_ids, ["2"])

    async def test_expiry_during_accepted_recall_finishes_all_targets(self):
        self.plugin.recall_ttl = 0.04
        self.plugin.config["send_interval_ms"] = 30
        token = self.plugin._cache_forwarded("100200", ["1", "2", "3"])
        await self.plugin.recall_messages(self.event("recall"), token)
        self.assertEqual(self.api.call_count("delete_msg"), 3)
        self.assertEqual(self.plugin._recall_cache, {})

    async def test_native_napcat_response_models_supply_deletable_ids(self):
        from ncatbot.adapter.napcat.api.bot_api import NapCatBotAPI
        from ncatbot.api.qq import QQAPIClient
        from types import SimpleNamespace

        responses = [
            {
                "status": "ok",
                "retcode": 0,
                "data": {
                    "message_id": 123,
                    "message": [{"type": "text", "data": {"text": "important"}}],
                },
            },
            {"status": "ok", "retcode": 0, "data": {"message_id": -98765}},
            {"status": "ok", "retcode": 0, "data": {}},
        ]
        protocol = SimpleNamespace(call=AsyncMock(side_effect=responses))
        qq_api = QQAPIClient(NapCatBotAPI(protocol))
        recall_event = self.event("recall")
        with patch.object(self.plugin.api, "_platforms", {"qq": qq_api}):
            target_id = await self.plugin.safe_forward_message(200300, "123", "test")
            self.assertEqual(target_id, "-98765")
            token = self.plugin._cache_forwarded("100200", [target_id])
            # 回复仍使用 Mock QQ，删除走真实 NapCat 适配器的序列化链路。
            await self.plugin.recall_messages(recall_event, token)
        self.assertEqual(
            protocol.call.call_args_list[-1].args,
            ("delete_msg", {"message_id": -98765}),
        )


if __name__ == "__main__":
    unittest.main()
