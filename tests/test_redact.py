import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import redact  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


def kinds(text):
    return {k for k, _ in redact.find_secrets(text)}


class RedactTests(unittest.TestCase):
    def test_email_and_phone(self):
        out = redact.redact("write to alex.rivera@example.com or +39 333 123 4567")
        self.assertIn("[REDACTED:email]", out)
        self.assertIn("[REDACTED:phone]", out)
        self.assertNotIn("example.com", out)

    def test_phone_formats(self):
        self.assertIn("phone", kinds("+1 (415) 555-0132"))
        self.assertIn("phone", kinds("415-555-0132"))

    def test_card_luhn(self):
        self.assertIn("card", kinds("4111 1111 1111 1111"))
        self.assertNotIn("card", kinds("4111 1111 1111 1112"))

    def test_iban(self):
        self.assertIn("iban", kinds("IBAN: IT60 X054 2811 1010 0000 0123 456"))
        self.assertIn("iban", kinds("DE89370400440532013000"))

    def test_password_labels(self):
        self.assertEqual(redact.redact("password: hunter2"), "password: [REDACTED:password]")
        self.assertIn("[REDACTED:password]", redact.redact("passwort ist Zugang99!"))
        self.assertIn("[REDACTED:password]", redact.redact("mot de passe : tresor42"))
        self.assertIn("[REDACTED:password]", redact.redact("пароль: секрет1"))
        self.assertIn("[REDACTED:password]", redact.redact("password hunter22"))

    def test_api_keys(self):
        for s in ["sk-ant-FAKEFAKEFAKEFAKEFAKE1234", "ghp_" + "A" * 24, "AKIA" + "B" * 16,
                  "xoxb-1234567890-abcdef", "glpat-" + "c" * 20, "AIza" + "d" * 35,
                  "api_key=abcd1234efgh5678ijkl"]:
            self.assertIn("api_key", kinds(s), s)

    def test_jwt_and_private_key(self):
        self.assertIn("jwt", kinds("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.SIG"))
        pem = "-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----"
        self.assertEqual(redact.redact("x " + pem + " y"), "x [REDACTED:private_key] y")

    def test_no_false_positives(self):
        text = (FIX / "clean.txt").read_text(encoding="utf-8")
        self.assertEqual(redact.find_secrets(text), [])
        self.assertEqual(redact.redact(text), text)

    def test_fixture_all_kinds(self):
        found = kinds((FIX / "secrets.txt").read_text(encoding="utf-8"))
        for k in ["private_key", "jwt", "api_key", "password", "iban", "card", "email", "phone"]:
            self.assertIn(k, found)

    def test_cli_check(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = redact.main(["--check", str(FIX / "secrets.txt")])
        self.assertEqual(rc, 1)
        self.assertIn(":1: email", buf.getvalue())
        self.assertNotIn("example.com", buf.getvalue())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(redact.main(["--check", str(FIX / "clean.txt")]), 0)

    def test_cli_redact(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            redact.main([str(FIX / "secrets.txt")])
        self.assertNotIn("hunter2secret", buf.getvalue())


class CheckUnreadableTests(unittest.TestCase):
    def test_missing_file_exits_2(self):
        import tempfile
        from contextlib import redirect_stderr
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as d, redirect_stderr(err), redirect_stdout(io.StringIO()):
            missing = str(Path(d) / "nope.txt")
            code = redact.main(["--check", missing])
        self.assertEqual(code, 2)
        self.assertEqual(err.getvalue(), "%s: cannot read\n" % missing)


if __name__ == "__main__":
    unittest.main()
