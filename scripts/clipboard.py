#!/usr/bin/env python3
"""Copy text to the system clipboard on macOS, Windows, WSL and Linux.

Reads stdin or --file as raw bytes and copies it unchanged, except for one final
newline (a shell heredoc always adds one). The command used is printed to stderr.
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys

INSTALL_HINT = (
    "no clipboard command found. Install one: wl-clipboard (Wayland), xclip or xsel (X11); "
    "on macOS pbcopy and on Windows clip are built in."
)


def pick_command(plat, release, env, which):
    """Return (argv, encoding) for the first available clipboard tool, or None."""
    if plat == "darwin":
        return (["pbcopy"], "utf-8") if which("pbcopy") else None
    if plat == "win32":
        return (["clip"], "utf-16-le") if which("clip") else None
    if "microsoft" in (release or "").lower() and which("clip.exe"):
        return (["clip.exe"], "utf-16-le")
    if env.get("WAYLAND_DISPLAY") and which("wl-copy"):
        return (["wl-copy"], "utf-8")
    if which("xclip"):
        return (["xclip", "-selection", "clipboard"], "utf-8")
    if which("xsel"):
        return (["xsel", "--clipboard", "--input"], "utf-8")
    return None


def encode(text, encoding):
    if encoding == "utf-16-le":
        return b"\xff\xfe" + text.encode("utf-16-le")
    return text.encode(encoding)


def copy(text):
    picked = pick_command(sys.platform, platform.release(), os.environ, shutil.which)
    if picked is None:
        raise RuntimeError(INSTALL_HINT)
    argv, encoding = picked
    try:
        proc = subprocess.run(
            argv, input=encode(text, encoding),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
    except OSError as exc:
        raise RuntimeError("could not run {}: {}".format(argv[0], exc))
    if proc.returncode != 0:
        raise RuntimeError("{} exited with status {}".format(argv[0], proc.returncode))
    return argv[0]


def strip_final_newline(raw):
    if raw.endswith(b"\r\n"):
        return raw[:-2]
    if raw.endswith(b"\n"):
        return raw[:-1]
    return raw


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", help="read text from this file instead of stdin")
    ap.add_argument("--keep-final-newline", action="store_true",
                    help="do not remove the single trailing newline")
    args = ap.parse_args(argv)
    if args.file:
        with open(args.file, "rb") as fh:
            raw = fh.read()
    else:
        raw = sys.stdin.buffer.read()
    if not args.keep_final_newline:
        raw = strip_final_newline(raw)
    try:
        used = copy(raw.decode("utf-8"))
    except (RuntimeError, UnicodeDecodeError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1
    print("copied with {}".format(used), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
