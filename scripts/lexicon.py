#!/usr/bin/env python3
"""Count, discover and check vocabulary in the user's own messages.

Subcommands:
  count  occurrences of listed terms per group (channel or register)
  top    most frequent words or phrases per group
  check  list black-listed terms present in a draft (exit 1 if any)
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

_CJK = (
    "⺀-⿟぀-ヿ㄀-ㄯ㄰-㆏ㇰ-ㇿ"
    "㐀-䶿一-鿿ꥠ-꥿가-힯豈-﫿"
    "ｦ-ﾟ\U00020000-\U0002fa1f"
)
_CJK_CHAR = re.compile("[" + _CJK + "]")
_TOKEN = re.compile("[" + _CJK + r"]|(?:(?![" + _CJK + r"])\w)+(?:['](?:(?![" + _CJK + r"])\w)+)*")
_SENT_SPLIT = re.compile(r"(?<=[.!?…。！？])\s+|\n+|(?<=[。！？])")


def data_dir():
    return Path(os.environ.get("OWN_VOICE_DIR", Path.home() / ".own-voice")).expanduser()


def load_corpus(paths):
    records = []
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and isinstance(rec.get("text"), str):
                    records.append(rec)
    return [r for r in records if r.get("is_me") is True and r["text"].strip()]


def load_config(path=None):
    p = Path(path) if path else data_dir() / "config.json"
    try:
        with open(p, encoding="utf-8") as fh:
            cfg = json.load(fh)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def register_of(rec, cfg):
    regs = cfg.get("registers") or []
    thread = rec.get("thread")
    for reg in regs:
        if thread is not None and thread in (reg.get("threads") or []):
            return reg.get("name", "?")
    for reg in regs:
        if rec.get("channel") in (reg.get("channels") or []):
            return reg.get("name", "?")
    return "(unassigned)"


def group_key(rec, by, cfg):
    if by == "register":
        return register_of(rec, cfg)
    if by == "channel":
        return str(rec.get("channel") or "(none)")
    return "all"


def normalize(text, accent_insensitive=False):
    t = text.replace("’", "'").replace("‘", "'")
    if accent_insensitive:
        t = "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))
    else:
        t = unicodedata.normalize("NFC", t)
    return t.casefold()


def tokenize(text):
    return _TOKEN.findall(text)


def load_terms(path):
    terms = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            t = line.strip()
            if t and not t.startswith("#") and t not in terms:
                terms.append(t)
    return terms


def term_regex(term, accent_insensitive=False):
    norm = normalize(term, accent_insensitive)
    parts = [re.escape(p) for p in norm.split()]
    body = r"\s+".join(parts)
    if _CJK_CHAR.search(norm):
        return re.compile(body)
    return re.compile(r"(?<!\w)" + body + r"(?!\w)")


def count_in(rx, text):
    return len(rx.findall(text))


def count_terms(records, terms, by="channel", cfg=None, accent_insensitive=False):
    cfg = cfg or {}
    rxs = {t: term_regex(t, accent_insensitive) for t in terms}
    groups = {}
    unit = "words"
    for r in records:
        g = groups.setdefault(group_key(r, by, cfg), {"messages": 0, "words": 0, "terms": {}})
        text = normalize(r["text"], accent_insensitive)
        toks = tokenize(text)
        if toks and sum(1 for t in toks if _CJK_CHAR.match(t)) / len(toks) > 0.3:
            unit = "chars"
        g["messages"] += 1
        g["words"] += len(toks)
        for t, rx in rxs.items():
            n = count_in(rx, text)
            e = g["terms"].setdefault(t, {"count": 0, "messages": 0})
            e["count"] += n
            e["messages"] += 1 if n else 0
    for g in groups.values():
        for t in terms:
            e = g["terms"].setdefault(t, {"count": 0, "messages": 0})
            e["per_1000"] = round(1000.0 * e["count"] / g["words"], 2) if g["words"] else None
        g["terms"] = {t: g["terms"][t] for t in terms}
    return {"unit": unit, "groups": dict(sorted(groups.items()))}


def top_phrases(records, by="channel", cfg=None, n=300, ngram=1, min_count=3, accent_insensitive=False):
    cfg = cfg or {}
    counters = {}
    for r in records:
        c = counters.setdefault(group_key(r, by, cfg), Counter())
        for s in _SENT_SPLIT.split(normalize(r["text"], accent_insensitive)):
            toks = tokenize(s)
            for i in range(len(toks) - ngram + 1):
                c[" ".join(toks[i:i + ngram])] += 1
    return {
        g: [[k, v] for k, v in c.most_common() if v >= min_count][:n]
        for g, c in sorted(counters.items())
    }


def check_text(text, terms, accent_insensitive=False):
    hits = []
    lines = text.splitlines()
    for t in terms:
        rx = term_regex(t, accent_insensitive)
        total = 0
        where = []
        for i, line in enumerate(lines, 1):
            k = count_in(rx, normalize(line, accent_insensitive))
            if k:
                total += k
                where.append((i, line))
        if total:
            hits.append({"term": t, "count": total, "lines": where})
    return hits


def md_count(result):
    lines = ["unit: " + result["unit"], ""]
    groups = list(result["groups"])
    lines.append("| term | " + " | ".join(groups) + " |")
    lines.append("|---|" + "---|" * len(groups))
    terms = list(next(iter(result["groups"].values()))["terms"]) if groups else []
    for t in terms:
        cells = []
        for g in groups:
            e = result["groups"][g]["terms"][t]
            cells.append("{} ({}/1000, {} msg)".format(e["count"], e["per_1000"], e["messages"]))
        lines.append("| {} | {} |".format(t, " | ".join(cells)))
    lines.append("")
    lines.extend("{}: {} messages, {} {}".format(g, result["groups"][g]["messages"],
                 result["groups"][g]["words"], result["unit"]) for g in groups)
    return "\n".join(lines)


def cmd_count(args):
    result = count_terms(load_corpus(args.corpus), load_terms(args.terms), args.by,
                         load_config(args.config), args.accent_insensitive)
    print(md_count(result) if args.format == "markdown" else json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_top(args):
    result = top_phrases(load_corpus(args.corpus), args.by, load_config(args.config), args.n,
                         args.ngram, args.min_count, args.accent_insensitive)
    if args.format == "markdown":
        for g, items in result.items():
            print("# {}\n".format(g))
            print(", ".join("{} ({})".format(k, v) for k, v in items))
            print()
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_check(args):
    terms = load_terms(args.terms)
    if args.text == "-":
        text = sys.stdin.read()
    else:
        with open(args.text, encoding="utf-8") as fh:
            text = fh.read()
    hits = check_text(text, terms, args.accent_insensitive)
    for h in hits:
        for ln, line in h["lines"]:
            print("{}\t{}\tline {}: {}".format(h["term"], h["count"], ln, line.strip()))
    return 1 if hits else 0


def build_parser():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("count", help="count listed terms")
    c.add_argument("--terms", required=True)
    c.add_argument("--corpus", nargs="+", required=True)
    c.add_argument("--by", choices=["channel", "register", "none"], default="channel")
    c.add_argument("--config")
    c.add_argument("--accent-insensitive", action="store_true")
    c.add_argument("--format", choices=["json", "markdown"], default="json")
    c.set_defaults(func=cmd_count)
    t = sub.add_parser("top", help="most frequent words or phrases")
    t.add_argument("--corpus", nargs="+", required=True)
    t.add_argument("--by", choices=["channel", "register", "none"], default="channel")
    t.add_argument("--config")
    t.add_argument("--n", type=int, default=300)
    t.add_argument("--ngram", type=int, choices=[1, 2, 3], default=1)
    t.add_argument("--min-count", type=int, default=3)
    t.add_argument("--accent-insensitive", action="store_true")
    t.add_argument("--format", choices=["json", "markdown"], default="json")
    t.set_defaults(func=cmd_top)
    k = sub.add_parser("check", help="check a draft against a black list")
    k.add_argument("--terms", required=True)
    k.add_argument("--accent-insensitive", action="store_true")
    k.add_argument("text", metavar="TEXTFILE|-")
    k.set_defaults(func=cmd_check)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except OSError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
