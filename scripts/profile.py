#!/usr/bin/env python3
"""Measure writing style from a corpus of the user's own messages.

Subcommands:
  measure   style metrics per group (register, thread, channel or all)
  polished  list own messages that look overly polished
  filter    drop records by index, thread, duplicate, or channel and date, into a clean corpus
  threads   per-thread features to help group threads into registers

Corpus files are JSONL, one record per message. Indexes are positions among the
valid records of the given files, in order, and are stable for the same inputs.
"""
import argparse
import json
import os
import re
import statistics
import sys
import unicodedata
from collections import Counter
from pathlib import Path

SHORT_WORDS, LONG_WORDS = 6, 35
SHORT_CHARS, LONG_CHARS = 12, 80

_CJK = (
    "⺀-⿟぀-ヿ㄀-ㄯ㄰-㆏ㇰ-ㇿ"
    "㐀-䶿一-鿿ꥠ-꥿가-힯豈-﫿"
    "ｦ-ﾟ\U00020000-\U0002fa1f"
)
_CJK_CHAR = re.compile("[" + _CJK + "]")
_TOKEN = re.compile("[" + _CJK + r"]|(?:(?![" + _CJK + r"])\w)+(?:['’](?:(?![" + _CJK + r"])\w)+)*")
_SENT_SPLIT = re.compile(r"(?<=[.!?…。！？])\s+|\n+|(?<=[。！？])")
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿⭐⭕⬛⬜⌀-⏿"
    "←-⇿⤀-⥿〰〽㊗㊙©®‼⁉™ℹ]"
    "️?"
)
_TRAIL_CLOSERS = "\"'»”’)]}*_"
_END_PUNCT = ".!?…。！？"
_ELLIPSIS = re.compile(r"\.{3,}|…")
_LAUGH_FIXED = re.compile(r"^(?:lol+|lmao+|lmfao|rofl|xd+|x+d+|k{3,}|w{3,}|5{3,}|haha\w*|wkwk\w*)$")
_LAUGH_SYL = re.compile(r"^(?:[hjk]?[aeiou][hjk]?){2,}$")
_LIST_LINE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+\S", re.M)
_HEADING = re.compile(r"^\s*(?:#{1,6}\s+\S|\*\*[^*\n]+\*\*\s*:?\s*$)", re.M)


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
    return records


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


def group_key(rec, mode, cfg):
    if mode == "register":
        return register_of(rec, cfg)
    if mode == "thread":
        return str(rec.get("thread") or "(none)")
    if mode == "channel":
        return str(rec.get("channel") or "(none)")
    return "all"


def tokenize(text):
    return _TOKEN.findall(text.casefold())


def is_cjk(text):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    return sum(1 for c in letters if _CJK_CHAR.match(c)) / len(letters) > 0.3


def split_sentences(text):
    return [s for s in (x.strip() for x in _SENT_SPLIT.split(text)) if s]


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def is_laughter(token):
    t = strip_accents(token)
    if _LAUGH_FIXED.match(t):
        return True
    return len(t) >= 4 and bool(re.search("[hjk]", t)) and bool(_LAUGH_SYL.match(t))


def ends_with_punct(text):
    t = text.rstrip()
    while t and t[-1] in _TRAIL_CLOSERS:
        t = t[:-1]
    return bool(t) and t[-1] in _END_PUNCT


def starts_lower(text):
    t = text.lstrip()
    for c in t[:1]:
        if c.isupper() or c.istitle():
            return False
        if c.islower():
            return True
    return None


def pct(n, d):
    return round(100.0 * n / d, 2) if d else None


def per1000(n, words):
    return round(1000.0 * n / words, 2) if words else None


def top(counter, n, min_count=1):
    return [[k, v] for k, v in counter.most_common() if v >= min_count][:n]


def load_stopwords(path):
    if not path:
        return set()
    with open(path, encoding="utf-8") as fh:
        return {w.strip().casefold() for w in fh if w.strip() and not w.startswith("#")}


def own(records):
    return [r for r in records if r.get("is_me") is True and r["text"].strip()]


