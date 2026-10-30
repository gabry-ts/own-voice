# own-voice

own-voice is a Claude Code plugin that builds a voice system from your own texts (WhatsApp chats, sent email, call transcripts, your posts and documents) so Claude can write messages, emails and posts that sound like you, in any language. It also analyzes your Claude Code history and proposes skills, agents, CLAUDE.md rules and hooks backed by evidence. The plugin ships only the system. Your data stays on your machine.

## Install

On Claude Code 2.1.275 or later, one step:

```
/plugin install own-voice --marketplace gabry-ts/own-voice
```

On older versions, two steps:

```
/plugin marketplace add gabry-ts/own-voice
/plugin install own-voice@own-voice
```

Requirements:

- Claude Code
- Python 3.9 or newer, standard library only
- macOS, Linux or Windows (WSL works)
- Apple Mail import works on macOS only
- A clipboard tool: `pbcopy` (macOS), `wl-copy`, `xclip` or `xsel` (Linux), `clip` (Windows)

## Quick start

| Skill | What it does | Example |
|---|---|---|
| `/own-voice:setup` | Imports and cleans your texts, then builds your voice sheets and lexicon | `/own-voice:setup ~/Downloads/chats` |
| `/own-voice:write` | Writes a reply or message in your voice and copies it to the clipboard | `/own-voice:write Anna asks if we can move Friday's meeting. Say yes, 11 works.` |
| `/own-voice:learn` | Compares a draft with what you actually sent and turns the changes into corrections; refreshes the sheets with new exports | `/own-voice:learn this is what I sent: ...` |
| `/own-voice:history` | Reads your Claude Code history and proposes skills, agents, rules and hooks. Read only | `/own-voice:history since 2026-01-01` |

Run setup once, then use write day to day. Paste what you really sent into learn so your corrections accumulate.

### Exporting WhatsApp chats

Export without media. iOS: open the chat, tap the contact or group name, then Export Chat, then Without Media. Android: open the chat, tap the three dots, then More, Export chat, Without media. You get a `.zip` or `.txt` file per chat.

### Getting your sent mail

- Gmail: use Google Takeout and select Mail (optionally only the Sent label). You get an `.mbox` file.
- Thunderbird: export the Sent folder, or copy its mbox file from the profile folder. Individual `.eml` files also work.
- Apple Mail (macOS): no export needed, the importer reads the Sent mailboxes directly. macOS will ask for permission to control Mail.

Call transcripts (`.md` or `.txt`) and your own posts and documents (`.txt`, `.md`) are imported from files or folders.

## How it works

```
import -> redact -> hygiene -> registers -> sheets -> lexicon -> write -> learn
                                                                   ^        |
                                                                   +--------+
```

1. Import: each source becomes JSONL records with your messages only.
2. Redact: secrets and personal identifiers are removed at import.
3. Hygiene: you drop automated or non-authored threads, check text that looks more polished than the rest of your writing, and apply an AI cutoff (default 2022-11-30). Email and long-form text written on or after the cutoff are dropped. Chats and spoken text are kept.
4. Registers: groups of threads that share tone and audience (for example friends, work, public posts) are proposed from the data. You confirm, rename, merge or split them.
5. Sheets: each register gets a measured part (numbers) and an observed part (claims backed by a number or a short anonymized quote).
6. Lexicon: words typical of assistant-written text that you never use form a black list, each paired with the substitute you actually write.
7. Write: Claude drafts from your sheet and lexicon, runs the draft against the black list, and copies the result.
8. Learn: you paste what you really sent, and each meaningful change becomes an author correction. Author corrections override the measurements.

Nothing is saved without your approval.

## Your data

Everything lives in `~/.own-voice/`. Set `OWN_VOICE_DIR` to use another location.

