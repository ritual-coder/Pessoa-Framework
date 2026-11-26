"""Rule-based Prima Materia graph: a digest built by Python, with no model.

The third way to feed a source into creation, next to the full text and the
model-written digest. It finds recurring proper names, links them to the
subject by how often they share a paragraph, and backs every node and edge
with a verbatim sentence, so the result passes the same quote check.

Deliberate limits (heuristics, not NLP): names are found by capitalisation,
so pronouns ("he") are invisible and lowercase-name languages fail; relation
labels are co-occurrence counts, not meanings; node types are guesses; the
theme lexicon is English. It is fast, free and reproducible -- and it misses
everything that is not spelled out on the page.
"""

import collections
import re

_WORD = re.compile(r"[^\W\d_][\w'’-]*")
_POSSESSIVE = re.compile(r"['’]s$")
# "I", "I'm", "I'd": capitalised but never names.
_NOT_NAME = re.compile(r"^(I|I['’]\w+|[^\W\d_]|Oh|Ah|Yes|No|OK|Okay|Hey|Well)$")
_PARTICLES = {"de", "da", "di", "van", "von", "la", "le", "del", "der", "du", "of"}
_QUOTE = re.compile(r"[“\"]([^”\"]{20,200})[”\"]")
_SPEECH = re.compile(r"\b(said|asked|told|thought|called|answered|replied|murmured|whispered)\b", re.I)
_PLACE_PREP = re.compile(r"\b(in|at|on|to|from|into|near|toward|towards|across|up)\s+(?:the\s+)?$", re.I)

# Small English affect lexicon: counts, not interpretation.
THEMES = {
    "fear": ("fear", "afraid", "terror", "dread", "horror", "panic", "fright", "frightened", "scared"),
    "grief": ("grief", "mourn", "mourning", "widow", "widower", "dead", "death", "died"),
    "love": ("love", "loved", "loving", "tender", "tenderness", "darling"),
    "guilt": ("guilt", "guilty", "shame", "ashamed", "remorse"),
    "solitude": ("alone", "lonely", "loneliness", "solitary", "isolation"),
    "drink": ("drink", "drank", "drunk", "drinking", "bottle", "liquor", "whisky", "whiskey"),
}


def _paragraphs(text):
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    return [p for p in paras if p and not p.startswith("#")]


def _sentences(paragraph):
    return [s for s in re.split(r"(?<=[.!?])[”\"’)]?\s+", paragraph) if s]


def _name_runs(sentence):
    """Runs of capitalised words (allowing 'de', 'van' ...), skipping sentence-initial words."""
    tokens = [(_POSSESSIVE.sub("", m.group()), m.start()) for m in _WORD.finditer(sentence)]
    runs, run = [], []
    for i, (tok, start) in enumerate(tokens):
        if tok[0].isupper() and i > 0 and not _NOT_NAME.match(tok):
            run.append((tok, start))
        elif run and tok in _PARTICLES and i + 1 < len(tokens) and tokens[i + 1][0][0].isupper():
            run.append((tok, start))
        else:
            if run:
                runs.append(run)
            run = []
        # A possessive ends a name: "Ben's Bach" is two things.
        if run and _POSSESSIVE.search(sentence[start:start + len(tok) + 2]):
            runs.append(run)
            run = []
    if run:
        runs.append(run)
    return [(" ".join(t for t, _ in r).strip(), r[0][1]) for r in runs if r[-1][0] not in _PARTICLES]


def _entities(paragraphs, min_count):
    """{canonical name: {aliases}} for names that recur and are rarely lowercase."""
    caps, lower = collections.Counter(), collections.Counter()
    for p in paragraphs:
        for s in _sentences(p):
            for name, _ in _name_runs(s):
                caps[name] += 1
            for w in _WORD.findall(s):
                if w[0].islower():
                    lower[_POSSESSIVE.sub("", w)] += 1
    names = {n for n, c in caps.items()
             if c >= min_count and all(lower[w.lower()] < c for w in n.split() if w not in _PARTICLES)}
    canon = {}
    for n in sorted(names, key=lambda x: -len(x.split())):
        # "Mara" joins "Mara Quell", "Van Holt" joins "Ida Van Holt": part of a longer name is an alias.
        home = next((m for m in canon if f" {n} " in f" {m} "), None)
        canon.setdefault(home or n, set()).add(n)
    return canon, caps


def _mentions(text, aliases):
    return any(re.search(rf"\b{re.escape(a)}\b", text) for a in aliases)


def _rank(sentence):
    """Sort key for evidence: sentences that say something (6+ words) first, then shortest."""
    return (len(sentence.split()) < 6, len(sentence))


def _shortest(sentences, limit=200):
    fitting = [s for s in sentences if len(s) <= limit]
    return min(fitting, key=_rank) if fitting else None