def measure_group(msgs, stopwords=frozenset(), examples=0):
    texts = [m["text"] for m in msgs]
    joined = "\n".join(texts)
    cjk = is_cjk(joined)
    short_t, long_t = (SHORT_CHARS, LONG_CHARS) if cjk else (SHORT_WORDS, LONG_WORDS)
    words = sentences = 0
    lens = []
    commas = questions = excl = ellipsis = dashes = semis = parens = 0
    low_start = low_den = no_final = multiline = 0
    emoji = Counter()
    laugh = Counter()
    wc, bi, tri = Counter(), Counter(), Counter()
    op = {1: Counter(), 2: Counter(), 3: Counter()}
    cl = {1: Counter(), 2: Counter(), 3: Counter()}
    for t in texts:
        toks = tokenize(t)
        words += len(toks)
        sents = split_sentences(t)
        counted = 0
        for s in sents:
            st = tokenize(s)
            if st:
                lens.append(len(st))
                counted += 1
                for i in range(len(st) - 1):
                    bi[" ".join(st[i:i + 2])] += 1
                for i in range(len(st) - 2):
                    tri[" ".join(st[i:i + 3])] += 1
        sentences += max(counted, 1)
        commas += t.count(",") + t.count("、") + t.count("，")
        questions += t.count("?") + t.count("？")
        excl += t.count("!") + t.count("！")
        ellipsis += len(_ELLIPSIS.findall(t))
        dashes += t.count("—") + t.count("–")
        semis += t.count(";")
        parens += t.count("(")
        sl = starts_lower(t)
        if sl is not None:
            low_den += 1
            low_start += 1 if sl else 0
        if not ends_with_punct(t):
            no_final += 1
        if "\n" in t.strip():
            multiline += 1
        for e in _EMOJI.findall(t):
            emoji[e.replace("️", "")] += 1
        for tok in toks:
            if is_laughter(tok):
                laugh[tok] += 1
            if tok not in stopwords:
                wc[tok] += 1
        for n in (1, 2, 3):
            if len(toks) >= n:
                op[n][" ".join(toks[:n])] += 1
                cl[n][" ".join(toks[-n:])] += 1
    out = {
        "unit": "chars" if cjk else "words",
        "messages": len(msgs),
        "words": words,
        "sentences": sentences,
        "sentence_length": {
            "mean": round(statistics.mean(lens), 2) if lens else None,
            "median": statistics.median(lens) if lens else None,
            "stdev": round(statistics.pstdev(lens), 2) if len(lens) > 1 else None,
        },
        "pct_short_sentences": pct(sum(1 for x in lens if x < short_t), len(lens)),
        "pct_long_sentences": pct(sum(1 for x in lens if x > long_t), len(lens)),
        "short_long_thresholds": [short_t, long_t],
        "questions_per_1000": per1000(questions, words),
        "exclamations_per_1000": per1000(excl, words),
        "commas_per_sentence": round(commas / sentences, 2) if sentences else None,
        "pct_messages_start_lowercase": pct(low_start, low_den),
        "pct_messages_no_final_punctuation": pct(no_final, len(msgs)),
        "ellipsis": ellipsis,
        "dashes_em_en": dashes,
        "semicolons": semis,
        "parentheses": parens,
        "emoji_per_1000": per1000(sum(emoji.values()), words),
        "top_emoji": top(emoji, 10),
        "pct_multiline_messages": pct(multiline, len(msgs)),
        "laughter_per_1000": per1000(sum(laugh.values()), words),
        "top_laughter": top(laugh, 10),
        "top_words": top(wc, 30),
        "top_bigrams": top(bi, 20, 2),
        "top_trigrams": top(tri, 20, 2),
        "openers": {str(n): top(op[n], 15, 1 if n == 1 else 2) for n in (1, 2, 3)},
        "closers": {str(n): top(cl[n], 15, 1 if n == 1 else 2) for n in (1, 2, 3)},
    }
    if examples:
        out["examples"] = pick_examples(texts, examples)
    return out