```
~/.own-voice/
  config.json        languages, your names, AI cutoff, registers, excluded threads
  raw/               imported records, one JSONL file per source
  corpus/            cleaned corpus used for measuring
  work/              scratch files
  voice/
    <register>.md    one sheet per register
    lexicon.md       black list with your substitutes
    blacklist.txt    one term per line, used by the check
  history/           redacted digests and stats from /own-voice:history
```

Recommendations:

- Keep this folder in a private git repository, never a public one.
- Delete `raw/` and `work/` once setup is done. The cleaned corpus stays in `corpus/`.
- Delete the original exports too, often left in Downloads. They commonly contain passwords, keys and IBANs.
- Importers redact secrets, and every sheet and the lexicon are checked with `redact.py --check` before saving. This is heuristic, so read your sheets.
- `history/` holds your prompts, even if redacted. Delete it when you no longer need it.

## Scripts

Skills run these for you. All are plain Python and run with `python3 -I scripts/<name>.py`. Importers share `--out`, `--append`, `--since` and `--before`.

| Script | Purpose | Main flags |
|---|---|---|
| `import_whatsapp.py` | WhatsApp `.zip` or `.txt` exports | `--me`, `--thread`, `--keep-others`, `--date-order dmy\|mdy` |
| `import_mbox.py` | Sent email from `.mbox` or `.eml` files or folders | `--me` (repeatable), `--keep-others` |
| `import_applemail.py` | Sent mail from Apple Mail (macOS only) | `--mailbox`, `--batch`, `--limit` |
| `import_transcripts.py` | Call transcripts, Kaiku folders or `.md`/`.txt` | `--me`, `--language`, `--exclude-source`, `--keep-others` |
| `import_text.py` | Your own `.txt`/`.md` files or stdin | `--channel chat\|email\|post\|text`, `--thread`, `--split none\|paragraphs`, `--date` |
| `profile.py measure` | Style metrics per group | `--group-by register\|thread\|channel\|none`, `--format`, `--examples`, `--config`, `--stopwords` |
| `profile.py polished` | Find messages more polished than the rest | `--threshold`, `--channel`, `--top` |
| `profile.py filter` | Write the cleaned corpus | `--drop-indexes`, `--drop-threads`, `--before`, `--channels`, `--dedup`, `--dedup-against`, `--out` |
| `profile.py threads` | Per-thread features | `--format json\|markdown` |
| `lexicon.py count` | Count listed terms in your corpus | `--terms`, `--corpus`, `--by channel\|register\|none`, `--accent-insensitive` |
| `lexicon.py top` | Most frequent words or phrases | `--corpus`, `--ngram 1\|2\|3`, `--n`, `--min-count`, `--by` |
| `lexicon.py check` | Check a draft against the black list (exit 1 on hits) | `--terms`, `--accent-insensitive`, file or `-` |
| `redact.py` | Redact secrets and identifiers; with `--check`, report findings without values | `--check` |
| `clipboard.py` | Copy stdin or a file to the clipboard | `--file`, `--keep-final-newline` |
| `history_digest.py` | Redacted digests of Claude Code sessions | `--claude-dir`, `--out`, `--since`, `--max-prompt-chars`, `--shard-chars` |

When an importer cannot tell which author is you, it exits with code 2 and prints the names it found. Pick yours and rerun with `--me`.

## Limitations

- Redaction is heuristic. Review your sheets before relying on them.
- Languages written without spaces between words are measured in characters, not words.
- If you write in several languages, keep one language per register. Measurements and the lexicon are per register.
- Apple Mail can be slow on large mailboxes. Use `--since`, `--before` or `--limit`.
- Transcription errors and dictation typos are not voice. Sheets list them as errors to fix.
- A register with under about 2,000 words of real prose is marked indicative only.
- The plugin never sends anything and never creates drafts in any service. It copies text to your clipboard.

## Development

```
python3 -m unittest discover tests
```

Test fixtures are synthetic.

## License

MIT. See [LICENSE](LICENSE).