def build_graph(text, subject, top=35, min_count=3):
    """A digest dict (same schema as save_prima_digest) for `subject` in `text`."""
    paragraphs = _paragraphs(text)
    canon, caps = _entities(paragraphs, min_count)
    subject_tokens = [w for w in subject.split() if len(w) >= 3]
    subject_aliases = set(subject_tokens) | {subject}
    subject_key = next((k for k, als in canon.items() if als & subject_aliases), subject)
    subject_aliases |= canon.pop(subject_key, set())

    # Pronouns are invisible here, so count the paragraphs either side of a
    # mention as the subject's scene: "he" usually still means the subject.
    named = {i for i, p in enumerate(paragraphs) if _mentions(p, subject_aliases)}
    own = [p for i, p in enumerate(paragraphs) if named & {i - 1, i, i + 1}]
    weight, evidence, place_votes = collections.Counter(), {}, collections.Counter()
    for p in own:
        sentences = _sentences(p)
        for key, als in canon.items():
            if not _mentions(p, als):
                continue
            weight[key] += 1
            both = [s for s in sentences if _mentions(s, als) and _mentions(s, subject_aliases)]
            cand = _shortest(both) or _shortest([s for s in sentences if _mentions(s, als)])
            if cand and (key not in evidence or _rank(cand) < _rank(evidence[key])):
                evidence[key] = cand
            for s in sentences:
                for a in als:
                    for m in re.finditer(rf"\b{re.escape(a)}\b", s):
                        place_votes[key, bool(_PLACE_PREP.search(s[:m.start()]))] += 1

    sid = "subject"
    nodes = [{"id": sid, "type": "person", "label": subject,
              "summary": f"Named in {len(named)} of {len(paragraphs)} paragraphs; "
                         f"{len(own)} counted as their scene with neighbours "
                         f"(aliases: {', '.join(sorted(subject_aliases))}).",
              "evidence": []}]
    edges = []
    for key, n in weight.most_common(top):
        nid = re.sub(r"\W+", "_", key.lower()).strip("_")
        # Places are mostly reached by a preposition ("on Gull Point"); a third is enough.
        is_place = 3 * place_votes[key, True] >= place_votes[key, True] + place_votes[key, False]
        nodes.append({"id": nid, "type": "place" if is_place else "person_or_thing",
                      "label": key, "summary": f"{sum(caps[a] for a in canon[key])} mentions; shares {n} paragraphs "
                      f"with {subject}. Type is a guess from prepositions.",
                      "evidence": [{"quote": evidence[key]}] if key in evidence else []})
        edges.append({"from": sid, "to": nid, "relation": f"co-occurs in {n} paragraphs",
                      "evidence": [{"quote": evidence[key]}] if key in evidence else []})

    own_text = " ".join(own).lower()
    for theme, words in THEMES.items():
        hits = sum(len(re.findall(rf"\b{w}\b", own_text)) for w in words)
        if not hits:
            continue
        sents = [s for p in own for s in _sentences(p) if re.search(rf"\b({'|'.join(words)})\b", s, re.I)]
        q = _shortest(sents)
        nodes.append({"id": f"theme_{theme}", "type": "theme", "label": theme,
                      "summary": f"{hits} lexicon hits in {subject}'s paragraphs.",
                      "evidence": [{"quote": q}] if q else []})
        edges.append({"from": sid, "to": f"theme_{theme}", "relation": f"{hits} hits",
                      "evidence": [{"quote": q}] if q else []})

    voice = []
    for p in own:
        for s in _sentences(p):
            if _SPEECH.search(s) and _mentions(s, subject_aliases):
                voice += [q.strip() for q in _QUOTE.findall(s)]
    voice = list(dict.fromkeys(voice))[:10]

    return {"subject": subject, "method": "python rule-based extraction (core/extract.py)",
            "nodes": nodes, "edges": edges,
            "voice_samples": [{"quote": q} for q in voice]}


def _self_test():
    text = ("Mara Quell climbed Gull Point at dusk. He was afraid.\n\n"
            "Mara looked at Gull Point again. Ben said, “Come down and hear the Bach tonight, please.” "
            "Mara said, “I will come down after I finish this chapter, Ben.”\n\n"
            "Ben practised alone. Mara thought of Ben and of Gull Point.\n\n"
            "Otto met Rosa. They talked about Otto's patients.")
    g = build_graph(text, "Mara Quell", min_count=2)
    labels = {n["label"] for n in g["nodes"]}
    assert {"Gull Point", "Ben"} <= labels, labels
    assert "Otto" not in labels, labels  # never shares a paragraph with Mara
    assert any(n["label"] == "fear" for n in g["nodes"]), labels
    assert g["voice_samples"], g
    place = next(n for n in g["nodes"] if n["label"] == "Gull Point")
    assert place["evidence"], place
    return True


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) == 3:
        print(json.dumps(build_graph(open(sys.argv[1], encoding="utf-8").read(), sys.argv[2]),
                         indent=2, ensure_ascii=False))
    else:
        print("extract self-test:", _self_test())
