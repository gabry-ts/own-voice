#!/usr/bin/env python3
"""Import WhatsApp chat exports (.zip or .txt) into own-voice JSONL records."""
import argparse
import collections
import json
import os
import re
import sys
import zipfile
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import redact  # noqa: E402

SOURCE = "whatsapp"

_MARKS = re.compile("[‎‏‪-‮﻿]")
_DATE = r"(\d{1,4}[./-]\d{1,2}[./-]\d{1,4})"
_TIME = r"(\d{1,2}[:.]\d{2}(?::\d{2})?)\s*([AaPp]\.?\s?[Mm]\.?)?"
_IOS = re.compile(r"^\[" + _DATE + r",?\s+" + _TIME + r"\]\s+(.*)$")
_ANDROID = re.compile(r"^" + _DATE + r",?\s+" + _TIME + r"\s+-\s+(.*)$")

_NOUNS = (
    r"image|video|audio|sticker|document|gif|contact card|voice message|immagine|documento|"
    r"scheda contatto|messaggio vocale|imagen|documento|tarjeta de contacto|mensaje de voz|"
    r"bild|dokument|kontaktkarte|sprachnachricht|vidéo|fichier|carte de contact|message vocal|"
    r"imagem|vídeo|áudio|figurinha|mensagem de voz|cartão de contato"
)
_OMIT = (
    r"omitted|omessa|omesso|omessi|omitida|omitido|omise|omis|weggelassen|ausgelassen|"
    r"oculto|oculta|omitidas|omitidos"
)

# Anything matching one of these (fullmatch, case-insensitive) is dropped as a placeholder.
PLACEHOLDER_PATTERNS = [
    r"<?\s*(?:media|medien|multimedia|médias?|mídia|archivo multimedia)\s+"
    r"(?:omitted|omessi|omesso|ausgeschlossen|omitido|omis|oculta|oculto|omitidos)\s*>?",
    r"<?\s*(?:" + _NOUNS + r")\s+(?:" + _OMIT + r")\s*>?",
    r"<?\s*arquivo de mídia\s+(?:oculto|omitido)\s*>?",
    r"<\s*(?:attached|allegato|angehängt|adjunto|joint|anexado)\s*:[^>]*>",
    r".{0,120}\((?:file attached|file allegato|datei angehängt|archivo adjunto|fichier joint|"
    r"arquivo anexado)\)",
    r"this message was deleted|you deleted this message|questo messaggio è stato eliminato|"
    r"hai eliminato questo messaggio|diese nachricht wurde gelöscht|"
    r"du hast diese nachricht gelöscht|se eliminó este mensaje|eliminaste este mensaje|"
    r"ce message a été supprimé|vous avez supprimé ce message|esta mensagem foi apagada|"
    r"você apagou esta mensagem|mensagem apagada",
    r"missed (?:voice|video) call|(?:chiamata vocale|videochiamata) persa|"
    r"verpasster (?:sprach|video)anruf|(?:llamada|videollamada)(?: de voz)? perdida|"
    r"appel (?:vocal|vidéo) manqué|chamada de (?:voz|vídeo) perdida",
    r"waiting for this message\..*|in attesa del messaggio\..*|warte auf diese nachricht\..*",
    r"null",
]

# Substring patterns for system notices that may carry a group name as author.
SYSTEM_SEARCH_PATTERNS = [
    r"end-to-end encrypted|crittografia end-to-end|ende-zu-ende-verschlüssel|"
    r"cifrados de extremo a extremo|chiffrés de bout en bout|criptografia de ponta a ponta",
]

# Name-prefixed system lines; the name never contains a colon.
_NAME = r"[^:]{1,80}?"
SYSTEM_SUFFIX_PATTERNS = [
    r"joined.*", r"left", r"added .+", r"removed .+", r"was added", r"was removed",
    r"changed the subject.*", r"changed the group .*", r"changed this group's .*",
    r"created group.*", r"changed their phone number.*", r"'s security code changed.*",
    r"ha aggiunto .+", r"ha rimosso .+", r"ha cambiato l'oggetto.*", r"ha cambiato l'icona.*",
    r"ha cambiato la descrizione.*", r"ha creato il gruppo.*", r"è uscit[oa]",
    r"ha lasciato.*", r"si è unit[oa].*", r"è stat[oa] aggiunt[oa]", r"ha cambiato numero.*",
    r"hat .+ hinzugefügt", r"hat .+ entfernt", r"hat den betreff .*",
    r"hat die gruppe .*erstellt", r"hat das gruppenbild .*", r"hat die gruppenbeschreibung.*",
    r"ist beigetreten", r"hat die gruppe verlassen", r"wurde hinzugefügt", r"wurde entfernt",
    r"hat die telefonnummer .*",
    r"añadió a .+", r"eliminó a .+", r"cambió el asunto.*", r"creó el grupo.*", r"salió",
    r"se unió.*", r"cambió el ícono.*", r"cambió la descripción.*",
    r"a ajouté .+", r"a retiré .+", r"a changé l'objet.*", r"a créé le groupe.*",
    r"a quitté.*", r"a rejoint.*", r"a changé l'icône.*",
    r"adicionou .+", r"removeu .+", r"mudou o assunto.*", r"criou o grupo.*", r"saiu",
    r"entrou.*", r"mudou o ícone.*",
]

