#!/usr/bin/env python3
"""Build redacted digests of Claude Code session history.

Reads session transcripts (projects/**/*.jsonl) and history.jsonl from a Claude
directory and writes stats.json plus date-ordered markdown shards, so recurring
workflows can be spotted and turned into skills, agents, rules or hooks.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from redact import redact  # noqa: E402

_NOISE = re.compile(r"<((?:system-reminder|command-[\w-]+|local-command-[\w-]+))>.*?</\1>", re.S)
_NOISE_OPEN = re.compile(r"^\s*<(?:system-reminder|command-[\w-]+|local-command-[\w-]+)>")
_CMD_NAME = re.compile(r"<command-name>\s*(/[\w:.-]+)\s*</command-name>")
_SLASH = re.compile(r"^/([\w:.-]+)")


def data_dir():
    return Path(os.environ.get("OWN_VOICE_DIR", Path.home() / ".own-voice")).expanduser()


def parse_ts(value):
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
        if isinstance(value, str) and value:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError, OSError):
        pass
    return None


def iso_day(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d") if dt else None


def read_jsonl(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    yield rec
    except OSError:
        return


def user_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
        return "\n".join(parts)
    return ""


def clean_prompt(text):
    """Return (prompt_text or None, slash_commands list)."""
    cmds = _CMD_NAME.findall(text)
    stripped = _NOISE.sub("", text).strip()
    if not stripped or _NOISE_OPEN.match(stripped) or stripped.startswith("[Request interrupted"):
        return None, cmds
    m = _SLASH.match(stripped)
    if m:
        cmds.append("/" + m.group(1))
    return stripped, cmds


def short(text, limit):
    t = " ".join(text.split())
    return t if len(t) <= limit else t[: max(limit - 3, 0)] + "..."


def home_short(p):
    if not isinstance(p, str):
        return "(unknown)"
    home = str(Path.home())
    return "~" + p[len(home):] if p.startswith(home) else p


def parse_session(path):
    s = {"id": path.stem, "project": None, "start": None, "end": None, "prompts": [],
         "tools": Counter(), "skills": Counter(), "subagents": [], "commands": Counter(), "source": "session"}
    seen_id = False
    for rec in read_jsonl(path):
        if rec.get("isSidechain"):
            continue
        if not seen_id and isinstance(rec.get("sessionId"), str):
            s["id"] = rec["sessionId"]
            seen_id = True
        if s["project"] is None and isinstance(rec.get("cwd"), str):
            s["project"] = rec["cwd"]
        ts = parse_ts(rec.get("timestamp"))
        if ts:
            s["start"] = min(s["start"], ts) if s["start"] else ts
            s["end"] = max(s["end"], ts) if s["end"] else ts
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if rec.get("type") == "user" and not rec.get("isMeta"):
            prompt, cmds = clean_prompt(user_text(content))
            s["commands"].update(cmds)
            if prompt:
                s["prompts"].append((ts, prompt))
        elif rec.get("type") == "assistant" and isinstance(content, list):
            for b in content:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                name = b.get("name")
                inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                if not isinstance(name, str):
                    continue
                s["tools"][name] += 1
                if name == "Skill" and isinstance(inp.get("skill"), str):
                    s["skills"][inp["skill"]] += 1
                elif name in ("Agent", "Task"):
                    s["subagents"].append((str(inp.get("subagent_type") or "general-purpose"), inp.get("model")))
    return s


def history_sessions(path, known):
    groups = {}
    for rec in read_jsonl(path):
        text = rec.get("display")
        if not isinstance(text, str):
            continue
        prompt, cmds = clean_prompt(text)
        if not prompt or " ".join(prompt.split())[:200] in known:
            continue
        ts = parse_ts(rec.get("timestamp"))
        project = rec.get("project") if isinstance(rec.get("project"), str) else None
        key = (project, iso_day(ts))
        s = groups.setdefault(key, {
            "id": "history-" + (iso_day(ts) or "undated"), "project": project, "start": ts, "end": ts,
            "prompts": [], "tools": Counter(), "skills": Counter(), "subagents": [],
            "commands": Counter(), "source": "history"})
        s["prompts"].append((ts, prompt))
        s["commands"].update(cmds)
        if ts:
            s["start"] = min(s["start"], ts) if s["start"] else ts
            s["end"] = max(s["end"], ts) if s["end"] else ts
    return list(groups.values())


def collect(claude_dir, since=None):
    claude_dir = Path(claude_dir).expanduser()
    sessions = [parse_session(p) for p in sorted((claude_dir / "projects").rglob("*.jsonl"))]
    sessions = [s for s in sessions if s["prompts"] or s["tools"]]
    known = {" ".join(t.split())[:200] for s in sessions for _, t in s["prompts"]}
    sessions += history_sessions(claude_dir / "history.jsonl", known)
    if since:
        sessions = [s for s in sessions if s["start"] and iso_day(s["start"]) >= since[:10]]
    sessions.sort(key=lambda s: (s["start"] or datetime.max.replace(tzinfo=timezone.utc), s["id"]))
    return sessions


def render_session(s, max_chars):
    proj = redact(home_short(s["project"]))
    span = "{} -> {}".format(iso_day(s["start"]) or "?", iso_day(s["end"]) or "?")
    head = ["## {} | {} | {}".format(redact(s["id"])[:8], proj, span),
            "prompts: {} | source: {}".format(len(s["prompts"]), s["source"])]
    if s["tools"]:
        head.append("tools: " + ", ".join("{} {}".format(k, v) for k, v in s["tools"].most_common()))
    if s["skills"]:
        head.append("skills: " + ", ".join("{} {}".format(redact(k), v) for k, v in s["skills"].most_common()))
    if s["subagents"]:
        head.append("subagents: " + ", ".join(
            "{}{}".format(redact(t), " ({})".format(m) if m else "") for t, m in s["subagents"]))
    if s["commands"]:
        head.append("commands: " + ", ".join(redact(k) for k in s["commands"]))
    lines = ["{}. {}".format(i, short(redact(t), max_chars)) for i, (_, t) in enumerate(s["prompts"], 1)]
    return head, lines


def build_shards(sessions, max_chars, shard_chars):
    shards, cur = [], ""
    for s in sessions:
        head, lines = render_session(s, max_chars)
        full = "\n".join(head + lines) + "\n\n"
        if len(full) <= shard_chars:
            if cur and len(cur) + len(full) > shard_chars:
                shards.append(cur)
                cur = ""
            cur += full
            continue
        if cur:
            shards.append(cur)
            cur = ""
        chunk = "\n".join(head) + "\n"
        for ln in lines:
            if len(chunk) + len(ln) + 1 > shard_chars and chunk.count("\n") > len(head):
                shards.append(chunk)
                chunk = head[0] + " (cont.)\n"
            chunk += ln + "\n"
        cur = chunk + "\n"
    if cur:
        shards.append(cur)
    return shards


def build_stats(sessions, max_chars=400):
    projects, tools, skills, agents, models, months, cmds, openings = (Counter() for _ in range(8))
    opening_sessions = {}
    total_prompts = 0
    for s in sessions:
        projects[redact(home_short(s["project"]))] += 1
        tools.update(s["tools"])
        skills.update({redact(k): v for k, v in s["skills"].items()})
        for t, m in s["subagents"]:
            agents[redact(t)] += 1
            if m:
                models[str(m)] += 1
        cmds.update({redact(k): v for k, v in s["commands"].items()})
        for ts, text in s["prompts"]:
            total_prompts += 1
            if ts:
                months[ts.astimezone(timezone.utc).strftime("%Y-%m")] += 1
            words = redact(text).lower().split()
            if len(words) >= 4 and not words[0].startswith("/"):
                key = " ".join(words[:6])
                openings[key] += 1
                opening_sessions.setdefault(key, set()).add(s["id"])
    rep = [[k, v] for k, v in openings.most_common() if v >= 2 and len(opening_sessions[k]) >= 2][:20]
    return {
        "sessions": len(sessions),
        "prompts": total_prompts,
        "sessions_per_project": dict(projects.most_common()),
        "top_tools": tools.most_common(20),
        "top_skills": skills.most_common(20),
        "top_subagent_types": agents.most_common(20),
        "subagent_models": dict(models.most_common()),
        "top_slash_commands": cmds.most_common(20),
        "prompts_per_month": dict(sorted(months.items())),
        "repeated_prompt_openings": rep,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--claude-dir", default="~/.claude")
    ap.add_argument("--out")
    ap.add_argument("--since", help="only sessions starting on/after this date (YYYY-MM-DD)")
    ap.add_argument("--max-prompt-chars", type=int, default=400)
    ap.add_argument("--shard-chars", type=int, default=120000)
    args = ap.parse_args(argv)
    claude_dir = Path(args.claude_dir).expanduser()
    if not claude_dir.is_dir():
        print("error: {} is not a directory".format(claude_dir), file=sys.stderr)
        return 2
    out = Path(args.out).expanduser() if args.out else data_dir() / "history"
    sessions = collect(claude_dir, args.since)
    shards = build_shards(sessions, args.max_prompt_chars, args.shard_chars)
    stats = build_stats(sessions, args.max_prompt_chars)
    out.mkdir(parents=True, exist_ok=True)
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    for i, text in enumerate(shards, 1):
        (out / "digest_{:03d}.md".format(i)).write_text(text, encoding="utf-8")
    stale = [p.name for p in out.glob("digest_*.md") if int(re.sub(r"\D", "", p.stem) or 0) > len(shards)]
    print("sessions: {}, prompts: {}, shards written: {} -> {}".format(
        stats["sessions"], stats["prompts"], len(shards), out))
    if stale:
        print("warning: stale shards from a previous run left in place: " + ", ".join(sorted(stale)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
