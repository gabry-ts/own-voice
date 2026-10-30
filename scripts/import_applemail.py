#!/usr/bin/env python3
"""Import sent mail from Apple Mail (macOS only) into own-voice JSONL records."""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import redact  # noqa: E402
import import_mbox  # noqa: E402

SOURCE = "applemail"
SENT_NAMES = [
    "Sent", "Sent Messages", "Sent Items", "Sent Mail", "Inviata", "Posta inviata",
    "Messaggi inviati", "Gesendet", "Gesendete Objekte", "Gesendete Elemente", "Enviados",
    "Elementos enviados", "Mensajes enviados", "Envoyés", "Éléments envoyés", "Messages envoyés",
    "Enviadas", "Itens Enviados",
]

MSG, FIELD, END = "@@OWV_MSG@@", "@@OWV_F@@", "@@OWV_END@@"

LIST_SCRIPT = r'''
on run argv
  set out to ""
  with timeout of 600 seconds
    tell application "Mail"
      repeat with i from 1 to count of accounts
        try
          set names to name of mailboxes of account i
          repeat with j from 1 to count of names
            set out to out & i & tab & j & tab & (item j of names) & linefeed
          end repeat
        end try
      end repeat
    end tell
  end timeout
  return out
end run
'''

_ISO_HANDLERS = r"""
on pad(n)
  set s to (n as integer) as text
  if (count of s) < 2 then set s to "0" & s
  return s
end pad
on iso(d)
  return (year of d as text) & "-" & pad(month of d as integer) & "-" & pad(day of d) & "T" & pad(hours of d) & ":" & pad(minutes of d) & ":" & pad(seconds of d)
end iso
"""

DATES_SCRIPT = _ISO_HANDLERS + r"""
on run argv
  set out to ""
  with timeout of 600 seconds
    tell application "Mail"
      set ds to date sent of messages of (item (item 2 of argv as integer) of (mailboxes of account (item 1 of argv as integer)))
    end tell
  end timeout
  repeat with d in ds
    try
      set out to out & my iso(contents of d) & linefeed
    on error
      set out to out & linefeed
    end try
  end repeat
  return out
end run
"""

FETCH_SCRIPT = _ISO_HANDLERS + r"""
on run argv
  set a to item 1 of argv as integer
  set b to item 2 of argv as integer
  set out to ""
  with timeout of 600 seconds
    tell application "Mail"
      set mb to item b of (mailboxes of account a)
      repeat with idx in (items 3 thru -1 of argv)
        try
          set m to message (idx as integer) of mb
          set rcpts to ""
          repeat with r in (to recipients of m)
            set rcpts to rcpts & (address of r) & ", "
          end repeat
          set out to out & "@@OWV_MSG@@" & my iso(date sent of m) & "@@OWV_F@@" & rcpts & "@@OWV_F@@" & (subject of m) & "@@OWV_F@@" & (sender of m) & "@@OWV_F@@" & (content of m) & "@@OWV_END@@"
        end try
      end repeat
    end tell
  end timeout
  return out
end run
"""


def parse_dates_output(raw):
    """Return one datetime (or None) per message, in mailbox order."""
    out = []
    for line in raw.split("\n")[:-1] if raw.endswith("\n") else raw.split("\n"):
        try:
            out.append(datetime.fromisoformat(line.strip()))
        except ValueError:
            out.append(None)
    return out


def select_indices(dates, since=None, before=None):
    """1-based message indices whose date passes the filters (unknown dates only without filters)."""
    picked = []
    for i, dt in enumerate(dates, 1):
        if not since and not before:
            picked.append(i)
        elif dt is not None and import_mbox.in_range(dt, since, before):
            picked.append(i)
    return picked


def parse_osascript_output(raw):
    out = []
    for chunk in re.findall(re.escape(MSG) + r"(.*?)" + re.escape(END), raw, re.DOTALL):
        parts = chunk.split(FIELD, 4)
        if len(parts) != 5:
            continue
        date_s, to, subject, sender, content = (p.strip() for p in parts)
        recipients = [a.strip() for a in to.split(",") if a.strip()]
        out.append({"date": date_s or None, "recipients": recipients, "subject": subject,
                    "sender": sender, "content": parts[4].strip("\n")})
    return out


def parse_mailbox_list(raw):
    boxes = []
    for line in raw.splitlines():
        p = line.split("\t", 2)
        if len(p) == 3 and p[0].isdigit() and p[1].isdigit():
            boxes.append((int(p[0]), int(p[1]), p[2].strip()))
    return boxes


