import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import import_transcripts as it  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
CALLS = FIX / "calls"


def run(args):
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        rc = it.main(args)
    return rc, out.getvalue()


def read(path):
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines()]


class ParseTests(unittest.TestCase):
    def test_line_formats(self):
        self.assertEqual(it.parse_line("**[00:00:01] Me:** Hello"), ("Me", "Hello"))
        self.assertEqual(it.parse_line("[00:01:00] Alex: Hi"), ("Alex", "Hi"))
        self.assertEqual(it.parse_line("00:01:10 Jo: Hi"), ("Jo", "Hi"))
        self.assertEqual(it.parse_line("Alex: Hi"), ("Alex", "Hi"))
        self.assertIsNone(it.parse_line("just some words"))

    def test_generic_turns_merge_and_continuation(self):
        turns = it.parse_transcript((FIX / "generic_transcript.txt").read_text(encoding="utf-8"))
        self.assertEqual(turns[0], ("Alex", "First line of the call. continued on a second line."))
        self.assertEqual([t[0] for t in turns], ["Alex", "Jo", "Alex"])

    def test_collapse_loops(self):
        self.assertEqual(it.collapse_loops("Thank you. Thank you. Thank you. Thank you. Bye."),
                         "Thank you. Bye.")
        self.assertEqual(it.collapse_loops("No. No. Fine."), "No. No. Fine.")

    def test_folder_date(self):
        self.assertEqual(it.folder_date("2024-03-12_1030_kickoff"), "2024-03-12T10:30:00")
        self.assertEqual(it.folder_date("2024-03-12_x"), "2024-03-12")
        self.assertIsNone(it.folder_date("kickoff"))


class CliTests(unittest.TestCase):
    def test_no_me_lists_speakers(self):
        rc, text = run([str(CALLS)])
        self.assertEqual(rc, 2)
        self.assertIn("Me: ", text)
        self.assertIn("Sam Lee: ", text)
        self.assertNotIn("budget", text)

    def test_import_parent_folder(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "t.jsonl"
            rc, _ = run([str(CALLS), "--me", "Me", "--out", str(out)])
            self.assertEqual(rc, 0)
            recs = read(out)
            self.assertEqual(len(recs), 3)
            self.assertEqual(recs[0]["thread"], "Project kickoff")
            self.assertEqual(recs[0]["date"], "2024-03-12T10:30:00")
            self.assertEqual(recs[0]["channel"], "spoken")
            self.assertEqual(recs[0]["recipient"], "Sam Lee")
            self.assertIn("Thank you.", recs[1]["text"])
            self.assertEqual(recs[1]["text"].count("Thank you"), 1)

    def test_filters(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "t.jsonl"
            run([str(CALLS), "--me", "Me", "--out", str(out), "--exclude-source", "ZOOM"])
            self.assertEqual({r["thread"] for r in read(out)}, {"Standup"})
            run([str(CALLS), "--me", "Me", "--out", str(out), "--language", "en"])
            self.assertEqual({r["thread"] for r in read(out)}, {"Project kickoff"})
            run([str(CALLS), "--me", "Me", "--out", str(out), "--since", "2024-03-13"])
            self.assertEqual({r["thread"] for r in read(out)}, {"Standup"})

    def test_keep_others_redacts(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "t.jsonl"
            run([str(CALLS / "2024-03-12_1030_kickoff"), "--me", "me", "--keep-others",
                 "--out", str(out)])
            recs = read(out)
            others = [r for r in recs if not r["is_me"]]
            self.assertEqual(len(others), 2)
            self.assertIn("[REDACTED:phone]", others[1]["text"])
            self.assertEqual(others[0]["recipient"], "Me")

    def test_generic_file(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "t.jsonl"
            run([str(FIX / "generic_transcript.txt"), "--me", "Alex", "--out", str(out)])
            recs = read(out)
            self.assertEqual(len(recs), 2)
            self.assertEqual(recs[0]["thread"], "generic_transcript")
            self.assertIsNone(recs[0]["date"])


if __name__ == "__main__":
    unittest.main()
