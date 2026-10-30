#!/usr/bin/env python3
"""Import plain text or markdown files you wrote into own-voice JSONL records."""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import redact  # noqa: E402

SOURCE = "text"
CHANNELS = ["chat", "email", "post", "text"]


def data_dir():
    return Path(os.environ.get("OWN_VOICE_DIR", Path.home() / ".own-voice")).expanduser()


def collect(inputs):
    files = []
    for item in inputs:
        if item == "-":
            files.append("-")
            continue
        p = Path(item)
        if p.is_dir():
            files.extend(sorted(f for f in p.rglob("*")
                                if f.is_file() and f.suffix.lower() in (".txt", ".md")))
        elif p.is_file():
            files.append(p)
        else:
            raise FileNotFoundError(item)
    return files


def split_text(text, mode):
    if mode == "paragraphs":
        parts = re.split(r"\n\s*\n", text)
    else:
        parts = [text]
    return [p.strip() for p in parts if p.strip()]


def build_records(text, channel, thread, iso_date, split):
    return [{
        "source": SOURCE, "channel": channel, "date": iso_date, "thread": thread,
        "recipient": None, "author": "me", "is_me": True, "text": redact.redact(part),
    } for part in split_text(text, split)]


def in_range(iso, since, before):
    if since is None and before is None:
        return True
    if not iso:
        return False
    d = date.fromisoformat(iso[:10])
    return not ((since and d < since) or (before and d >= before))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("inputs", nargs="+", help=".txt/.md files, directories, or - for stdin")
    ap.add_argument("--channel", default="text", choices=CHANNELS)
    ap.add_argument("--thread", help="thread name (default: file name)")
    ap.add_argument("--split", choices=["none", "paragraphs"], default="none")
    ap.add_argument("--date", help="ISO date for all records (default: file mtime)")
    ap.add_argument("--out")
    ap.add_argument("--append", action="store_true")
    ap.add_argument("--since", help="ISO date, inclusive")
    ap.add_argument("--before", help="ISO date, exclusive")
    args = ap.parse_args(argv)
    try:
        since = date.fromisoformat(args.since) if args.since else None
        before = date.fromisoformat(args.before) if args.before else None
        if args.date:
            date.fromisoformat(args.date[:10])
    except ValueError:
        ap.error("dates must be ISO (YYYY-MM-DD)")
    try:
        files = collect(args.inputs)
    except FileNotFoundError as exc:
        print("not found: %s" % exc, file=sys.stderr)
        return 1

    records, skipped = [], 0
    for f in files:
        if f == "-":
            text, name, iso = sys.stdin.read(), "stdin", args.date
        else:
            text = f.read_text(encoding="utf-8", errors="replace")
            name = f.stem
            iso = args.date or datetime.fromtimestamp(f.stat().st_mtime).replace(microsecond=0).isoformat()
        recs = build_records(text, args.channel, args.thread or name, iso, args.split)
        if not in_range(iso, since, before):
            skipped += len(recs) or 1
            continue
        records.extend(recs)
    out = Path(args.out) if args.out else data_dir() / "raw" / (SOURCE + ".jsonl")
    if records:
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "a" if args.append else "w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    ds = sorted(r["date"][:10] for r in records if r["date"])
    print("written: %d, skipped: %d, range: %s" % (
        len(records), skipped, "%s to %s" % (ds[0], ds[-1]) if ds else "n/a"))
    print("output: %s%s" % (out, "" if records else " (no records, left untouched)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
