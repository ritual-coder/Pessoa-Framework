"""Prima Materia digests: a compact, evidence-backed map of one subject in a text.

A raw source (a novel, a corpus of notes) can run to 80k tokens, most of it
not about the subject. A digest is a semantic graph the model writes once from
the full text -- people, places, objects, fears and themes as nodes, their
relations as edges, each backed by short quotes -- so later creation runs can
work from a few thousand tokens instead.

A digest is lossy and model-written, so every quote is checked against the
source on save: a quote the text does not contain is reported, never trusted.
"""

import json
import re

REQUIRED_KEYS = ("subject", "nodes", "edges")

_QUOTE_CHARS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                              "—": "-", "–": "-", "…": "..."})


def unfence(text):
    """Drop a ```json ... ``` wrapper. EVE is told to emit each file as a code
    block, so models often carry the fence into a blob or digest."""
    # Each end is stripped on its own: models often drop the closing fence.
    text = re.sub(r"^```[\w-]*[ \t]*\n", "", text.strip())
    return re.sub(r"\n?```$", "", text)


def normalize(text):
    """Lowercase, unify quote/dash styles and collapse whitespace for matching."""
    return re.sub(r"\s+", " ", text.translate(_QUOTE_CHARS).lower()).strip()


def parse_digest(raw):
    """Parse and structurally check a digest. Returns (digest, problems)."""
    try:
        digest = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as e:
        return None, [f"not valid JSON: {e}"]
    if not isinstance(digest, dict):
        return None, ["a digest must be a JSON object"]
    problems = [f"missing '{k}'" for k in REQUIRED_KEYS if k not in digest]
    if problems:
        return None, problems
    if not str(digest["subject"]).strip():
        return None, ["'subject' is empty"]
    if not isinstance(digest["nodes"], list) or not isinstance(digest["edges"], list):
        return None, ["'nodes' and 'edges' must be lists"]

    ids = {n.get("id") for n in digest["nodes"] if isinstance(n, dict)}
    for e in digest["edges"]:
        if not isinstance(e, dict):
            problems.append(f"edge is not an object: {e!r}")
            continue
        for end in ("from", "to"):
            if e.get(end) not in ids:
                problems.append(f"edge {e.get('from')!r} -> {e.get('to')!r}: unknown node {e.get(end)!r}")
    return digest, problems


def _quotes(digest):
    """Every (where-it-sits, quote) pair in the digest."""
    for n in digest["nodes"]:
        for ev in (n.get("evidence") or []) if isinstance(n, dict) else []:
            yield f"node {n.get('id')}", ev.get("quote", "") if isinstance(ev, dict) else ""
    for e in digest["edges"]:
        for ev in (e.get("evidence") or []) if isinstance(e, dict) else []:
            yield f"edge {e.get('from')}->{e.get('to')}", ev.get("quote", "") if isinstance(ev, dict) else ""
    for ev in digest.get("voice_samples") or []:
        yield "voice_samples", ev.get("quote", "") if isinstance(ev, dict) else ""


def check_quotes(digest, source_text):
    """Return (verified_count, [(where, quote) not found in the source])."""
    haystack = normalize(source_text)
    verified, missing = 0, []
    for where, quote in _quotes(digest):
        if quote and normalize(quote) in haystack:
            verified += 1
        else:
            missing.append((where, quote))
    return verified, missing


def _self_test():
    src = "She told herself, “It’s only weather—but it frightens me.”"
    d, probs = parse_digest(json.dumps({
        "subject": "Mara", "nodes": [{"id": "mara", "evidence": [
            {"quote": "it's only weather-but it frightens me"}, {"quote": "invented line"}]}],
        "edges": [{"from": "mara", "to": "ghost"}]}))
    assert probs == ["edge 'mara' -> 'ghost': unknown node 'ghost'"], probs
    ok, missing = check_quotes(d, src)
    assert ok == 1 and missing == [("node mara", "invented line")], (ok, missing)
    assert parse_digest("{")[0] is None and parse_digest('{"subject": "x"}')[0] is None
    return True


if __name__ == "__main__":
    print("digest self-test:", _self_test())
