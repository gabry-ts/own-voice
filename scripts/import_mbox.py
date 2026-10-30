#!/usr/bin/env python3
"""Import sent email from .mbox/.eml files into own-voice JSONL records."""
import argparse
import email
import email.policy
import json
import mailbox
import os
import re
import sys
from collections import Counter
from datetime import date, datetime
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import redact  # noqa: E402

SOURCE = "mbox"

_VERBS = r"wrote|ha scritto|schrieb|a écrit|escribió|escreveu|skrev|schreef"
_REPLY_INTRO = re.compile(
    r"^\s*(?:On|Il giorno|Il|Am|Le|El|Em|Den|Op)\b.{0,250}?\b(?:" + _VERBS + r")\b.{0,120}?:?\s*$",
    re.IGNORECASE,
)
_SEPARATOR = re.compile(
    r"^[\s\-_*=]*(?:original message|messaggio originale|forwarded message|messaggio inoltrato|"
    r"ursprüngliche nachricht|weitergeleitete nachricht|mensaje original|mensaje reenviado|"
    r"message d'origine|message transféré|mensagem original|mensagem encaminhada|"
    r"begin forwarded message)[\s\-_*=:]*$",
    re.IGNORECASE,
)
_OUTLOOK_FROM = re.compile(r"^\s*\**(?:From|Da|De|Von)\s*:", re.IGNORECASE)
_OUTLOOK_SENT = re.compile(r"^\s*\**(?:Sent|Inviato|Enviado|Gesendet|Date|Data|Envoyé)\s*:", re.IGNORECASE)
_MOBILE_SIG = re.compile(
    r"^\s*(?:sent from my |sent from outlook|sent from mail for|get outlook for|"
    r"inviato da (?:iphone|ipad|il mio|outlook|yahoo|samsung|android|mail)|"
    r"inviato dal mio|gesendet von (?:meinem )?(?:iphone|ipad|samsung|android|outlook)|"
    r"enviado desde mi |enviado de meu |enviado do meu |envoyé de mon |envoyé depuis mon |"
    r"von meinem (?:iphone|ipad))",
    re.IGNORECASE,
)
_SUBJECT_PREFIX = re.compile(r"^\s*(?:(?:re|fwd?|r|i|aw|wg|tr|rif|sv|vs|enc|rv)\s*:\s*)+", re.IGNORECASE)


def data_dir():
    return Path(os.environ.get("OWN_VOICE_DIR", Path.home() / ".own-voice")).expanduser()


def clean_body(text):
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept = []
    for i, line in enumerate(lines):
        if line.rstrip() == "--" and line.startswith("--"):
            break
        if _SEPARATOR.match(line) or _MOBILE_SIG.match(line):
            break
        if _REPLY_INTRO.match(line) or (
            i + 1 < len(lines) and _REPLY_INTRO.match(line + " " + lines[i + 1])
            and not line.strip().endswith(".")
        ):
            break
        if _OUTLOOK_FROM.match(line) and any(
            _OUTLOOK_SENT.match(l) for l in lines[i + 1:i + 5]
        ):
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line)
    out = "\n".join(kept)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


