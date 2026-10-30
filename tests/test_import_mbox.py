import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import import_mbox as im  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
ME = ["alex.rivera@example.com"]


def run(args):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = im.main(args)
    return rc, out.getvalue()


class CleanBodyTests(unittest.TestCase):
    def test_quoted_reply_languages(self):
        for intro in ["On Tue, 12 Mar 2024, Sam wrote:", "Il giorno 12 mar 2024 Sam ha scritto:",
                      "Am 12.03.2024 schrieb Sam <s@x.org>:", "Le 12 mars 2024, Sam a écrit :",
                      "El 12 mar 2024, Sam escribió:", "Em 12 de mar de 2024, Sam escreveu:",
                      "-----Original Message-----", "---- Messaggio originale ----",
                      "---------- Forwarded message ---------", "Messaggio inoltrato"]:
            self.assertEqual(im.clean_body("Hello\n\n%s\n> old\nmore" % intro), "Hello", intro)

    def test_wrapped_reply_intro(self):
        text = "Thanks\nOn Tue, 12 Mar 2024 at 09:00, Sam Lee <sam.lee@example.org>\nwrote:\n> x"
        self.assertEqual(im.clean_body(text), "Thanks")

    def test_outlook_block(self):
        for a, b in [("From", "Sent"), ("Da", "Inviato"), ("De", "Enviado"), ("Von", "Gesendet")]:
            text = "Ok\n\n%s: Sam\n%s: Monday\nTo: me\nbody" % (a, b)
            self.assertEqual(im.clean_body(text), "Ok")

    def test_from_without_sent_is_kept(self):
        self.assertIn("From: the team", im.clean_body("Ok\nFrom: the team\nsee you"))

    def test_signatures(self):
        self.assertEqual(im.clean_body("Hi\n-- \nAlex\nCEO"), "Hi")
        self.assertEqual(im.clean_body("Hi\n\nSent from my iPhone"), "Hi")
        self.assertEqual(im.clean_body("Ciao\n\nInviato da iPhone"), "Ciao")

    def test_quote_lines_removed(self):
        self.assertEqual(im.clean_body("a\n> q\nb"), "a\nb")

    def test_subject(self):
        self.assertEqual(im.clean_subject("Fwd: R: RE: AW: WG: TR: I: Hello"), "Hello")


class FileTests(unittest.TestCase):
    def test_mbox_records(self):
        seen = set()
        recs, reasons = [], []
        for m in im.iter_messages(FIX / "sample.mbox"):
            r, why = im.build_record(m, set(ME), False, None, None, seen)
            recs.append(r)
            reasons.append(why)
        self.assertEqual(reasons, ["", "", "other", "", "duplicate"])
        self.assertEqual(recs[0]["text"], "Hi Sam,\n\nThursday works for me. I'll bring the draft.\n\nBest,\nAlex")
        self.assertEqual(recs[0]["thread"], "Project kickoff")
        self.assertEqual(recs[0]["recipient"], "sam.lee@example.org")
        self.assertEqual(recs[1]["text"], "Ciao Giulia,\nti mando i numeri domani.\n\nAlex")
        self.assertEqual(recs[3]["text"], "Hallo Jonas,\n\nvielen Dank für das Angebot.")
        self.assertEqual(recs[0]["channel"], "email")
        self.assertTrue(recs[0]["date"].startswith("2024-03-12T10:00:00"))

    def test_eml_signature_and_redaction(self):
        (msg,) = list(im.iter_messages(FIX / "sample.eml"))
        rec, _ = im.build_record(msg, set(ME), False, None, None, set())
        self.assertEqual(rec["recipient"], "sam.lee@example.org, jo@example.org")
        self.assertEqual(rec["thread"], "Meeting notes")
        self.assertIn("[REDACTED:phone]", rec["text"])
        self.assertNotIn("Product Lead", rec["text"])

    def test_cli_directory_and_filters(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "m.jsonl"
            rc, text = run([str(FIX), "--me", ME[0], "--out", str(out)])
            self.assertEqual(rc, 0)
            lines = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(lines), 4)
            self.assertNotIn("Thursday", text)
            run([str(FIX), "--me", ME[0], "--out", str(out), "--since", "2024-03-14"])
            lines = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(lines), 2)
            run([str(FIX / "sample.mbox"), "--me", ME[0], "--out", str(out), "--keep-others"])
            lines = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([l["is_me"] for l in lines].count(False), 1)

    def test_cli_without_me_lists_senders(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "m.jsonl"
            rc, text = run([str(FIX), "--out", str(out)])
            self.assertEqual(rc, 2)
            self.assertFalse(out.exists())
            lines = text.strip().splitlines()
            self.assertEqual(lines[0], "alex.rivera@example.com: 5")
            self.assertEqual(lines[1], "sam.lee@example.org: 1")


if __name__ == "__main__":
    unittest.main()