_EDIT = re.compile(
    r"[ \t]*<[^<>\n]{0,60}\b(?:edited|modificato|bearbeitet|editado|editada|modifié|modificado)"
    r"\b[^<>\n]{0,20}>",
    re.IGNORECASE,
)

_PLACEHOLDER_RE = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in PLACEHOLDER_PATTERNS]
_SYSTEM_SEARCH_RE = [re.compile(p, re.IGNORECASE) for p in SYSTEM_SEARCH_PATTERNS]
_SYSTEM_PREFIX_RE = re.compile(
    r"^" + _NAME + r" (?:" + "|".join(SYSTEM_SUFFIX_PATTERNS) + r")$", re.IGNORECASE | re.DOTALL
)

_THREAD_PATTERNS = [
    r"WhatsApp Chat(?: with| mit| avec| con| com)?\s*[-–]?\s*(.+)",
    r"Chat (?:de )?WhatsApp (?:con|com|avec)\s+(.+)",
    r"Chat WhatsApp\s*[-–]\s*(.+)",
    r"WhatsApp-Chat (?:mit|-)\s*(.+)",
    r"Discussion WhatsApp (?:avec|-)\s*(.+)",
    r"Conversa do WhatsApp (?:com|-)\s*(.+)",
    r"Conversaci[oó]n de WhatsApp con\s+(.+)",
]


def data_dir():
    return Path(os.environ.get("OWN_VOICE_DIR", Path.home() / ".own-voice")).expanduser()


def thread_from_name(filename):
    stem = Path(filename).stem
    for pat in _THREAD_PATTERNS:
        m = re.fullmatch(pat, stem, re.IGNORECASE)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return stem


def is_placeholder(text):
    t = _MARKS.sub("", text).strip()
    if not t:
        return True
    if any(r.fullmatch(t) for r in _PLACEHOLDER_RE):
        return True
    return False


def is_system(rest):
    if any(r.search(rest) for r in _SYSTEM_SEARCH_RE):
        return True
    return _SYSTEM_PREFIX_RE.match(rest) is not None


def clean_text(text):
    text = _EDIT.sub("", text)
    return text.strip()


def _split_entries(content):
    entries = []
    for raw in content.splitlines():
        line = _MARKS.sub("", raw)
        m = _IOS.match(line) or _ANDROID.match(line)
        if m:
            d, t, ap, rest = m.groups()
            entries.append({"d": d, "t": t, "ap": ap, "rest": rest})
        elif entries:
            entries[-1]["rest"] += "\n" + line
    return entries


def _date_fields(d):
    parts = re.split(r"[./-]", d)
    return [int(p) for p in parts], [len(p) for p in parts]


def infer_order(entries, forced=None):
    if forced:
        return forced
    ymd = False
    seen_first_gt12 = seen_second_gt12 = False
    for e in entries:
        nums, lens = _date_fields(e["d"])
        if lens[0] == 4:
            ymd = True
            continue
        if nums[0] > 12:
            seen_first_gt12 = True
        if nums[1] > 12:
            seen_second_gt12 = True
    if ymd:
        return "ymd"
    if seen_first_gt12:
        return "dmy"
    if seen_second_gt12:
        return "mdy"
    return "dmy"


def parse_datetime(e, order):
    nums, lens = _date_fields(e["d"])
    if lens[0] == 4:
        y, mo, d = nums
    elif order == "mdy":
        mo, d, y = nums
    else:
        d, mo, y = nums
    if y < 100:
        y += 2000
    tparts = re.split(r"[:.]", e["t"])
    h, mi = int(tparts[0]), int(tparts[1])
    s = int(tparts[2]) if len(tparts) > 2 else 0
    if e["ap"]:
        pm = e["ap"].lower().startswith("p")
        h = h % 12 + (12 if pm else 0)
    try:
        return datetime(y, mo, d, h, mi, s)
    except ValueError:
        return None


