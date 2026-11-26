"""Episodic memory and relationship ledger of a heteronym.

characters/<name>/memory/
    ledger.jsonl          new entries, one JSON object per line, append-only
    summary.md            the compacted memory, written by the model
    archive.jsonl         every entry ever compacted (nothing is deleted)

The server has no model of its own, so compaction is a two-step handshake:
recall() shows the summary and the open entries, the model writes a new
summary, and compact() saves it and moves the open entries to the archive.

The ledger is plain text on this machine. It holds whatever the model chose to
remember about the user, so it is private data: never commit it.
"""

import json
import os

# Owner-only: these files hold what a character knows about its user.
_PRIVATE_FILE, _PRIVATE_DIR = 0o600, 0o700


def _private(path, flags):
    """opener for open(): new files are owner-only, and old ones are made so."""
    fd = os.open(path, flags, _PRIVATE_FILE)
    # os.fchmod exists on Windows only from Python 3.13, and Windows ignores these bits anyway.
    fchmod = getattr(os, "fchmod", None)
    if fchmod:
        fchmod(fd, _PRIVATE_FILE)
    return fd
from datetime import datetime, timezone

KINDS = ("decision", "preference", "arc", "relationship", "note")
MAX_TEXT = 1000
SHOW_OPEN = 40          # open entries shown by recall()
COMPACT_AFTER = 30      # recall() suggests compaction past this many


def _dir(char_dir):
    return os.path.join(char_dir, "memory")


def _read_jsonl(path):
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def remember(char_dir, text, kind="note", who=""):
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    text = " ".join(str(text).split())
    if not text:
        raise ValueError("nothing to remember")
    entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "kind": kind, "text": text[:MAX_TEXT]}
    if who:
        entry["who"] = str(who)[:80]
    os.makedirs(_dir(char_dir), mode=_PRIVATE_DIR, exist_ok=True)
    os.chmod(_dir(char_dir), _PRIVATE_DIR)
    with open(os.path.join(_dir(char_dir), "ledger.jsonl"), "a", encoding="utf-8", opener=_private) as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def _summary(char_dir):
    path = os.path.join(_dir(char_dir), "summary.md")
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def _line(e):
    who = f" ({e['who']})" if e.get("who") else ""
    return f"- [{e['ts'][:10]} {e['kind']}{who}] {e['text']}"


def recall(char_dir, limit=SHOW_OPEN):
    """The memory as text: summary first, then the newest open entries."""
    summary, open_entries = _summary(char_dir), _read_jsonl(os.path.join(_dir(char_dir), "ledger.jsonl"))
    if not summary and not open_entries:
        return ""
    parts = []
    if summary:
        parts.append("### Summary\n" + summary)
    if open_entries:
        shown = open_entries[-limit:]
        head = f"### Since the summary ({len(open_entries)} entries"
        head += f", newest {len(shown)} shown)" if len(shown) < len(open_entries) else ")"
        parts.append(head + "\n" + "\n".join(_line(e) for e in shown))
    if len(open_entries) > COMPACT_AFTER:
        parts.append(f"({len(open_entries)} open entries: time to compact. Write a new summary that "
                     "keeps decisions, preferences, open arcs and relationships, and save it with compact_memory.)")
    return "\n\n".join(parts)


def compact(char_dir, summary):
    """Save the model's summary and archive the entries it covered. Returns how many."""
    summary = summary.strip()
    if not summary:
        raise ValueError("empty summary: nothing would be kept")
    d = _dir(char_dir)
    os.makedirs(d, mode=_PRIVATE_DIR, exist_ok=True)
    os.chmod(d, _PRIVATE_DIR)
    ledger = os.path.join(d, "ledger.jsonl")
    entries = _read_jsonl(ledger)
    # Archive first: if the summary write then fails, the entries are still on disk.
    with open(os.path.join(d, "archive.jsonl"), "a", encoding="utf-8", opener=_private) as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    tmp = os.path.join(d, "summary.md.tmp")
    with open(tmp, "w", encoding="utf-8", opener=_private) as f:
        f.write(summary + "\n")
    os.replace(tmp, os.path.join(d, "summary.md"))
    open(ledger, "w", opener=_private).close()
    return len(entries)


def _self_test():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        assert recall(d) == ""
        remember(d, "Prefers answers without bullet lists", "preference", who="user")
        remember(d, "We  agreed to   investigate the tower first", "decision")
        text = recall(d)
        assert "(user)] Prefers answers" in text and "agreed to investigate" in text, text
        assert compact(d, "User dislikes bullets. Tower first.") == 2
        assert recall(d).startswith("### Summary\nUser dislikes bullets"), recall(d)
        assert len(_read_jsonl(os.path.join(d, "memory", "archive.jsonl"))) == 2
        for name in ("ledger.jsonl", "archive.jsonl", "summary.md"):
            assert os.stat(os.path.join(d, "memory", name)).st_mode & 0o777 == 0o600, name
        assert os.stat(os.path.join(d, "memory")).st_mode & 0o777 == 0o700
        for i in range(COMPACT_AFTER + 1):
            remember(d, f"n{i}")
        assert "time to compact" in recall(d)
        for bad in (lambda: remember(d, "x", "gossip"), lambda: compact(d, "  ")):
            try:
                bad()
            except ValueError:
                pass
            else:
                raise AssertionError("bad input accepted")
    return True


if __name__ == "__main__":
    print("memory self-test:", _self_test())
