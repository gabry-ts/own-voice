import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
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


def run_err(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = prof.main(argv)
    return code, out.getvalue(), err.getvalue()


def write_jsonl(path, texts, date="2024-01-01"):
    rows = [{"source": "t", "channel": "spoken", "date": date, "thread": "call", "text": t} for t in texts]
    Path(path).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def texts_of(path):
    return [r["text"] for r in prof.load_corpus([path])]


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

    def test_filter_drop_threads(self):
        with tempfile.TemporaryDirectory() as d:
            out = str(Path(d) / "c.jsonl")
            _, text = run(["filter", CORPUS, "--drop-threads", " pals ,jp", "--out", out])
            recs = prof.load_corpus([out])
            self.assertFalse({"pals", "jp"} & {r["thread"] for r in recs})
            self.assertIn("dropped by thread: ", text)
            lst = Path(d) / "t.txt"
            lst.write_text("jp\n\n boss \n")
            run(["filter", CORPUS, "--drop-threads", str(lst), "--out", out])
            threads = {r["thread"] for r in prof.load_corpus([out])}
            self.assertFalse({"jp", "boss"} & threads)
            self.assertIn("pals", threads)

    def test_filter_dedup(self):
        with tempfile.TemporaryDirectory() as d:
            out = str(Path(d) / "c.jsonl")
            new = Path(d) / "new.jsonl"
            extra = {"source": "x", "channel": "chat", "date": "2024-01-01", "thread": "t", "text": "fresh"}
            new.write_text(Path(CORPUS).read_text(encoding="utf-8") + json.dumps(extra) + "\n")
            total = len(prof.load_corpus([str(new)]))
            _, text = run(["filter", CORPUS, str(new), "--dedup", "--out", out])
            recs = prof.load_corpus([out])
            self.assertEqual(len(recs), total)
            self.assertEqual(recs[-1]["text"], "fresh")
            self.assertIn("dropped by duplicate: 12", text)
            _, text = run(["filter", str(new), "--dedup-against", CORPUS, "--out", out])
            self.assertEqual([r["text"] for r in prof.load_corpus([out])], ["fresh"])
            self.assertIn("dropped by duplicate: 12", text)

    def test_dedup_keeps_repeats_within_a_file(self):
        with tempfile.TemporaryDirectory() as d:
            a, out = str(Path(d) / "a.jsonl"), str(Path(d) / "o.jsonl")
            write_jsonl(a, ["ok"] * 10 + ["bye"])
            _, text = run(["filter", a, "--dedup", "--out", out])
            self.assertEqual(texts_of(out), ["ok"] * 10 + ["bye"])
            self.assertIn("dropped by duplicate: 0", text)

    def test_dedup_overlapping_files_collapse(self):
        with tempfile.TemporaryDirectory() as d:
            a, b, out = (str(Path(d) / n) for n in ("a.jsonl", "b.jsonl", "o.jsonl"))
            write_jsonl(a, ["ok", "ok", "x"])
            write_jsonl(b, ["ok", "ok", "ok", "y"])
            run(["filter", a, b, "--dedup", "--out", out])
            self.assertEqual(texts_of(out), ["ok", "ok", "x", "ok", "y"])
            run(["filter", a, a, "--dedup", "--out", out])
            self.assertEqual(texts_of(out), ["ok", "ok", "x"])

    def test_dedup_against_sums_files(self):
        with tempfile.TemporaryDirectory() as d:
            a, b, new, out = (str(Path(d) / n) for n in ("a.jsonl", "b.jsonl", "n.jsonl", "o.jsonl"))
            write_jsonl(a, ["ok"])
            write_jsonl(b, ["ok", "z"])
            write_jsonl(new, ["ok", "ok", "ok", "z", "w"])
            run(["filter", new, "--dedup-against", a, b, "--out", out])
            self.assertEqual(texts_of(out), ["ok", "w"])

    def test_dedup_against_same_as_out_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            first, new = str(Path(d) / "first.jsonl"), str(Path(d) / "n.jsonl")
            write_jsonl(first, ["ok", "ok"])
            write_jsonl(new, ["ok", "ok", "more"])
            code, _, err = run_err(["filter", new, "--dedup-against", first, "--out", first])
            self.assertEqual(code, 0)
            self.assertIn("ignoring --dedup-against", err)
            self.assertEqual(texts_of(first), ["ok", "ok", "more"])

    def test_filter_empty_result_keeps_existing_output(self):
        with tempfile.TemporaryDirectory() as d:
            a, out = str(Path(d) / "a.jsonl"), str(Path(d) / "o.jsonl")
            write_jsonl(a, ["ok"])
            write_jsonl(out, ["keep me"])
            code, _, err = run_err(["filter", a, "--dedup-against", a, "--out", out])
            self.assertEqual(code, 1)
            self.assertIn("not overwriting", err)
            self.assertEqual(texts_of(out), ["keep me"])
            Path(out).write_text("")
            code, _, _ = run_err(["filter", a, "--dedup-against", a, "--out", out])
            self.assertEqual(code, 0)

    def test_drop_threads_missing_file_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            out = str(Path(d) / "o.jsonl")
            for value in ("nope/threads.txt", "threads.txt"):
                code, _, err = run_err(["filter", CORPUS, "--drop-threads", value, "--out", out])
                self.assertEqual(code, 2)
                self.assertIn("not found", err)
            self.assertFalse(Path(out).exists())

    def test_measure_by_register_needs_registers(self):
        with tempfile.TemporaryDirectory() as d:
            empty, bad = Path(d) / "e.json", Path(d) / "b.json"
            empty.write_text('{"registers": []}')
            bad.write_text("{nope")
            for cfg in (str(empty), str(bad), str(Path(d) / "missing.json")):
                code, _, err = run_err(["measure", CORPUS, "--config", cfg])
                self.assertEqual(code, 2)
                self.assertIn("config", err)
            code, _, _ = run_err(["measure", CORPUS, "--config", str(empty), "--group-by", "none"])
            self.assertEqual(code, 0)

    def test_threads(self):
        code, out = run(["threads", CORPUS])
        data = json.loads(out)
        self.assertEqual(data[0]["thread"], "boss")
        self.assertEqual(data[0]["channel"], "email")
        self.assertEqual(data[0]["date_range"][0], "2021-03-02")


if __name__ == "__main__":
    unittest.main()
