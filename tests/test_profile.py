import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import profile as prof  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
CORPUS = str(FIX / "profile_corpus.jsonl")
CONFIG = str(FIX / "profile_config.json")


def run(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = prof.main(argv)
    return code, buf.getvalue()


class ProfileTests(unittest.TestCase):
    def test_load_skips_malformed(self):
        self.assertEqual(len(prof.load_corpus([CORPUS])), 12)

    def test_laughter(self):
        for w in ["ahah", "haha", "jajaja", "hihi", "kkkk", "lol", "xd"]:
            self.assertTrue(prof.is_laughter(w), w)
        for w in ["casa", "ciao", "ok", "hello"]:
            self.assertFalse(prof.is_laughter(w), w)

    def test_tokenize_apostrophe_accent(self):
        self.assertEqual(prof.tokenize("L'amico è già qui"), ["l'amico", "è", "già", "qui"])

    def test_measure_by_register(self):
        code, out = run(["measure", CORPUS, "--config", CONFIG, "--examples", "2"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(set(data), {"friends", "work"})
        f = data["friends"]
        self.assertEqual(f["messages"], 6)
        self.assertEqual(f["unit"], "words")
        self.assertGreater(f["laughter_per_1000"], 0)
        self.assertEqual(f["top_emoji"][0][0], "\U0001F602")
        self.assertEqual(len(f["examples"]), 2)
        self.assertGreater(f["pct_messages_no_final_punctuation"], 30)
        self.assertGreater(data["work"]["dashes_em_en"], 0)

    def test_cjk_unit(self):
        recs = [r for r in prof.load_corpus([CORPUS]) if r["thread"] == "jp"]
        self.assertEqual(prof.measure_group(recs)["unit"], "chars")

    def test_group_none_and_markdown(self):
        code, out = run(["measure", CORPUS, "--group-by", "none", "--format", "markdown"])
        self.assertEqual(code, 0)
        self.assertIn("# all", out)

    def test_stopwords(self):
        with tempfile.TemporaryDirectory() as d:
            sw = Path(d) / "sw.txt"
            sw.write_text("ok\nti\n")
            _, out = run(["measure", CORPUS, "--group-by", "none", "--stopwords", str(sw)])
            words = [w for w, _ in json.loads(out)["all"]["top_words"]]
            self.assertNotIn("ok", words)

    def test_polished_ranks_formal_email_first(self):
        code, out = run(["polished", CORPUS, "--threshold", "0.1"])
        self.assertEqual(code, 0)
        rows = out.strip().splitlines()[1:]
        self.assertTrue(rows)
        self.assertIn("boss", rows[0])

    def test_filter(self):
        with tempfile.TemporaryDirectory() as d:
            out = str(Path(d) / "c.jsonl")
            run(["filter", CORPUS, "--drop-indexes", "0,1", "--out", out])
            self.assertEqual(len(prof.load_corpus([out])), 10)
            run(["filter", CORPUS, "--before", "2023-01-01", "--channels", "email", "--out", out])
            recs = prof.load_corpus([out])
            self.assertEqual(len(recs), 10)
            self.assertTrue(all(r["date"][:4] <= "2022" for r in recs if r["channel"] == "email"))

    def test_threads(self):
        code, out = run(["threads", CORPUS])
        data = json.loads(out)
        self.assertEqual(data[0]["thread"], "boss")
        self.assertEqual(data[0]["channel"], "email")
        self.assertEqual(data[0]["date_range"][0], "2021-03-02")


if __name__ == "__main__":
    unittest.main()
