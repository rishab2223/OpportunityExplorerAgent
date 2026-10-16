"""The kept Claude Code processes of an apply session (llm_claude.ClaudeSession).

A stand-in for the SDK's client: nothing here starts a real Claude process.
"""

from __future__ import annotations

import sys
import time
import types
import unittest
import unittest.mock

from pydantic import BaseModel

from src import llm, llm_claude
from src.config import AppConfig


class Answer(BaseModel):
    word: str


class Other(BaseModel):
    n: int


class FakeResult:
    def __init__(self, output):
        self.subtype = "success"
        self.result = ""
        self.structured_output = output


class FakeClient:
    started: list["FakeClient"] = []
    fail_connect = False
    fail_query = False

    def __init__(self, options=None):
        self.options = options
        self.sessions: list[str] = []
        self.prompts: list = []
        self.disconnected = False
        FakeClient.started.append(self)

    async def connect(self):
        if FakeClient.fail_connect:
            raise RuntimeError("could not start")

    async def query(self, prompt, session_id="default"):
        if FakeClient.fail_query:
            raise RuntimeError("process died")
        self.sessions.append(session_id)
        if not isinstance(prompt, str):
            # Streaming input: a user turn whose content carries pictures.
            turns = [m async for m in prompt]
            prompt = turns[0]["message"]["content"]
        self.prompts.append(prompt)
        self.prompt = prompt if isinstance(prompt, str) else prompt[-1]["text"]

    async def receive_response(self):
        yield FakeResult({"word": f"echo {self.prompt}"})

    async def disconnect(self):
        self.disconnected = True


def fake_sdk():
    return types.SimpleNamespace(ClaudeSDKClient=FakeClient, ResultMessage=FakeResult)


