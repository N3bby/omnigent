"""A message the web UI sends again is delivered once.

Tests images/server/omnigent_message_resend.py on its own; the patched
upstream route has its own test in
images/server/patches/0017-resend-dropped-message.patch.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "images" / "server"))
import omnigent_message_resend as resend  # noqa: E402

KEY = ("conv_resend", "user@example.com", "0123456789abcdef0123456789abcdef")


class Request:
    """Stands in for upstream's handler: counts calls, and can be held."""

    def __init__(self, answer: object = None, error: Exception | None = None) -> None:
        self.calls = 0
        self.answer = answer if answer is not None else {"queued": True, "pending_id": "pending_1"}
        self.error = error
        self.release = asyncio.Event()
        self.release.set()
        self.started = asyncio.Event()

    async def __call__(self) -> object:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        if self.error is not None:
            raise self.error
        return self.answer


class OnceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        resend.reset()

    async def test_a_copy_waits_for_the_first_and_gets_its_answer(self) -> None:
        request = Request()
        request.release.clear()
        first = asyncio.create_task(resend.once(KEY, request))
        await request.started.wait()
        copy = asyncio.create_task(resend.once(KEY, request))
        await asyncio.sleep(0)
        self.assertFalse(copy.done())
        request.release.set()
        answers = await asyncio.gather(first, copy)
        self.assertEqual(answers, [request.answer, request.answer])
        self.assertIsNot(answers[1], request.answer)
        self.assertEqual(request.calls, 1)

    async def test_a_later_copy_gets_the_answer_until_it_expires(self) -> None:
        request = Request()
        self.assertEqual(await resend.once(KEY, request), request.answer)
        self.assertEqual(await resend.once(KEY, request), request.answer)
        self.assertEqual(request.calls, 1)
        with mock.patch.object(resend.time, "monotonic", return_value=resend.time.monotonic() + resend.TTL_S + 1):
            await resend.once(KEY, request)
        self.assertEqual(request.calls, 2)

    async def test_other_keys_run_on_their_own(self) -> None:
        request = Request()
        await resend.once(KEY, request)
        await resend.once(KEY[:2] + ("fedcba9876543210fedcba9876543210",), request)
        await resend.once(("another session",) + KEY[1:], request)
        self.assertEqual(request.calls, 3)

    async def test_a_waiting_copy_gets_the_error_and_the_next_one_runs_again(self) -> None:
        request = Request(error=RuntimeError("runner unavailable"))
        request.release.clear()
        first = asyncio.create_task(resend.once(KEY, request))
        await request.started.wait()
        copy = asyncio.create_task(resend.once(KEY, request))
        await asyncio.sleep(0)
        request.release.set()
        for task in (first, copy):
            with self.assertRaisesRegex(RuntimeError, "runner unavailable"):
                await task
        request.error = None
        self.assertEqual(await resend.once(KEY, request), request.answer)
        self.assertEqual(request.calls, 2)

    async def test_a_waiting_copy_runs_when_the_first_is_cancelled(self) -> None:
        request = Request()
        request.release.clear()
        first = asyncio.create_task(resend.once(KEY, request))
        await request.started.wait()
        copy = asyncio.create_task(resend.once(KEY, request))
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.sleep(0)
        request.release.set()
        self.assertEqual(await copy, request.answer)
        self.assertEqual(request.calls, 2)
        with self.assertRaises(asyncio.CancelledError):
            await first

    async def test_old_answers_are_dropped_past_the_cap(self) -> None:
        request = Request()
        with mock.patch.object(resend, "MAX_ANSWERS", 2):
            for n in range(3):
                await resend.once(("key", n), request)
            await resend.once(("key", 0), request)
            await resend.once(("key", 2), request)
        self.assertEqual(request.calls, 4)


class ContentKeyTests(unittest.TestCase):
    def test_an_edited_message_has_another_key(self) -> None:
        text = [{"type": "input_text", "text": "hi"}]
        self.assertEqual(resend.content_key({"content": text, "stable_id": "a"}),
                         resend.content_key({"stable_id": "b", "content": [dict(reversed(text[0].items()))]}))
        self.assertNotEqual(resend.content_key({"content": text}),
                            resend.content_key({"content": [{"type": "input_text", "text": "hi!"}]}))
        self.assertEqual(resend.content_key(None), "null")


class StableIdTests(unittest.TestCase):
    def test_only_the_web_ui_stable_ids_count(self) -> None:
        self.assertEqual(resend.stable_id({"stable_id": KEY[2]}), KEY[2])
        for data in ({}, {"stable_id": KEY[2].upper()}, {"stable_id": "short"},
                     {"stable_id": 7}, None, "text"):
            with self.subTest(data=data):
                self.assertIsNone(resend.stable_id(data))


if __name__ == "__main__":
    unittest.main()