def pick_examples(texts, n):
    norm = []
    for t in texts:
        s = " ".join(t.split())
        if 3 <= len(tokenize(s)) and len(s) <= 200:
            norm.append(s)
    norm.sort(key=len)
    pool = norm[: max(len(norm) // 2 + 1, 1)]
    if len(pool) <= n:
        return pool
    step = (len(pool) - 1) / (n - 1) if n > 1 else 0
    return [pool[round(i * step)] for i in range(n)]


def render_md(title, data, level=1):
    lines = ["#" * level + " " + title]
    for k, v in data.items():
        if isinstance(v, dict):
            lines.append("")
            lines.extend(render_md(k, v, level + 1))
        elif isinstance(v, list) and v and all(isinstance(x, list) and len(x) == 2 for x in v):
            lines.append("- {}: {}".format(k, ", ".join("{} ({})".format(a, b) for a, b in v)))
        elif isinstance(v, list):
            lines.append("- {}:".format(k))
            lines.extend("  - {}".format(x) for x in v)
        else:
            lines.append("- {}: {}".format(k, v))
    return lines


def cmd_measure(args):
    cfg = load_config(args.config)
    records = own(load_corpus(args.corpus))
    stop = load_stopwords(args.stopwords)
    groups = {}
    for r in records:
        groups.setdefault(group_key(r, args.group_by, cfg), []).append(r)
    result = {g: measure_group(m, stop, args.examples) for g, m in sorted(groups.items())}
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        lines = []
        for g, d in result.items():
            lines.extend(render_md(g, d, 1))
            lines.append("")
        print("\n".join(lines))
    return 0


def _list_lines(t):
    return len(_LIST_LINE.findall(t))


def _title_case_lines(t):
    n = 0
    for line in t.splitlines():
        ws = line.split()
        if len(ws) >= 3 and all(w[:1].isalpha() for w in ws if len(w) > 3):
            big = [w for w in ws if len(w) > 3]
            if big and all(w[0].isupper() for w in big) and not line.isupper():
                n += 1
    return n


def _perfect(t):
    sents = split_sentences(t)
    if len(sents) < 2:
        return False
    return all(s[0].isupper() for s in sents if s[0].isalpha()) and ends_with_punct(t) and \
        all(s[-1] in _END_PUNCT or s[-1] in _TRAIL_CLOSERS for s in sents)


def _features(t):
    toks = tokenize(t)
    lens = [len(tokenize(s)) for s in split_sentences(t)]
    lens = [x for x in lens if x]
    mean = statistics.mean(lens) if lens else 0
    cv = (statistics.pstdev(lens) / mean) if len(lens) >= 3 and mean else None
    return {
        "n": len(toks),
        "mean_len": mean,
        "cv": cv,
        "dash": t.count("—") + t.count("–"),
        "semi": t.count(";"),
        "lists": _list_lines(t),
        "headings": len(_HEADING.findall(t)),
        "titlecase": _title_case_lines(t),
        "perfect": _perfect(t),
    }


def _z(x, vals):
    if len(vals) < 2:
        return 0.0
    sd = statistics.pstdev(vals) or 1.0
    return max(0.0, min(4.0, (x - statistics.median(vals)) / sd))


def score_messages(records, channel=None):
    rows = []
    for idx, r in enumerate(records):
        if r.get("is_me") is not True or not r["text"].strip():
            continue
        if channel and r.get("channel") != channel:
            continue
        rows.append((idx, r, _features(r["text"])))
    base = [f for _, _, f in rows if f["n"] >= 8]
    if not base:
        return []
    lens = [f["mean_len"] for f in base]
    dashes = [f["dash"] for f in base]
    semis = [f["semi"] for f in base]
    cvs = [f["cv"] for f in base if f["cv"] is not None]
    p_perfect = sum(1 for f in base if f["perfect"]) / len(base)
    p_list = sum(1 for f in base if f["lists"] >= 2) / len(base)
    p_head = sum(1 for f in base if f["headings"]) / len(base)
    p_title = sum(1 for f in base if f["titlecase"]) / len(base)
    out = []
    for idx, r, f in rows:
        if f["n"] < 8:
            continue
        s = _z(f["mean_len"], lens) * 0.8
        if f["cv"] is not None and cvs:
            s += _z(-f["cv"], [-c for c in cvs]) * 0.6
        if f["dash"]:
            s += (1 - (sum(1 for d in dashes if d) / len(dashes))) * min(f["dash"], 3) * 0.7
        if f["semi"]:
            s += (1 - (sum(1 for d in semis if d) / len(semis))) * min(f["semi"], 3) * 0.4
        if f["perfect"]:
            s += (1 - p_perfect) * 1.0
        if f["lists"] >= 2:
            s += (1 - p_list) * 1.2
        if f["headings"]:
            s += (1 - p_head) * 1.5
        if f["titlecase"]:
            s += (1 - p_title) * 0.8
        out.append((round(s, 2), idx, r))
    out.sort(key=lambda x: -x[0])
    return out


def cmd_polished(args):
    records = load_corpus(args.corpus)
    scored = [x for x in score_messages(records, args.channel) if x[0] >= args.threshold]
    print("index\tdate\tthread\tscore\ttext")
    for score, idx, r in scored[: args.top]:
        first = " ".join(r["text"].split())[:80]
        print("{}\t{}\t{}\t{}\t{}".format(idx, r.get("date") or "-", r.get("thread") or "-", score, first))
    return 0


def load_thread_names(value):
    path = Path(value).expanduser()
    if path.is_file():
        items = path.read_text(encoding="utf-8").splitlines()
    else:
        items = value.split(",")
    return {x.strip() for x in items if x.strip()}


def dedup_key(r):
    return (r.get("source"), r.get("channel"), r.get("date"), r.get("thread"), r.get("text"))


def cmd_filter(args):
    records = load_corpus(args.corpus)
    drop = set()
    if args.drop_indexes:
        try:
            drop = {int(x) for x in args.drop_indexes.split(",") if x.strip()}
        except ValueError:
            print("error: --drop-indexes must be comma separated integers", file=sys.stderr)
            return 2
    channels = {c.strip() for c in args.channels.split(",") if c.strip()} if args.channels else None
    threads = load_thread_names(args.drop_threads) if args.drop_threads else set()
    dedup = args.dedup or bool(args.dedup_against)
    seen = {dedup_key(r) for r in load_corpus(args.dedup_against or [])}
    dropped = Counter()
    kept = 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for idx, r in enumerate(records):
            if idx in drop:
                dropped["index"] += 1
                continue
            if args.before:
                d = r.get("date")
                if d and str(d)[:10] >= args.before[:10] and (channels is None or r.get("channel") in channels):
                    dropped["before"] += 1
                    continue
            if threads and str(r.get("thread") or "").strip() in threads:
                dropped["thread"] += 1
                continue
            if dedup:
                key = dedup_key(r)
                if key in seen:
                    dropped["duplicate"] += 1
                    continue
                seen.add(key)
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            kept += 1
    print("kept {} of {} records -> {}".format(kept, len(records), out))
    for reason in ("index", "before", "thread", "duplicate"):
        print("dropped by {}: {}".format(reason, dropped[reason]))
    return 0


def cmd_threads(args):
    groups = {}
    for r in own(load_corpus(args.corpus)):
        groups.setdefault(str(r.get("thread") or "(none)"), []).append(r)
    rows = []
    for name, msgs in groups.items():
        texts = [m["text"] for m in msgs]
        toks = [tokenize(t) for t in texts]
        words = sum(len(t) for t in toks)
        low = [starts_lower(t) for t in texts]
        low = [x for x in low if x is not None]
        laugh = sum(1 for tl in toks for tok in tl if is_laughter(tok))
        emoji = sum(len(_EMOJI.findall(t)) for t in texts)
        dates = sorted(str(m["date"])[:10] for m in msgs if m.get("date"))
        ch = Counter(m.get("channel") for m in msgs).most_common(1)
        op = Counter(" ".join(tl[:1]) for tl in toks if tl)
        rows.append({
            "thread": name,
            "channel": ch[0][0] if ch else None,
            "unit": "chars" if is_cjk("\n".join(texts)) else "words",
            "messages": len(msgs),
            "words": words,
            "mean_message_length": round(words / len(msgs), 2),
            "pct_start_lowercase": pct(sum(low), len(low)),
            "emoji_per_1000": per1000(emoji, words),
            "laughter_per_1000": per1000(laugh, words),
            "pct_final_punctuation": pct(sum(1 for t in texts if ends_with_punct(t)), len(texts)),
            "top_openers": top(op, 5),
            "date_range": [dates[0], dates[-1]] if dates else None,
        })
    rows.sort(key=lambda r: -r["words"])
    if args.format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            print("\n".join(render_md(r["thread"], {k: v for k, v in r.items() if k != "thread"}, 2)))
            print()
    return 0


def build_parser():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("measure", help="style metrics per group")
    m.add_argument("corpus", nargs="+")
    m.add_argument("--config")
    m.add_argument("--group-by", choices=["register", "thread", "channel", "none"], default="register")
    m.add_argument("--format", choices=["json", "markdown"], default="json")
    m.add_argument("--examples", type=int, default=0)
    m.add_argument("--stopwords")
    m.set_defaults(func=cmd_measure)
    po = sub.add_parser("polished", help="find overly polished messages")
    po.add_argument("corpus", nargs="+")
    po.add_argument("--threshold", type=float, default=1.5)
    po.add_argument("--channel")
    po.add_argument("--top", type=int, default=30)
    po.set_defaults(func=cmd_polished)
    f = sub.add_parser("filter", help="write a cleaned corpus")
    f.add_argument("corpus", nargs="+")
    f.add_argument("--drop-indexes")
    f.add_argument("--before", help="drop records dated on/after this date (with --channels)")
    f.add_argument("--channels", help="comma separated; default all channels")
    f.add_argument("--drop-threads", help="comma separated thread names, or a file with one per line")
    f.add_argument("--dedup", action="store_true",
                   help="drop records identical to an earlier one (source, channel, date, thread, text)")
    f.add_argument("--dedup-against", nargs="+", metavar="FILE",
                   help="files used only as already-seen records, not written (implies --dedup)")
    f.add_argument("--out", required=True)
    f.set_defaults(func=cmd_filter)
    t = sub.add_parser("threads", help="per-thread features")
    t.add_argument("corpus", nargs="+")
    t.add_argument("--format", choices=["json", "markdown"], default="json")
    t.set_defaults(func=cmd_threads)
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