def to_record(item, since, before, seen):
    dt = None
    if item["date"]:
        try:
            dt = datetime.fromisoformat(item["date"])
        except ValueError:
            dt = None
    if not import_mbox.in_range(dt, since, before):
        return None
    text = import_mbox.clean_body(item["content"])
    if not text:
        return None
    iso = dt.isoformat() if dt else None
    if (iso, text) in seen:
        return None
    seen.add((iso, text))
    return {
        "source": SOURCE, "channel": "email", "date": iso,
        "thread": import_mbox.clean_subject(item["subject"]) or "(no subject)",
        "recipient": ", ".join(item["recipients"]) if item["recipients"] else None,
        "author": item["sender"], "is_me": True, "text": redact.redact(text),
    }


def osa(script, *args):
    res = subprocess.run(["osascript", "-e", script] + [str(a) for a in args],
                         capture_output=True, text=True, timeout=700)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or "osascript failed")
    return res.stdout


def finalize_output(partial, out, written, append):
    """Move the partial file into place; keep the existing output when nothing was written."""
    partial, out = Path(partial), Path(out)
    if written <= 0:
        partial.unlink(missing_ok=True)
        return False
    if append and out.exists():
        with open(partial, "rb") as src, open(out, "ab") as dst:
            shutil.copyfileobj(src, dst)
        partial.unlink()
    else:
        os.replace(partial, out)
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mailbox", action="append", default=[], help="extra sent mailbox name")
    ap.add_argument("--batch", type=int, default=100)
    ap.add_argument("--limit", type=int, help="maximum number of records")
    ap.add_argument("--out")
    ap.add_argument("--append", action="store_true")
    ap.add_argument("--since", help="ISO date, inclusive")
    ap.add_argument("--before", help="ISO date, exclusive")
    args = ap.parse_args(argv)
    if sys.platform != "darwin":
        print("import_applemail.py works on macOS only.", file=sys.stderr)
        return 1
    try:
        since, before = import_mbox.parse_day(args.since), import_mbox.parse_day(args.before)
    except ValueError:
        ap.error("--since/--before must be ISO dates (YYYY-MM-DD)")
    wanted = {n.casefold() for n in (args.mailbox or SENT_NAMES)}
    if args.mailbox:
        wanted |= {n.casefold() for n in SENT_NAMES}
    out = Path(args.out) if args.out else import_mbox.data_dir() / "raw" / (SOURCE + ".jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.name + ".partial")
    seen, written, skipped, failed, dates_out = set(), 0, 0, 0, []
    rc = 0
    with open(partial, "w", encoding="utf-8") as fh:
        try:
            boxes = [b for b in parse_mailbox_list(osa(LIST_SCRIPT)) if b[2].casefold() in wanted]
            done = False
            for acc, box, name in boxes:
                print("mailbox %d/%d: reading dates" % (acc, box), file=sys.stderr)
                try:
                    dates = parse_dates_output(osa(DATES_SCRIPT, acc, box))
                except (RuntimeError, subprocess.TimeoutExpired) as exc:
                    print("mailbox %d/%d skipped: %s" % (acc, box, exc), file=sys.stderr)
                    failed += 1
                    continue
                picked = select_indices(dates, since, before)
                for n in range(0, len(picked), args.batch):
                    chunk = picked[n:n + args.batch]
                    print("mailbox %d/%d: %d-%d of %d matching" % (
                        acc, box, n + 1, n + len(chunk), len(picked)), file=sys.stderr)
                    try:
                        raw = osa(FETCH_SCRIPT, acc, box, *chunk)
                    except (RuntimeError, subprocess.TimeoutExpired) as exc:
                        print("batch failed, continuing: %s" % exc, file=sys.stderr)
                        failed += 1
                        continue
                    for item in parse_osascript_output(raw):
                        rec = to_record(item, since, before, seen)
                        if not rec:
                            skipped += 1
                            continue
                        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        written += 1
                        if rec["date"]:
                            dates_out.append(rec["date"][:10])
                        if args.limit and written >= args.limit:
                            done = True
                            break
                    fh.flush()
                    if done:
                        break
                if done:
                    break
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
            print("Apple Mail access failed: %s" % exc, file=sys.stderr)
            rc = 1
        except KeyboardInterrupt:
            print("interrupted, keeping records already written", file=sys.stderr)
            rc = 130
    if rc == 1 and written:
        print("records kept in %s" % partial, file=sys.stderr)
        note = " (failed, records in %s)" % partial
    else:
        finalize_output(partial, out, written, args.append)
        note = "" if written else " (no records, left untouched)"
    dates_out.sort()
    print("written: %d, skipped: %d, failed batches: %d, range: %s" % (
        written, skipped, failed,
        "%s to %s" % (dates_out[0], dates_out[-1]) if dates_out else "n/a"))
    print("output: %s%s" % (out, note))
    return rc


if __name__ == "__main__":
    sys.exit(main())
