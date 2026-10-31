"""Copies of the files agents show in the chat: finding them, and keeping them.

Tests images/server/omnigent_chat_media.py on its own. The route, the sandbox
reads, the status hook and deleting a session are tested against a patched
upstream checkout (tests/server/test_chat_media.py in
0027-chat-media-copies.patch).
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "images" / "server"))
import omnigent_chat_media as media  # noqa: E402

SESSION = "conv_media"


class _Blobs:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.puts: list[str] = []

    def put(self, key: str, data: bytes) -> None:
        self.puts.append(key)
        self.data[key] = data

    def get(self, key: str) -> bytes:
        return self.data[key]

    def delete(self, key: str) -> None:
        self.data.pop(key, None)


class MediaPathsTest(unittest.TestCase):
    def test_workspace_paths_in_image_syntax(self) -> None:
        text = (
            "Here:\n\n![Sign-in](/home/omnigent/workspace/app/signin.png)\n"
            "![Dash](./shots/dash%20board.png \"title\") ![x](<shots/with space.png>)\n"
            "![Doc](file:///tmp/a%20b.png) and [a link](notes.md)"
        )
        self.assertEqual(
            media.media_paths(text),
            [
                "/home/omnigent/workspace/app/signin.png",
                "shots/dash board.png",
                "shots/with space.png",
                "/tmp/a b.png",
            ],
        )

    def test_urls_anchors_and_code_are_skipped(self) -> None:
        text = (
            "![web](https://example.com/a.png) ![data](data:image/png;base64,AAAA)\n"
            "![anchor](#top) ![proto](//cdn.example.com/a.png)\n"
            "`![span](span.png)`\n\n```md\n![fenced](fenced.png)\n```\n"
            "~~~\n![tilde](tilde.png)\n~~~\n![after](after.png)"
        )
        self.assertEqual(media.media_paths(text), ["after.png"])

    def test_query_fragment_and_repeats(self) -> None:
        text = "![a](shot.png?v=2) ![b](shot.png#x) ![c](./shot.png) ![d](100%.png)"
        self.assertEqual(media.media_paths(text), ["shot.png", "100%.png"])

    def test_a_cited_line_number_is_a_file_not_a_scheme(self) -> None:
        self.assertEqual(media.media_paths("![x](shot.png:12)"), ["shot.png:12"])


class ResolveTest(unittest.TestCase):
    root = "/home/omnigent/workspace/app"
    home = "/home/omnigent"

    def resolve(self, cited: str) -> media.Resolved | None:
        return media.resolve(cited, self.root, self.home)

    def test_inside_the_workspace_is_relative(self) -> None:
        self.assertEqual(self.resolve("shots/a.png"), media.Resolved("shots/a.png", False))
        self.assertEqual(
            self.resolve("/home/omnigent/workspace/app/shots/a.png"),
            media.Resolved("shots/a.png", False),
        )

    def test_outside_the_workspace_is_absolute(self) -> None:
        self.assertEqual(self.resolve("/tmp/a.png"), media.Resolved("/tmp/a.png", True))
        self.assertEqual(self.resolve("~/a.png"), media.Resolved("/home/omnigent/a.png", True))

    def test_paths_that_cant_name_a_file(self) -> None:
        for cited in ("", "../a.png", "a//b.png", "/home/omnigent/workspace/app", "/",
                      "/tmp/../etc/passwd", "~other/a.png", "a.png?x", "http://x/a.png"):
            self.assertIsNone(self.resolve(cited), cited)

    def test_an_absolute_path_needs_the_root(self) -> None:
        self.assertTrue(media.needs_environment("/tmp/a.png"))
        self.assertTrue(media.needs_environment("~/a.png"))
        self.assertFalse(media.needs_environment("a.png"))
        self.assertIsNone(media.resolve("/tmp/a.png", None, None))


class MediaCopiesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.blobs = _Blobs()
        self.copies = media.MediaCopies(self.blobs)

    def test_a_copy_is_kept_and_read_back(self) -> None:
        self.assertTrue(
            self.copies.put(SESSION, "shot.png", b"PNG", content_type="image/png",
                            owner_only=False, now=5.0)
        )
        entry, data = self.copies.get(SESSION, "shot.png")
        self.assertEqual(data, b"PNG")
        self.assertEqual(
            {k: entry[k] for k in ("content_type", "bytes", "captured_at", "owner_only")},
            {"content_type": "image/png", "bytes": 3, "captured_at": 5.0, "owner_only": False},
        )
        self.assertIsNone(self.copies.get(SESSION, "other.png"))

    def test_the_index_survives_a_restart(self) -> None:
        self.copies.put(SESSION, "shot.png", b"PNG", content_type="image/png", owner_only=True)
        again = media.MediaCopies(self.blobs)
        entry, data = again.get(SESSION, "shot.png")
        self.assertEqual(data, b"PNG")
        self.assertTrue(entry["owner_only"])

    def test_unchanged_bytes_arent_written_again(self) -> None:
        self.copies.put(SESSION, "a.png", b"A", content_type="image/png", owner_only=False, now=1)
        self.blobs.puts.clear()
        self.copies.put(SESSION, "a.png", b"A", content_type="image/png", owner_only=False, now=2)
        self.assertEqual(self.blobs.puts, [f"chat-media/{SESSION}/index.json"])
        self.assertEqual(self.copies.entry(SESSION, "a.png")["captured_at"], 2)
        self.copies.put(SESSION, "a.png", b"B", content_type="image/png", owner_only=False)
        self.assertEqual(self.copies.get(SESSION, "a.png")[1], b"B")

    def test_too_big_or_a_bad_session_isnt_kept(self) -> None:
        big = b"x" * (media.MAX_COPY_BYTES + 1)
        self.assertFalse(self.copies.put(SESSION, "a.mp4", big, content_type="video/mp4",
                                         owner_only=False))
        self.assertFalse(self.copies.put("../x", "a.png", b"A", content_type="image/png",
                                         owner_only=False))
        self.assertEqual(self.blobs.data, {})

    def test_forget_deletes_a_sessions_copies_only(self) -> None:
        self.copies.put(SESSION, "a.png", b"A", content_type="image/png", owner_only=False)
        self.copies.put(SESSION, "b.png", b"B", content_type="image/png", owner_only=False)
        self.copies.put("conv_other", "a.png", b"O", content_type="image/png", owner_only=False)

        self.assertEqual(media.MediaCopies(self.blobs).forget(SESSION), 2)

        self.assertTrue(all(k.startswith("chat-media/conv_other/") for k in self.blobs.data))
        self.assertIsNone(media.MediaCopies(self.blobs).get(SESSION, "a.png"))


class ScanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.blobs = _Blobs()
        self.copies = media.MediaCopies(self.blobs)
        self.replies: list[tuple[float, str]] = []
        self.fetched: list[str] = []
        self.files = {"a.png": b"A", "b.png": b"B"}

        async def fetch(session_id: str, cited: str) -> media.Fetched | None:
            self.fetched.append(cited)
            data = self.files.get(cited)
            return media.Fetched(data, "image/png", False) if data is not None else None

        media.configure(self.copies, lambda sid, n: list(self.replies), fetch)

    def tearDown(self) -> None:
        media.configure(None)

    def test_a_scan_keeps_what_replies_show_once(self) -> None:
        self.replies = [(10.0, "![a](a.png) ![gone](gone.png)"), (11.0, "![b](b.png)")]

        self.assertEqual(asyncio.run(media.scan(SESSION)), 2)
        self.assertEqual(self.fetched, ["a.png", "gone.png", "b.png"])
        self.assertEqual(self.copies.get(SESSION, "b.png")[1], b"B")

        self.fetched.clear()
        self.assertEqual(asyncio.run(media.scan(SESSION)), 0)
        self.assertEqual(self.fetched, ["gone.png"])

    def test_a_newer_reply_showing_a_kept_path_fetches_it_again(self) -> None:
        self.copies.put(SESSION, "a.png", b"old", content_type="image/png", owner_only=False,
                        now=10.0)
        self.replies = [(20.0, "![a](a.png)")]

        asyncio.run(media.scan(SESSION))

        self.assertEqual(self.copies.get(SESSION, "a.png")[1], b"A")

    def test_on_status_scans_once_a_turn_ends(self) -> None:
        self.replies = [(10.0, "![a](a.png)")]

        async def run() -> None:
            media.on_status(SESSION, "running")
            self.assertEqual(media._tasks, set())
            media.on_status(SESSION, "idle")
            media.on_status(SESSION, "idle")  # runs once more after the first
            await asyncio.gather(*list(media._tasks))

        asyncio.run(run())
        self.assertEqual(self.fetched, ["a.png"])
        self.assertEqual(media._scanning, set())

    def test_on_status_off_the_loop_does_nothing(self) -> None:
        media.on_status(SESSION, "idle")
        self.assertEqual(self.fetched, [])


if __name__ == "__main__":
    unittest.main()
