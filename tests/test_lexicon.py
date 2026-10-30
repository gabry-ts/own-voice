import io
import json
import sys
import unittest
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import lexicon  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
CORPUS = str(FIX / "lexicon_corpus.jsonl")
TERMS = str(FIX / "lexicon_terms.txt")
DRAFT = str(FIX / "lexicon_draft.txt")


def run(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = lexicon.main(argv)
    return code, buf.getvalue()


class LexiconTests(unittest.TestCase):
    def test_register_grouping_needs_registers(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "c.json"
            cfg.write_text('{"registers": []}')
            for missing in (str(cfg), str(Path(d) / "missing.json")):
                for argv in (["count", "--terms", TERMS], ["top"]):
                    err = io.StringIO()
                    with redirect_stderr(err), redirect_stdout(io.StringIO()):
                        code = lexicon.main(argv + ["--corpus", CORPUS, "--by", "register", "--config", missing])
                    self.assertEqual(code, 2)
                    self.assertIn("config", err.getvalue())

    def test_terms_skip_comments(self):
        self.assertNotIn("# black list", lexicon.load_terms(TERMS))

    def test_count_by_channel(self):
        code, out = run(["count", "--terms", TERMS, "--corpus", CORPUS])
        data = json.loads(out)["groups"]
        self.assertEqual(data["email"]["terms"]["delve"]["count"], 2)
        self.assertEqual(data["email"]["terms"]["delve"]["messages"], 1)
        self.assertEqual(data["email"]["terms"]["delve into"]["count"], 1)
        self.assertEqual(data["chat"]["terms"]["delve"]["count"], 0)
        self.assertEqual(data["email"]["terms"]["utilizzare"]["count"], 0)
        self.assertGreater(data["email"]["terms"]["delve"]["per_1000"], 0)

    def test_whole_word(self):
        res = lexicon.count_terms(lexicon.load_corpus([CORPUS]), ["ok", "o"], by="none")
        self.assertEqual(res["groups"]["all"]["terms"]["o"]["count"], 0)
        self.assertEqual(res["groups"]["all"]["terms"]["ok"]["count"], 8)

    def test_accent_insensitive(self):
        base = json.loads(run(["count", "--terms", TERMS, "--corpus", CORPUS])[1])
        acc = json.loads(run(["count", "--terms", TERMS, "--corpus", CORPUS, "--accent-insensitive"])[1])
        self.assertEqual(base["groups"]["chat"]["terms"]["GIA fatto"]["count"], 0)
        self.assertEqual(acc["groups"]["chat"]["terms"]["GIA fatto"]["count"], 3)

    def test_markdown(self):
        _, out = run(["count", "--terms", TERMS, "--corpus", CORPUS, "--format", "markdown"])
        self.assertIn("| term |", out)

    def test_top(self):
        _, out = run(["top", "--corpus", CORPUS, "--min-count", "3"])
        chat = dict((k, v) for k, v in json.loads(out)["chat"])
        self.assertEqual(chat["ok"], 7)
        self.assertNotIn("delve", chat)
        _, out2 = run(["top", "--corpus", CORPUS, "--ngram", "2", "--min-count", "3"])
        self.assertIn("ok ok", dict((k, v) for k, v in json.loads(out2)["chat"]))

    def test_check(self):
        code, out = run(["check", "--terms", TERMS, DRAFT])
        self.assertEqual(code, 1)
        self.assertIn("delve into\t1\tline 2", out)
        code, out = run(["check", "--terms", TERMS, str(FIX / "lexicon_terms.txt")])
        self.assertEqual(code, 1)

    def test_check_clean(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.txt"
            p.write_text("nothing to see\n", encoding="utf-8")
            code, out = run(["check", "--terms", TERMS, str(p)])
        self.assertEqual(code, 0)
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