class ClaudeSessionTests(unittest.TestCase):
    def setUp(self):
        FakeClient.started = []
        FakeClient.fail_connect = FakeClient.fail_query = False
        patcher = unittest.mock.patch.dict(sys.modules, {"claude_agent_sdk": fake_sdk()})
        patcher.start()
        self.addCleanup(patcher.stop)
        options = unittest.mock.patch.object(llm_claude, "_options", lambda *a: a)
        options.start()
        self.addCleanup(options.stop)
        self.oneoff = []

        async def oneoff(system, user, schema, model, effort, images=None):
            self.oneoff.append(user)
            return schema(word="one-off")

        agent = unittest.mock.patch.object(llm_claude, "_agent_call", oneoff)
        agent.start()
        self.addCleanup(agent.stop)

    def test_one_process_answers_every_call_each_in_its_own_conversation(self):
        sess = llm_claude.ClaudeSession()
        sess.warm("sys", Answer, "opus", "low")
        first = sess.call("sys", "a", Answer, "opus", "low")
        second = sess.call("sys", "b", Answer, "opus", "low")
        sess.close()
        self.assertEqual((first.word, second.word), ("echo a", "echo b"))
        self.assertEqual(len(FakeClient.started), 1)
        ids = FakeClient.started[0].sessions
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(self.oneoff, [])

    def test_each_answer_is_followed_by_clear(self):
        # A fresh session_id alone does not forget the last call: the second
        # call answered with a word the first had been told (Oct 2026).
        sess = llm_claude.ClaudeSession()
        sess.call("sys", "a", Answer, "opus", "low")
        sess.call("sys", "b", Answer, "opus", "low")
        sess.close()
        client = FakeClient.started[0]
        deadline = time.monotonic() + 3
        while not client.disconnected and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(client.prompts, ["a", "/clear", "b", "/clear"])

    def test_pictures_go_ahead_of_the_text(self):
        sess = llm_claude.ClaudeSession()
        got = sess.call("sys", "look", Answer, "opus", "low", images=[b"PNG-1", b"PNG-2"])
        sess.close()
        self.assertEqual(got.word, "echo look")
        content = FakeClient.started[0].prompts[0]
        self.assertEqual([b["type"] for b in content], ["image", "image", "text"])
        self.assertEqual(content[0]["source"]["data"], "UE5HLTE=")   # base64 of PNG-1
        self.assertEqual(content[0]["source"]["media_type"], "image/png")

    def test_a_one_off_call_carries_the_pictures_too(self):
        FakeClient.fail_connect = True
        seen = []

        async def oneoff(system, user, schema, model, effort, images=None):
            seen.append(images)
            return schema(word="one-off")

        with unittest.mock.patch.object(llm_claude, "_agent_call", oneoff):
            sess = llm_claude.ClaudeSession(lambda _line: None)
            sess.call("sys", "a", Answer, "opus", "low", images=[b"x"])
            sess.close()
        self.assertEqual(seen, [[b"x"]])

    def test_another_schema_or_model_gets_its_own_process(self):
        sess = llm_claude.ClaudeSession()
        sess.warm("sys", Answer, "opus", "low")
        sess.warm("pick", Other, "sonnet", "medium")
        sess.warm("sys", Answer, "opus", "low")
        # warm() starts its process in the background: closing at once could
        # beat both starts (0 != 2, about one run in six under load).
        deadline = time.monotonic() + 3
        while len(FakeClient.started) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        time.sleep(0.1)                  # and a third, wrong start would show
        sess.close()
        self.assertEqual(len(FakeClient.started), 2)

    def test_a_process_that_will_not_start_falls_back_to_a_one_off_call(self):
        FakeClient.fail_connect = True
        lines = []
        sess = llm_claude.ClaudeSession(lines.append)
        got = sess.call("sys", "a", Answer, "opus", "low")
        sess.close()
        self.assertEqual(got.word, "one-off")
        self.assertIn("could not start", lines[0])

    def test_a_process_that_dies_is_replaced_then_given_up(self):
        FakeClient.fail_query = True
        sess = llm_claude.ClaudeSession(lambda _line: None)
        for user in ("a", "b", "c"):
            self.assertEqual(sess.call("sys", user, Answer, "opus", "low").word, "one-off")
        sess.close()
        # Two broken processes, then every call goes the one-off way.
        self.assertEqual(len(FakeClient.started), llm_claude.MAX_BROKEN)
        self.assertEqual(self.oneoff, ["a", "b", "c"])

    def test_close_ends_the_processes_and_later_calls_go_one_off(self):
        sess = llm_claude.ClaudeSession()
        sess.call("sys", "a", Answer, "opus", "low")
        sess.close()
        deadline = time.monotonic() + 3
        while not FakeClient.started[0].disconnected and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(FakeClient.started[0].disconnected)
        self.assertEqual(sess.call("sys", "b", Answer, "opus", "low").word, "one-off")


class OpenSessionTests(unittest.TestCase):
    def test_only_for_claude_on_the_agent_sdk(self):
        cfg = AppConfig()
        cfg.apply_provider = "openai"
        self.assertIsNone(llm.open_claude_session(cfg))
        cfg.apply_provider = "claude"
        cfg.claude.backend = "api"
        self.assertIsNone(llm.open_claude_session(cfg))

    def test_the_invoker_uses_the_session_and_can_warm_it(self):
        cfg = AppConfig()
        cfg.apply_provider = "claude"
        cfg.claude.backend = "agent-sdk"
        calls = []
        session = types.SimpleNamespace(
            call=lambda *a: calls.append(("call",) + a) or "ok",
            warm=lambda *a: calls.append(("warm",) + a))
        invoke = llm.make_invoker(cfg, None, "pick", session)
        invoke.warm("sys", Answer)
        self.assertEqual(invoke("sys", "user", Answer), "ok")
        self.assertEqual(calls[0], ("warm", "sys", Answer, cfg.claude.pick_model, cfg.claude.pick_effort))
        self.assertEqual(calls[1][:3], ("call", "sys", "user"))
        self.assertTrue(invoke.takes_images)
        invoke("sys", "user", Answer, images=[b"png"])
        self.assertEqual(calls[2][-1], [b"png"])


if __name__ == "__main__":
    unittest.main()
