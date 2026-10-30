#!/usr/bin/env python3
"""Detect and redact secrets and personal identifiers in text.

Usage:
  redact.py FILE        print the redacted text to stdout ("-" reads stdin)
  redact.py --check FILE...   print path:line: kind for each finding, exit 1 if any
"""
import argparse
import re
import sys

PRIORITY = ["private_key", "jwt", "api_key", "password", "iban", "card", "email", "phone"]

_PASSWORD_LABELS = (
    r"password|passwd|pwd|pass|passwort|kennwort|contraseña|clave|mot de passe|mdp|"
    r"senha|parola d'ordine|пароль"
)
_STRICT_LABELS = {
    "password", "passwd", "passwort", "kennwort", "contraseña", "mot de passe",
    "mdp", "senha", "parola d'ordine", "пароль",
}

_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----.*?-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----",
    re.DOTALL,
)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
_API_PREFIX = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"sk-[A-Za-z0-9_\-]{16,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[bpas]-[A-Za-z0-9\-]{10,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|AIza[0-9A-Za-z_\-]{35}"
    r"|glpat-[A-Za-z0-9_\-]{20,}"
    r")"
)
_API_GENERIC = re.compile(
    r"\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|key)\b\s*[:=]\s*[\"']?"
    r"([A-Za-z0-9_\-./+=]{16,})",
    re.IGNORECASE,
)
_PASSWORD = re.compile(
    r"(?<![\w])(" + _PASSWORD_LABELS + r")(?![\w])"
    r"(?:\s*[:=]\s*|\s+(?:is|è|es|ist|est|é|в)\s+|\s+)[\"']?([^\s\"']+)",
    re.IGNORECASE,
)
_SEP_ONLY = re.compile(r"\s*[:=]")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")
_CARD = re.compile(r"(?<![\d-])\d(?:[ -]?\d){12,18}(?![\d-])")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
_PHONE_INTL = re.compile(r"(?<![\w+])\+\d[\d ()\-.]{6,18}\d(?!\d)")
_PHONE_LOCAL = re.compile(r"(?<![\d.\-/])(?:\d{3}([ .\-])\d{3}\1\d{4}|3\d{2}[ .\-]\d{3}[ .\-]?\d{4})(?![\d.\-/])")


def _luhn(digits):
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _spans(text):
    out = []

    def add(kind, m, group=0):
        out.append((m.start(group), m.end(group), kind))

    for m in _PRIVATE_KEY.finditer(text):
        add("private_key", m)
    for m in _JWT.finditer(text):
        add("jwt", m)
    for m in _API_PREFIX.finditer(text):
        add("api_key", m)
    for m in _API_GENERIC.finditer(text):
        if any(c.isdigit() for c in m.group(1)):
            add("api_key", m, 1)
    for m in _PASSWORD.finditer(text):
        label, value = m.group(1).lower(), m.group(2).rstrip(".,;)!?")
        if not value:
            continue
        explicit = _SEP_ONLY.match(text, m.end(1)) is not None
        has_is = re.match(r"\s+(?:is|è|es|ist|est|é|в)\s+", text[m.end(1):], re.IGNORECASE)
        if not explicit and not has_is:
            if label not in _STRICT_LABELS:
                continue
            if len(value) < 4 or not any(not c.isalpha() for c in value):
                continue
        start = m.start(2)
        out.append((start, start + len(value), "password"))
    for m in _IBAN.finditer(text):
        compact = m.group(0).replace(" ", "")
        if 15 <= len(compact) <= 34 and sum(c.isdigit() for c in compact) >= 6:
            add("iban", m)
    for m in _CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            add("card", m)
    for m in _EMAIL.finditer(text):
        add("email", m)
    for m in _PHONE_INTL.finditer(text):
        if 8 <= sum(c.isdigit() for c in m.group(0)) <= 15:
            add("phone", m)
    for m in _PHONE_LOCAL.finditer(text):
        add("phone", m)
    return out


def _resolve(text):
    ordered = sorted(_spans(text), key=lambda s: (PRIORITY.index(s[2]), s[0]))
    kept = []
    for start, end, kind in ordered:
        if all(end <= ks or start >= ke for ks, ke, _ in kept):
            kept.append((start, end, kind))
    return sorted(kept)


def find_secrets(text):
    return [(kind, text[s:e]) for s, e, kind in _resolve(text)]


def redact(text):
    spans = _resolve(text)
    for start, end, kind in reversed(spans):
        text = text[:start] + "[REDACTED:" + kind + "]" + text[end:]
    return text


def _read(path):
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def check_file(path):
    text = _read(path)
    findings = []
    for start, _end, kind in _resolve(text):
        findings.append((text.count("\n", 0, start) + 1, kind))
    return findings


def main(argv=None):
    ap = argparse.ArgumentParser(description="Redact secrets and personal identifiers.")
    ap.add_argument("files", nargs="+", help="input files, '-' for stdin")
    ap.add_argument("--check", action="store_true", help="report findings, never values")
    args = ap.parse_args(argv)
    if args.check:
        found = unreadable = False
        for path in args.files:
            try:
                findings = check_file(path)
            except OSError:
                print("%s: cannot read" % path, file=sys.stderr)
                unreadable = True
                continue
            for line, kind in findings:
                found = True
                print("%s:%d: %s" % ("<stdin>" if path == "-" else path, line, kind))
        return 2 if unreadable else 1 if found else 0
    for path in args.files:
        sys.stdout.write(redact(_read(path)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
