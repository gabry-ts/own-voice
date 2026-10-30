import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import import_applemail as am  # noqa: E402

RAW = (
    "@@OWV_MSG@@2024-03-12T10:00:00@@OWV_F@@sam.lee@example.org, jo@example.org, "
    "@@OWV_F@@Re: Kickoff@@OWV_F@@Alex Rivera <alex.rivera@example.com>@@OWV_F@@"
    "Hi Sam,\rThursday works. Call +44 7700 900123.\r\rOn Mon, 11 Mar 2024, Sam wrote:\r> old\r"
    "@@OWV_END@@"
    "@@OWV_MSG@@2024-03-13T09:30:00@@OWV_F@@@@OWV_F@@@@OWV_F@@Alex@@OWV_F@@@@OWV_END@@"
    "@@OWV_MSG@@broken@@OWV_END@@"
    "@@OWV_MSG@@2024-03-14T08:00:00@@OWV_F@@giulia@example.org, @@OWV_F@@Budget@@OWV_F@@Alex"
    "@@OWV_F@@Line with @@OWV_F@@ inside\n@@OWV_END@@"
)


class ParserTests(unittest.TestCase):
    def test_parse_output(self):
        items = am.parse_osascript_output(RAW)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["date"], "2024-03-12T10:00:00")
        self.assertEqual(items[0]["recipients"], ["sam.lee@example.org", "jo@example.org"])
        self.assertEqual(items[0]["subject"], "Re: Kickoff")
        self.assertIn("Thursday works", items[0]["content"])
        self.assertEqual(items[2]["content"], "Line with @@OWV_F@@ inside")

    def test_to_record(self):
        items = am.parse_osascript_output(RAW)
        seen = set()
        rec = am.to_record(items[0], None, None, seen)
        self.assertEqual(rec["thread"], "Kickoff")
        self.assertEqual(rec["text"], "Hi Sam,\nThursday works. Call [REDACTED:phone].")
        self.assertEqual(rec["source"], "applemail")
        self.assertTrue(rec["is_me"])
        self.assertIsNone(am.to_record(items[0], None, None, seen))
        self.assertIsNone(am.to_record(items[1], None, None, set()))

    def test_date_filter(self):
        items = am.parse_osascript_output(RAW)
        self.assertIsNone(am.to_record(items[0], date(2024, 3, 13), None, set()))
        self.assertIsNotNone(am.to_record(items[0], None, date(2024, 3, 13), set()))

    def test_mailbox_list(self):
        raw = "1\t1\tINBOX\n1\t2\tSent Messages\ngarbage\n2\t1\tPosta inviata\n"
        self.assertEqual(am.parse_mailbox_list(raw),
                         [(1, 1, "INBOX"), (1, 2, "Sent Messages"), (2, 1, "Posta inviata")])

    def test_parse_dates_and_select(self):
        raw = "2024-03-12T10:00:00\n2024-07-01T09:00:00\n\n2023-12-31T23:59:59\n"
        dates = am.parse_dates_output(raw)
        self.assertEqual(len(dates), 4)
        self.assertIsNone(dates[2])
        self.assertEqual(am.select_indices(dates), [1, 2, 3, 4])
        self.assertEqual(am.select_indices(dates, None, date(2024, 6, 1)), [1, 4])
        self.assertEqual(am.select_indices(dates, date(2024, 1, 1), None), [1, 2])
        self.assertEqual(am.select_indices(dates, date(2024, 3, 12), date(2024, 3, 13)), [1])

    def test_scripts_are_static(self):
        self.assertIn("with timeout of 600 seconds", am.DATES_SCRIPT)
        self.assertIn("with timeout of 600 seconds", am.FETCH_SCRIPT)
        self.assertNotIn("whose", am.DATES_SCRIPT + am.FETCH_SCRIPT)


if __name__ == "__main__":
    unittest.main()