class _TextExtractor(HTMLParser):
    BLOCK = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0
        self.quote = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.skip += 1
        elif tag == "blockquote":
            self.quote += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self.skip = max(0, self.skip - 1)
        elif tag == "blockquote":
            self.quote = max(0, self.quote - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip and not self.quote:
            self.parts.append(data)


def html_to_text(html):
    p = _TextExtractor()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    text = "".join(p.parts)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _decode_part(part):
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def extract_body(msg):
    plain, html = None, None
    for part in msg.walk():
        if part.is_multipart():
            continue
        if (part.get_content_disposition() or "") == "attachment":
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain" and plain is None:
            plain = _decode_part(part)
        elif ctype == "text/html" and html is None:
            html = _decode_part(part)
    if plain is not None and plain.strip():
        return plain
    if html is not None:
        return html_to_text(html)
    return ""


def _header(msg, name):
    v = msg.get(name)
    if v is None:
        return ""
    try:
        return str(make_header(decode_header(str(v))))
    except Exception:
        return str(v)


def addresses(msg, *names):
    vals = []
    for n in names:
        vals.extend(str(x) for x in msg.get_all(n, []))
    return [a.lower() for _, a in getaddresses(vals) if a]


def clean_subject(subject):
    return re.sub(r"\s+", " ", _SUBJECT_PREFIX.sub("", subject or "")).strip()


def parse_date(msg):
    try:
        return parsedate_to_datetime(str(msg.get("Date")))
    except (TypeError, ValueError):
        return None


def parse_day(value):
    return date.fromisoformat(value) if value else None


def in_range(dt, since, before):
    if since is None and before is None:
        return True
    if dt is None:
        return False
    if since and dt.date() < since:
        return False
    if before and dt.date() >= before:
        return False
    return True


def iter_messages(path):
    p = Path(path)
    if p.is_dir():
        for f in sorted(p.rglob("*")):
            if f.is_file() and f.suffix.lower() in (".mbox", ".eml"):
                yield from iter_messages(f)
    elif p.suffix.lower() == ".mbox":
        box = mailbox.mbox(str(p), create=False)
        try:
            for m in box:
                yield email.message_from_bytes(m.as_bytes(), policy=email.policy.compat32)
        finally:
            box.close()
    else:
        with open(p, "rb") as fh:
            yield email.message_from_binary_file(fh, policy=email.policy.compat32)


def build_record(msg, me, keep_others, since, before, seen):
    """Return (record or None, reason) where reason explains a skip."""
    sender = addresses(msg, "From")
    is_me = any(a in me for a in sender)
    if not is_me and not keep_others:
        return None, "other"
    dt = parse_date(msg)
    if not in_range(dt, since, before):
        return None, "range"
    text = clean_body(extract_body(msg))
    if not text:
        return None, "empty"
    iso = dt.isoformat() if dt else None
    key = (iso, text)
    if key in seen:
        return None, "duplicate"
    seen.add(key)
    to = addresses(msg, "To")
    return {
        "source": SOURCE, "channel": "email", "date": iso,
        "thread": clean_subject(_header(msg, "Subject")) or "(no subject)",
        "recipient": ", ".join(to) if to else None,
        "author": sender[0] if sender else "",
        "is_me": is_me, "text": redact.redact(text),
    }, ""


def write_records(records, out_path, append):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "a" if append else "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def date_range(records):
    ds = sorted(r["date"][:10] for r in records if r["date"])
    return (ds[0], ds[-1]) if ds else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("inputs", nargs="+", help=".mbox/.eml files or directories")
    ap.add_argument("--me", action="append", default=[], help="your address (repeatable)")
    ap.add_argument("--keep-others", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--append", action="store_true")
    ap.add_argument("--since", help="ISO date, inclusive")
    ap.add_argument("--before", help="ISO date, exclusive")
    args = ap.parse_args(argv)
    try:
        since, before = parse_day(args.since), parse_day(args.before)
    except ValueError:
        ap.error("--since/--before must be ISO dates (YYYY-MM-DD)")
    for path in args.inputs:
        if not Path(path).exists():
            print("not found: %s" % path, file=sys.stderr)
            return 1
    me = {a.strip().lower() for a in args.me}
    records, skipped, seen = [], 0, set()
    counts, matched = Counter(), False
    for path in args.inputs:
        for msg in iter_messages(path):
            sender = addresses(msg, "From")
            counts.update(sender[:1])
            if not args.me:
                continue
            matched = matched or any(a in me for a in sender)
            rec, _ = build_record(msg, me, args.keep_others, since, before, seen)
            if rec:
                records.append(rec)
            else:
                skipped += 1
    if not args.me or not matched:
        for addr, n in counts.most_common():
            print("%s: %d" % (addr, n))
        if args.me:
            print("No messages match --me; existing output left untouched.", file=sys.stderr)
        else:
            print("Pass your address(es) with --me ADDRESS to import.", file=sys.stderr)
        return 2
    out = args.out or data_dir() / "raw" / (SOURCE + ".jsonl")
    if records:
        write_records(records, out, args.append)
    rng = date_range(records)
    print("written: %d, skipped: %d, range: %s" % (
        len(records), skipped, "%s to %s" % rng if rng else "n/a"))
    print("output: %s%s" % (out, "" if records else " (no records, left untouched)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
