#!/usr/bin/env python3
"""Import call transcripts (Kaiku folders or generic .md/.txt files) into own-voice JSONL."""
import argparse
import collections
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import redact  # noqa: E402

SOURCE = "transcript"

_TIME = r"\d{1,2}:\d{2}(?::\d{2})?"
_KAIKU = re.compile(r"^\*\*\[(" + _TIME + r")\]\s*([^:*]{1,60}?):\*\*\s*(.*)$")
_KAIKU_NOTIME = re.compile(r"^\*\*([^:*\[\]]{1,60}?):\*\*\s*(.*)$")
_BRACKET = re.compile(r"^\[(" + _TIME + r")\]\s*([^:]{1,60}?):\s*(.*)$")
_PLAIN_TIME = re.compile(r"^(" + _TIME + r")\s+([^:]{1,60}?):\s*(.*)$")
_PLAIN = re.compile(r"^([^:\[\]*#\d][^:\[\]*#]{0,39}?):\s+(.*)$")
_FOLDER_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[_T -]?(\d{2})(\d{2}))?")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n")


def data_dir():
    return Path(os.environ.get("OWN_VOICE_DIR", Path.home() / ".own-voice")).expanduser()


def parse_line(line):
    line = line.strip()
    m = _KAIKU.match(line)
    if m:
        return m.group(2).strip(), m.group(3)
    m = _KAIKU_NOTIME.match(line)
    if m:
        return m.group(1).strip(), m.group(2)
    for rx in (_BRACKET, _PLAIN_TIME):
        m = rx.match(line)
        if m:
            return m.group(2).strip(), m.group(3)
    m = _PLAIN.match(line)
    if m and len(m.group(1).split()) <= 4:
        return m.group(1).strip(), m.group(2)
    return None


def _norm(s):
    return re.sub(r"\W+", " ", s.casefold()).strip()


def collapse_loops(text):
    parts = [p for p in _SENTENCE_SPLIT.split(text) if p.strip()]
    out, i = [], 0
    while i < len(parts):
        j = i
        while j + 1 < len(parts) and _norm(parts[j + 1]) == _norm(parts[i]):
            j += 1
        out.append(parts[i].strip())
        if j - i + 1 < 3:
            out.extend(p.strip() for p in parts[i + 1:j + 1])
        i = j + 1
    return " ".join(out)


def parse_transcript(content):
    """Return a list of (speaker, text) turns; consecutive same-speaker lines are merged."""
    turns = []
    for raw in content.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        parsed = parse_line(raw)
        if parsed:
            speaker, text = parsed
            if turns and turns[-1][0] == speaker:
                turns[-1][1].append(text)
            else:
                turns.append((speaker, [text]))
        elif turns:
            turns[-1][1].append(raw.strip())
    result = []
    for speaker, lines in turns:
        text = collapse_loops("\n".join(l for l in lines if l.strip()))
        if text:
            result.append((speaker, text))
    return result


def folder_date(name):
    m = _FOLDER_DATE.match(name)
    if not m:
        return None
    if m.group(2):
        return "%sT%s:%s:00" % (m.group(1), m.group(2), m.group(3))
    return m.group(1)


def normalize_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat()
    except ValueError:
        return str(value)[:10] if re.match(r"\d{4}-\d{2}-\d{2}", str(value)) else None


def load_call(path):
    p = Path(path)
    meta = {}
    if p.is_dir():
        tfile = p / "transcript.md"
        mfile = p / "meta.json"
        if mfile.is_file():
            try:
                loaded = json.loads(mfile.read_text(encoding="utf-8"))
                meta = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError):
                meta = {}
        name = p.name
    else:
        tfile, name = p, p.stem
    content = tfile.read_text(encoding="utf-8", errors="replace")
    return {
        "name": name, "meta": meta, "turns": parse_transcript(content),
        "date": normalize_date(meta.get("date")) or folder_date(name),
        "thread": str(meta.get("title") or name),
    }


def find_calls(inputs):
    found = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            if (p / "transcript.md").is_file():
                found.append(p)
            else:
                found.extend(sorted(t.parent for t in p.rglob("transcript.md")))
        elif p.is_file():
            found.append(p)
        else:
            raise FileNotFoundError(item)
    return found


def excluded(call, names):
    src = str(call["meta"].get("source") or "").casefold()
    folder = call["name"].casefold()
    return any(n == src or n in folder for n in names)


def in_range(iso, since, before):
    if since is None and before is None:
        return True
    if not iso:
        return False
    d = date.fromisoformat(iso[:10])
    return not ((since and d < since) or (before and d >= before))


def build_records(call, me, keep_others, since, before):
    if not in_range(call["date"], since, before):
        return []
    speakers = []
    for s, _ in call["turns"]:
        if s not in speakers:
            speakers.append(s)
    out = []
    for speaker, text in call["turns"]:
        is_me = speaker.casefold() in me
        if not is_me and not keep_others:
            continue
        if is_me:
            others = [s for s in speakers if s.casefold() not in me]
        else:
            others = [s for s in speakers if s.casefold() in me]
        out.append({
            "source": SOURCE, "channel": "spoken", "date": call["date"],
            "thread": call["thread"], "recipient": ", ".join(others) if others else None,
            "author": speaker, "is_me": is_me, "text": redact.redact(text),
        })
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("inputs", nargs="+", help="call folders, parent folders or transcript files")
    ap.add_argument("--me", action="append", default=[], help="your speaker label (repeatable)")
    ap.add_argument("--exclude-source", action="append", default=[])
    ap.add_argument("--language", help="keep only calls whose meta.json language matches")
    ap.add_argument("--keep-others", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--append", action="store_true")
    ap.add_argument("--since", help="ISO date, inclusive")
    ap.add_argument("--before", help="ISO date, exclusive")
    args = ap.parse_args(argv)
    try:
        since = date.fromisoformat(args.since) if args.since else None
        before = date.fromisoformat(args.before) if args.before else None
    except ValueError:
        ap.error("--since/--before must be ISO dates (YYYY-MM-DD)")
    try:
        paths = find_calls(args.inputs)
    except FileNotFoundError as exc:
        print("not found: %s" % exc, file=sys.stderr)
        return 1

    excl = [n.casefold() for n in args.exclude_source]
    calls, skipped_calls = [], 0
    for path in paths:
        try:
            call = load_call(path)
        except OSError:
            skipped_calls += 1
            continue
        lang = str(call["meta"].get("language") or "").casefold()
        if excluded(call, excl) or (
            args.language and not (lang == args.language.casefold()
                                   or lang.startswith(args.language.casefold() + "-"))
        ):
            skipped_calls += 1
            continue
        calls.append(call)

    if not args.me:
        words = collections.Counter()
        for call in calls:
            for speaker, text in call["turns"]:
                words[speaker] += len(text.split())
        for speaker, n in words.most_common():
            print("%s: %d words" % (speaker, n))
        print("Pass your speaker label(s) with --me LABEL to import.", file=sys.stderr)
        return 2

    me = {m.casefold() for m in args.me}
    records = []
    for call in calls:
        records.extend(build_records(call, me, args.keep_others, since, before))
    out = Path(args.out) if args.out else data_dir() / "raw" / (SOURCE + ".jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "a" if args.append else "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    ds = sorted(r["date"][:10] for r in records if r["date"])
    print("written: %d, skipped calls: %d, range: %s" % (
        len(records), skipped_calls, "%s to %s" % (ds[0], ds[-1]) if ds else "n/a"))
    print("output: %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