def parse_chat(content, order=None):
    """Return (messages, order); messages are dicts with dt, author, text."""
    entries = _split_entries(content)
    order = infer_order(entries, order)
    messages = []
    for e in entries:
        rest = e["rest"]
        if is_system(rest):
            continue
        if ": " in rest:
            author, text = rest.split(": ", 1)
        else:
            continue
        author = author.strip()
        if not author or is_placeholder(text):
            continue
        text = clean_text(text)
        if not text or is_placeholder(text):
            continue
        messages.append({"dt": parse_datetime(e, order), "author": author, "text": text})
    return messages, order


def read_export(path):
    p = Path(path)
    if p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p) as zf:
            names = [i for i in zf.infolist() if i.filename.lower().endswith(".txt")
                     and not i.filename.startswith("__MACOSX")]
            if not names:
                raise ValueError("no .txt chat found in " + p.name)
            pick = next((i for i in names if Path(i.filename).name == "_chat.txt"), None)
            if pick is None:
                pick = max(names, key=lambda i: i.file_size)
            data = zf.read(pick)
            name = Path(pick.filename).name
        thread_src = name if name != "_chat.txt" else p.name
    else:
        data = p.read_bytes()
        thread_src = p.name
    return data.decode("utf-8-sig", errors="replace"), thread_src


def parse_day(value):
    return date.fromisoformat(value) if value else None


def in_range(dt, since, before):
    if since is None and before is None:
        return True
    if dt is None:
        return False
    d = dt.date()
    if since and d < since:
        return False
    if before and d >= before:
        return False
    return True


def build_records(messages, thread, me_names, keep_others=False, since=None, before=None):
    me = {n.casefold() for n in me_names}
    authors = []
    for m in messages:
        if m["author"] not in authors:
            authors.append(m["author"])
    one_to_one = len(authors) == 2
    out = []
    skipped = 0
    for m in messages:
        is_me = m["author"].casefold() in me
        if not is_me and not keep_others:
            skipped += 1
            continue
        if not in_range(m["dt"], since, before):
            skipped += 1
            continue
        if one_to_one:
            other = next(a for a in authors if a != m["author"])
            recipient = other
        else:
            recipient = thread
        out.append({
            "source": SOURCE, "channel": "chat",
            "date": m["dt"].isoformat() if m["dt"] else None,
            "thread": thread, "recipient": recipient, "author": m["author"],
            "is_me": is_me, "text": redact.redact(m["text"]),
        })
    return out, skipped


def write_records(records, out_path, append):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "a" if append else "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def date_range(records):
    ds = sorted(r["date"] for r in records if r["date"])
    return (ds[0][:10], ds[-1][:10]) if ds else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("inputs", nargs="+", help=".zip exports or .txt chat files")
    ap.add_argument("--me", action="append", default=[], help="your display name (repeatable)")
    ap.add_argument("--thread", help="override the thread name")
    ap.add_argument("--keep-others", action="store_true", help="also keep other people's messages")
    ap.add_argument("--date-order", choices=["dmy", "mdy"], help="force day/month order")
    ap.add_argument("--out", help="output JSONL path")
    ap.add_argument("--append", action="store_true")
    ap.add_argument("--since", help="ISO date, inclusive")
    ap.add_argument("--before", help="ISO date, exclusive")
    args = ap.parse_args(argv)
    try:
        since, before = parse_day(args.since), parse_day(args.before)
    except ValueError:
        ap.error("--since/--before must be ISO dates (YYYY-MM-DD)")

    parsed = []
    for path in args.inputs:
        try:
            content, thread_src = read_export(path)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            print("cannot read %s: %s" % (path, exc), file=sys.stderr)
            return 1
        messages, _ = parse_chat(content, args.date_order)
        parsed.append((path, thread_src, messages))

    if not args.me:
        for path, _, messages in parsed:
            counts = collections.Counter(m["author"] for m in messages)
            print("%s:" % path)
            for author, n in counts.most_common():
                print("  %s: %d" % (author, n))
        print("Pass your name(s) with --me NAME to import.", file=sys.stderr)
        return 2

    records, skipped = [], 0
    for path, thread_src, messages in parsed:
        thread = args.thread or thread_from_name(thread_src)
        recs, sk = build_records(messages, thread, args.me, args.keep_others, since, before)
        records.extend(recs)
        skipped += sk
    out = args.out or data_dir() / "raw" / (SOURCE + ".jsonl")
    write_records(records, out, args.append)
    rng = date_range(records)
    print("written: %d, skipped: %d, range: %s" % (
        len(records), skipped, "%s to %s" % rng if rng else "n/a"))
    print("output: %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
