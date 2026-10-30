import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import import_text as itx  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


def run(args):
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        rc = itx.main(args)
    return rc, out.getvalue()


def read(path):
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines()]


class TextTests(unittest.TestCase):
    def test_split(self):
        self.assertEqual(itx.split_text("a\n\n\nb\n \nc", "paragraphs"), ["a", "b", "c"])
        self.assertEqual(itx.split_text("a\n\nb", "none"), ["a\n\nb"])

    def test_single_record_per_file(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "o.jsonl"
            rc, _ = run([str(FIX / "note.txt"), "--out", str(out), "--date", "2024-03-12",
                         "--channel", "post"])
            self.assertEqual(rc, 0)
            (rec,) = read(out)
            self.assertEqual(rec["channel"], "post")
            self.assertEqual(rec["thread"], "note")
            self.assertEqual(rec["date"], "2024-03-12")
            self.assertTrue(rec["is_me"])
            self.assertIn("[REDACTED:email]", rec["text"])
            self.assertIn("\n\n", rec["text"])

    def test_paragraphs_and_mtime(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "o.jsonl"
            run([str(FIX / "note.txt"), "--out", str(out), "--split", "paragraphs",
                 "--thread", "diary"])
            recs = read(out)
            self.assertEqual(len(recs), 2)
            self.assertEqual(recs[0]["thread"], "diary")
            self.assertRegex(recs[0]["date"], r"^\d{4}-\d{2}-\d{2}T")

    def test_directory_and_filter(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "src"
            (src / "sub").mkdir(parents=True)
            (src / "a.md").write_text("alpha", encoding="utf-8")
            (src / "sub" / "b.txt").write_text("beta", encoding="utf-8")
            (src / "c.json").write_text("{}", encoding="utf-8")
            out = Path(d) / "o.jsonl"
            run([str(src), "--out", str(out), "--date", "2024-01-01"])
            self.assertEqual(len(read(out)), 2)
            run([str(src), "--out", str(out), "--date", "2024-01-01", "--since", "2024-02-01"])
            self.assertEqual(len(read(out)), 2)

    def test_bad_channel_and_missing(self):
        with self.assertRaises(SystemExit):
            run([str(FIX / "note.txt"), "--channel", "spoken"])
        self.assertEqual(run(["/nonexistent/file.txt"])[0], 1)


class ZeroRecordTests(unittest.TestCase):
    def test_filtered_out_keeps_output(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "x.jsonl"
            out.write_text("keep\n")
            rc, text = run([str(FIX / "note.txt"), "--out", str(out), "--date", "2024-01-01",
                            "--since", "2099-01-01"])
            self.assertEqual(rc, 0)
            self.assertIn("left untouched", text)
            self.assertEqual(out.read_text(), "keep\n")


if __name__ == "__main__":
    unittest.main()
