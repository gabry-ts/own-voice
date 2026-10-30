import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import history_digest as hd  # noqa: E402

CLAUDE = str(Path(__file__).resolve().parent / "fixtures" / "history_claude")


def run(out, *extra):
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = hd.main(["--claude-dir", CLAUDE, "--out", str(out), *extra])
    return code, buf.getvalue()


class DigestTests(unittest.TestCase):
    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            code, out = run(d)
            self.assertEqual(code, 0)
            self.assertIn("shards written: 1", out)
            stats = json.loads((Path(d) / "stats.json").read_text())
            self.assertEqual(stats["sessions"], 4)
            self.assertEqual(dict(stats["top_skills"])["gaudit"], 2)
            self.assertEqual(dict(stats["top_tools"])["Bash"], 2)
            self.assertEqual(dict(stats["top_subagent_types"])["Explore"], 2)
            self.assertEqual(stats["subagent_models"], {"haiku": 1})
            self.assertEqual(stats["prompts_per_month"]["2025-01"], 2)
            self.assertEqual(dict(stats["top_slash_commands"])["/review"], 1)
            self.assertEqual(dict(stats["top_slash_commands"])["/gcommit"], 1)
            self.assertEqual(len(stats["sessions_per_project"]), 2)
            self.assertTrue(stats["repeated_prompt_openings"])
            text = (Path(d) / "digest_001.md").read_text()
            self.assertNotIn("ignore me", text)
            self.assertNotIn("abcd1234efgh5678ijkl", text)
            self.assertNotIn("alex.rivera@example.com", text)
            self.assertIn("REDACTED", text)
            self.assertNotIn("tool_result", text)
            self.assertLess(text.index("2025-01-10"), text.index("2025-03-01"))

    def test_history_dedupe(self):
        sessions = hd.collect(CLAUDE)
        hist = [s for s in sessions if s["source"] == "history"]
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["prompts"][0][1], "an old prompt only in history")

    def test_since(self):
        sessions = hd.collect(CLAUDE, since="2025-02-01")
        self.assertEqual(len(sessions), 2)

    def test_truncation(self):
        with tempfile.TemporaryDirectory() as d:
            run(d, "--max-prompt-chars", "50")
            text = (Path(d) / "digest_001.md").read_text()
            self.assertTrue(all(len(l) < 80 for l in text.splitlines() if l[:2] == "1."))

    def test_sharding_never_splits_small_sessions(self):
        sessions = hd.collect(CLAUDE)
        shards = hd.build_shards(sessions, 400, 700)
        self.assertGreater(len(shards), 1)
        for sh in shards:
            self.assertTrue(sh.startswith("## "))
        self.assertEqual(sum(sh.count("\n## ") + sh.startswith("## ") for sh in shards), len(sessions))

    def test_oversized_session_split(self):
        s = {"id": "abc", "project": "/p", "start": None, "end": None, "tools": hd.Counter(),
             "skills": hd.Counter(), "subagents": [], "commands": hd.Counter(), "source": "session",
             "prompts": [(None, "word " * 20) for _ in range(20)]}
        shards = hd.build_shards([s], 400, 300)
        self.assertGreater(len(shards), 1)
        self.assertTrue(all("(cont.)" in sh for sh in shards[1:]))

    def test_missing_dir(self):
        self.assertEqual(hd.main(["--claude-dir", "/nonexistent/zzz"]), 2)


class HeaderTests(unittest.TestCase):
    def test_history_header_is_clean(self):
        hist = [s for s in hd.collect(CLAUDE) if s["source"] == "history"][0]
        head, _ = hd.render_session(hist, 400)
        self.assertTrue(head[0].startswith("## history blog 2023-11-14 | "), head[0])
        real = [s for s in hd.collect(CLAUDE) if s["source"] != "history"][0]
        self.assertRegex(hd.render_session(real, 400)[0][0], r"^## \S{1,8} \| ")


if __name__ == "__main__":
    unittest.main()
