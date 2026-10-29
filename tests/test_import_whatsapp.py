import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import import_whatsapp as wa  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


def load(name):
    return (FIX / name).read_text(encoding="utf-8")


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = wa.main(args)
    return rc, out.getvalue()


def read_jsonl(path):
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines()]


class ParseTests(unittest.TestCase):
    def test_ios_en_mdy_inferred_and_filtered(self):
        msgs, order = wa.parse_chat(load("wa_ios_en.txt"))
        self.assertEqual(order, "mdy")
        texts = [m["text"] for m in msgs if m["author"] == "Alex Rivera"]
        self.assertEqual(texts[0], "Hey Sam, are we still on for lunch?")
        self.assertIn("Great. Two things:\nbring the slides\nand the laptop charger", texts)
        self.assertIn("see you at 12.30", texts)
        self.assertEqual(len(msgs), 5)
        self.assertEqual(msgs[0]["dt"], datetime(2024, 3, 12, 9, 14, 40))
        self.assertEqual(msgs[-2]["dt"], datetime(2024, 3, 12, 21, 19, 0))

    def test_android_en(self):
        msgs, _ = wa.parse_chat(load("wa_android_en.txt"))
        self.assertEqual([m["author"] for m in msgs], ["Alex Rivera", "Alex Rivera"])
        self.assertTrue(msgs[1]["text"].endswith("and decide then."))

    def test_italian_ios_and_android(self):
        msgs, order = wa.parse_chat(load("wa_ios_it.txt"))
        self.assertEqual(order, "dmy")
        self.assertEqual([m["text"] for m in msgs],
                         ["Ciao Giulia, tutto bene?", "Sì, tutto ok. Ci vediamo domani?",
                          "Perfetto, a domani!"])
        msgs, _ = wa.parse_chat(load("wa_android_it.txt"))
        self.assertEqual(len(msgs), 2)

    def test_german(self):
        msgs, _ = wa.parse_chat(load("wa_ios_de.txt"))
        self.assertEqual([m["text"] for m in msgs],
                         ["Hallo Jonas, wie geht's?", "Gut, danke!", "Bis morgen"])
        self.assertEqual(msgs[0]["dt"], datetime(2024, 3, 12, 9, 14, 40))
        msgs, _ = wa.parse_chat(load("wa_android_de.txt"))
        self.assertEqual(msgs[0]["dt"], datetime(2024, 3, 12, 9, 14))
        self.assertEqual(len(msgs), 2)

    def test_ambiguous_defaults_dmy_and_override(self):
        content = "03/04/24, 10:00 - A: x\n03/04/24, 10:01 - B: y\n"
        m1, _ = wa.parse_chat(content)
        self.assertEqual(m1[0]["dt"].month, 4)
        m2, _ = wa.parse_chat(content, "mdy")
        self.assertEqual(m2[0]["dt"].month, 3)

    def test_thread_names(self):
        cases = {
            "WhatsApp Chat - Sam Lee.txt": "Sam Lee",
            "WhatsApp Chat with Sam Lee.zip": "Sam Lee",
            "Chat WhatsApp con Giulia Neri.zip": "Giulia Neri",
            "WhatsApp-Chat mit Jonas Weber.zip": "Jonas Weber",
            "Chat de WhatsApp con Ana.zip": "Ana",
            "Discussion WhatsApp avec Luc.zip": "Luc",
            "Conversa do WhatsApp com Rita.zip": "Rita",
            "random.txt": "random",
        }
        for name, want in cases.items():
            self.assertEqual(wa.thread_from_name(name), want, name)

    def test_placeholders(self):
        for t in ["<Media omitted>", "image omitted", "GIF omitted", "sticker omitted",
                  "immagine omessa", "<Media omessi>", "This message was deleted",
                  "Hai eliminato questo messaggio", "Missed voice call", "<attached: x.jpg>",
                  "IMG-1.jpg (file attached)"]:
            self.assertTrue(wa.is_placeholder(t), t)
        self.assertFalse(wa.is_placeholder("I omitted the details"))


class RecordTests(unittest.TestCase):
    def test_one_to_one_and_group_recipient(self):
        msgs, _ = wa.parse_chat(load("wa_ios_en.txt"))
        recs, skipped = wa.build_records(msgs, "Sam Lee", ["Alex Rivera"])
        self.assertTrue(all(r["is_me"] and r["recipient"] == "Sam Lee" for r in recs))
        self.assertGreater(skipped, 0)
        gmsgs, _ = wa.parse_chat(load("wa_group_en.txt"))
        grecs, _ = wa.build_records(gmsgs, "Hiking Crew", ["alex rivera"])
        self.assertEqual({r["recipient"] for r in grecs}, {"Hiking Crew"})
        self.assertEqual(len(grecs), 2)

    def test_redaction_applied(self):
        msgs, _ = wa.parse_chat(load("wa_ios_en.txt"))
        recs, _ = wa.build_records(msgs, "t", ["Sam Lee"])
        joined = " ".join(r["text"] for r in recs)
        self.assertIn("[REDACTED:phone]", joined)
        self.assertNotIn("7700", joined)

    def test_keep_others_and_date_filter(self):
        msgs, _ = wa.parse_chat(load("wa_ios_en.txt"))
        recs, _ = wa.build_records(msgs, "t", ["Alex Rivera"], keep_others=True)
        self.assertIn(False, [r["is_me"] for r in recs])
        recs, _ = wa.build_records(msgs, "t", ["Alex Rivera"], keep_others=True,
                                   since=wa.parse_day("2024-03-13"))
        self.assertEqual(len(recs), 1)
        recs, _ = wa.build_records(msgs, "t", ["Alex Rivera"], before=wa.parse_day("2024-03-12"))
        self.assertEqual(recs, [])


class CliTests(unittest.TestCase):
    def test_no_me_lists_authors_exit_2(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "o.jsonl"
            rc, text = run([str(FIX / "wa_ios_en.txt"), "--out", str(out)])
            self.assertEqual(rc, 2)
            self.assertIn("Alex Rivera: 3", text)
            self.assertFalse(out.exists())

    def test_zip_import_and_append(self):
        with tempfile.TemporaryDirectory() as d:
            zp = Path(d) / "WhatsApp Chat with Sam Lee.zip"
            with zipfile.ZipFile(zp, "w") as zf:
                zf.writestr("_chat.txt", load("wa_ios_en.txt"))
                zf.writestr("IMG-1.jpg", b"\xff\xd8")
            out = Path(d) / "sub" / "wa.jsonl"
            rc, text = run([str(zp), "--me", "Alex Rivera", "--out", str(out)])
            self.assertEqual(rc, 0)
            recs = read_jsonl(out)
            self.assertEqual(len(recs), 3)
            self.assertEqual(recs[0]["thread"], "Sam Lee")
            self.assertEqual(set(recs[0]), {"source", "channel", "date", "thread", "recipient",
                                            "author", "is_me", "text"})
            self.assertNotIn("lunch", text)
            run([str(zp), "--me", "Alex Rivera", "--out", str(out), "--append"])
            self.assertEqual(len(read_jsonl(out)), 6)

    def test_default_out_uses_env(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["OWN_VOICE_DIR"] = d
            try:
                rc, _ = run([str(FIX / "wa_group_en.txt"), "--me", "Alex Rivera"])
            finally:
                del os.environ["OWN_VOICE_DIR"]
            self.assertEqual(rc, 0)
            self.assertEqual(len(read_jsonl(Path(d) / "raw" / "whatsapp.jsonl")), 2)


if __name__ == "__main__":
    unittest.main()
