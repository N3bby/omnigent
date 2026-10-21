"""Progress bars agents show in the chat: the server's record of them and their logs.

Tests images/server/omnigent_progress.py on its own. The routes, the event and
the status and message hooks are tested against a patched upstream checkout
(tests/server/test_agent_progress.py in 0022-agent-progress.patch).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "images" / "server"))
import omnigent_progress as progress  # noqa: E402

SESSION = "conv_progress"
GAP = progress.TURN_GAP_S


class AgentProgressTest(unittest.TestCase):
    def setUp(self) -> None:
        progress._sessions.clear()
        progress._logs.clear()
        progress._log_chars = 0
        self.published: list[tuple[str, list[dict]]] = []
        progress.set_publisher(lambda sid, bars: self.published.append((sid, bars)))

    def tearDown(self) -> None:
        progress.set_publisher(None)
        progress._sessions.clear()
        progress._logs.clear()
        progress._log_chars = 0

    def bars(self, session: str = SESSION) -> list[dict]:
        return progress.snapshot(session) or []

    def test_an_update_is_kept_and_published(self) -> None:
        bar = progress.update(SESSION, {"label": "Running tests", "percent": 40}, now=10.0)

        self.assertIsNotNone(bar)
        self.assertTrue(bar["id"].startswith("bar_"))
        self.assertEqual(
            {k: bar[k] for k in ("label", "percent", "started_at", "outcome", "has_log")},
            {"label": "Running tests", "percent": 40.0, "started_at": 10.0, "outcome": None,
             "has_log": False},
        )
        self.assertEqual(self.bars(), [bar])
        self.assertEqual(self.published, [(SESSION, [bar])])

    def test_updates_in_a_turn_change_one_bar(self) -> None:
        first = progress.update(SESSION, {"label": "Migrating (1/4)", "percent": 25}, now=10.0)
        second = progress.update(SESSION, {"label": "Migrating (2/4)", "percent": 50}, now=20.0)

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["started_at"], 10.0)
        self.assertEqual(second["updated_at"], 20.0)
        self.assertEqual([b["label"] for b in self.bars()], ["Migrating (2/4)"])

    def test_the_end_of_a_turn_ends_its_bar_and_the_next_turn_starts_another(self) -> None:
        progress.update(SESSION, {"label": "Migrating", "percent": 50}, now=10.0)

        progress.on_status(SESSION, "idle", now=30.0)
        self.assertEqual((self.bars()[-1]["outcome"], self.bars()[-1]["ended_at"]), ("done", 30.0))
        progress.on_status(SESSION, "running", now=30.0 + GAP)
        progress.update(SESSION, {"label": "Writing docs"}, now=31.0 + GAP)

        bars = self.bars()
        self.assertEqual([b["label"] for b in bars], ["Migrating", "Writing docs"])
        self.assertEqual([b["outcome"] for b in bars], ["done", None])

    def test_a_short_pause_mid_turn_keeps_the_bar_going(self) -> None:
        # Claude's terminal reads as idle for a second between steps.
        progress.update(SESSION, {"label": "Migrating", "percent": 50}, now=10.0)

        progress.on_status(SESSION, "idle", now=20.0)
        progress.on_status(SESSION, "running", now=21.0)
        self.assertIsNone(self.bars()[-1]["outcome"])
        progress.on_status(SESSION, "idle", now=30.0)
        progress.update(SESSION, {"label": "Migrating", "percent": 60}, now=31.0)

        bars = self.bars()
        self.assertEqual(len(bars), 1)
        self.assertEqual((bars[0]["percent"], bars[0]["outcome"]), (60.0, None))

    def test_a_message_from_the_user_starts_a_new_bar(self) -> None:
        # Even when the next turn starts straight after the last one.
        progress.update(SESSION, {"label": "Migrating"}, now=10.0)
        progress.on_status(SESSION, "idle", now=20.0)
        progress.on_user_message(SESSION, now=21.0)
        progress.on_status(SESSION, "running", now=21.5)
        self.assertEqual(self.bars()[-1]["outcome"], "done")
        progress.update(SESSION, {"label": "Writing docs"}, now=22.0)

        self.assertEqual([b["label"] for b in self.bars()], ["Migrating", "Writing docs"])

    def test_a_failed_turn_marks_its_bar(self) -> None:
        progress.update(SESSION, {"label": "Migrating"}, now=10.0)

        progress.on_status(SESSION, "idle", now=20.0)
        progress.on_status(SESSION, "failed", now=20.5)

        self.assertEqual(self.bars()[-1]["outcome"], "failed")

    def test_clear_ends_the_bar_and_keeps_it(self) -> None:
        progress.update(SESSION, {"label": "Building"}, now=10.0)

        self.assertIsNone(progress.update(SESSION, {"clear": True}, now=12.0))
        self.assertEqual((self.bars()[-1]["outcome"], self.bars()[-1]["ended_at"]), ("done", 12.0))
        published = len(self.published)
        progress.update(SESSION, {"clear": True}, now=13.0)
        self.assertEqual(len(self.published), published)

    def test_the_label_is_tidied_and_cut(self) -> None:
        self.assertEqual(progress.parse({"label": "  Running\n the   tests "})["label"], "Running the tests")
        long = progress.parse({"label": "x" * 500})["label"]
        self.assertEqual(len(long), progress.MAX_LABEL_CHARS)
        self.assertTrue(long.endswith("…"))

    def test_a_malformed_update_says_why(self) -> None:
        for body in (
            [],
            {},
            {"label": " "},
            {"label": 3},
            {"label": "x", "percent": True},
            {"label": "x", "percent": "40"},
            {"label": "x", "percent": -1},
            {"label": "x", "percent": 100.5},
            {"label": "x", "percent": float("nan")},
            {"label": "x", "clear": "yes"},
            {"label": "x", "log_file": "logs/dev.log"},
            {"label": "x", "log_file": 7},
        ):
            with self.subTest(body=body), self.assertRaises(ValueError):
                progress.update(SESSION, body)
        self.assertIsNone(progress.snapshot(SESSION))
        self.assertEqual(self.published, [])

    def test_a_log_is_kept_and_read_after_a_line(self) -> None:
        bar = progress.update(
            SESSION, {"label": "Dev server", "log_file": "/home/omnigent/logs/dev.log"}, now=10.0
        )
        published = len(self.published)

        self.assertTrue(progress.append_log(SESSION, bar["id"], ["ready", "GET / 200"], now=11.0))
        self.assertTrue(progress.append_log(SESSION, bar["id"], ["GET /a 404"], now=12.0))

        page = progress.log_page(SESSION, bar["id"], after=1)
        self.assertEqual([line["text"] for line in page["lines"]], ["GET / 200", "GET /a 404"])
        self.assertEqual((page["next"], page["dropped"]), (3, False))
        self.assertEqual(page["log_files"], ["/home/omnigent/logs/dev.log"])
        # Only the first lines are published, to show the log is there.
        self.assertEqual(len(self.published), published + 1)
        self.assertTrue(self.published[-1][1][-1]["has_log"])

    def test_a_log_keeps_streaming_after_its_turn(self) -> None:
        bar = progress.update(SESSION, {"label": "Dev server"}, now=10.0)
        progress.on_status(SESSION, "idle", now=20.0)
        progress.update(SESSION, {"label": "Tests"}, now=100.0)

        self.assertTrue(progress.append_log(SESSION, bar["id"], ["GET / 200"], now=200.0))
        self.assertEqual(len(progress.log_page(SESSION, bar["id"])["lines"]), 1)

    def test_lines_for_an_unknown_bar_are_refused(self) -> None:
        self.assertFalse(progress.append_log(SESSION, "bar_missing", ["x"]))
        self.assertIsNone(progress.log_page(SESSION, "bar_missing"))

    def test_long_lines_are_cut_and_old_lines_dropped(self) -> None:
        bar = progress.update(SESSION, {"label": "Tests"}, now=10.0)

        progress.append_log(SESSION, bar["id"], ["y" * 5000])
        progress.append_log(SESSION, bar["id"], [f"line {n}" for n in range(progress.MAX_LOG_LINES)])

        page = progress.log_page(SESSION, bar["id"])
        self.assertEqual(len(page["lines"]), progress.MAX_LOG_LINES)
        self.assertEqual(page["lines"][0]["text"], "line 0")
        self.assertTrue(page["dropped"])
        self.assertEqual(progress._log_chars, sum(len(line["text"]) for line in page["lines"]))

    def test_the_oldest_logs_go_past_the_memory_budget(self) -> None:
        old_budget = progress.MAX_LOG_CHARS
        progress.MAX_LOG_CHARS = 100
        try:
            first = progress.update("conv_a", {"label": "A"}, now=1.0)
            second = progress.update("conv_b", {"label": "B"}, now=2.0)
            progress.append_log("conv_a", first["id"], ["a" * 60])
            progress.append_log("conv_b", second["id"], ["b" * 60])
        finally:
            progress.MAX_LOG_CHARS = old_budget

        self.assertEqual(progress.log_page("conv_a", first["id"])["lines"], [])
        self.assertTrue(progress.log_page("conv_a", first["id"])["dropped"])
        self.assertEqual(len(progress.log_page("conv_b", second["id"])["lines"]), 1)
        self.assertEqual(progress._log_chars, 60)

    def test_a_session_keeps_its_last_bars(self) -> None:
        for n in range(progress.MAX_BARS + 3):
            progress.update(SESSION, {"label": f"Turn {n}"}, now=n * 100.0)
            progress.on_user_message(SESSION, now=n * 100.0 + 50)

        labels = [b["label"] for b in self.bars()]
        self.assertEqual(len(labels), progress.MAX_BARS)
        self.assertEqual(labels[-1], f"Turn {progress.MAX_BARS + 2}")

    def test_the_least_recent_sessions_go_first(self) -> None:
        for n in range(progress.MAX_SESSIONS + 2):
            progress.update(f"conv_{n}", {"label": "Working"})

        self.assertEqual(len(progress._sessions), progress.MAX_SESSIONS)
        self.assertIsNone(progress.snapshot("conv_0"))
        self.assertIsNotNone(progress.snapshot(f"conv_{progress.MAX_SESSIONS + 1}"))


if __name__ == "__main__":
    unittest.main()
