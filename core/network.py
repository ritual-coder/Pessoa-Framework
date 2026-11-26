"""Word co-occurrence network of a Prima Materia source, and queries on it.

The method, for any text:

- Nodes: every distinct word except stop words, lower-cased. A word keeps its
  capital only if it is capitalised mid-sentence (a name, a place) and never
  written in lower case. A trailing 's is dropped (Ben's -> Ben).
- Links: two words are linked when they share a unit; weight = shared units.
  The unit is the line for verse and the sentence for prose,
  where one "line" of a file is often a whole paragraph.

Output is a TSV pair: nodes (id, label, count, source) and edges (a, b,
weight), easy to load into any graph or visualisation tool.
"""

import collections
import heapq
import itertools
import re
import statistics

# English stop words and contractions.
STOP = set("""
a about above after again against all also am an and any are aren't as at be because been before being
below between both but by can can't could couldn't did didn't do does doesn't doing don't down during
each even ever few for from further had hadn't has hasn't have haven't having he he'd he'll he's her here
here's hers herself him himself his how how's i i'd i'll i'm i've if in into is isn't it it's its itself
just let's me more most mustn't my myself no nor not now of off on once only or other ought our ours
ourselves out over own same shan't she she'd she'll she's should shouldn't so some such than that that's
the their theirs them themselves then there there's these they they'd they'll they're they've this those
through to too under until up upon very was wasn't we we'd we'll we're we've were weren't what what's when
when's where where's which while who who's whom why why's will with won't would wouldn't yes you you'd
you'll you're you've your yours yourself yourselves us one oh still yet must may might shall
said st mr mrs ms dr
""".split())

WORD = re.compile(r"[^\W\d_]+(?:[-'][^\W\d_]+)*")
_SENTENCE_END = '.!?"“”(…:'
# An abbreviation's full stop does not end a sentence: "St. James", "Mr. Cap".
_ABBREV = ("St.", "Mr.", "Mrs.", "Ms.", "Dr.")
# Section numbers and dividers are not text: "12", "XIV", "* * *".
_HEADING = re.compile(r"^(\d+|[IVXLC]+|[*….\s]+)$")


def _units(text, unit):
    lines = [l.strip() for l in text.replace("’", "'").splitlines()]
    lines = [l for l in lines if l and not l.startswith("#") and not _HEADING.match(l)]
    if unit == "auto":
        # Verse lines are short; prose "lines" are paragraphs.
        unit = "line" if lines and statistics.median(map(len, lines)) < 80 else "sentence"
    if unit == "line":
        return lines, unit
    # Split after . ! ? -- but not after an abbreviation like "Mr.".
    split = re.compile(r"(?<!\bMr\.)(?<!\bMs\.)(?<!\bDr\.)(?<!\bSt\.)(?<!\bMrs\.)(?<=[.!?])[\"”)]?\s+")
    return [s for l in lines for s in split.split(l) if s], unit


def build_network(sources, unit="auto"):
    """sources: [(name, text)]. Returns (counts, display, links, where, unit_used)."""
    counts, display, links, where = collections.Counter(), {}, collections.Counter(), {}
    lower_seen, name_form = set(), {}
    used = set()
    for source_name, text in sources:
        units, u = _units(text, unit)
        used.add(u)
        for line in units:
            words = []
            for m in WORD.finditer(line):
                raw = m.group()
                if raw.lower().endswith("'s"):
                    raw = raw[:-2]
                w = raw.lower()
                if not w or w in STOP or len(w) < 2:
                    continue
                counts[w] += 1
                where.setdefault(w, set()).add(source_name)
                before = line[:m.start()].rstrip()
                mid = bool(before) and (before[-1] not in _SENTENCE_END or before.endswith(_ABBREV))
                if raw.islower():
                    lower_seen.add(w)
                elif mid:
                    name_form.setdefault(w, raw)  # first mid-sentence spelling: "McKay", not "Mckay"
                words.append(w)
            for a, b in itertools.combinations(sorted(set(words)), 2):
                links[a, b] += 1
    for w in counts:
        display[w] = name_form[w] if (w in name_form and w not in lower_seen) else w
    return counts, display, links, where, "+".join(sorted(used))


def write_tsv(nodes_path, edges_path, counts, display, links, where):
    ids = {w: f"w{i}" for i, w in enumerate(sorted(counts))}
    with open(nodes_path, "w", encoding="utf-8") as f:
        f.write("id\tlabel\tcount\tsource\n")
        for w in sorted(counts):
            src = next(iter(where[w])) if len(where[w]) == 1 else "multiple"
            f.write(f"{ids[w]}\t{display[w]}\t{counts[w]}\t{src}\n")
    with open(edges_path, "w", encoding="utf-8") as f:
        f.write("a\tb\tweight\n")
        for (a, b), n in sorted(links.items()):
            f.write(f"{ids[a]}\t{ids[b]}\t{n}\n")


def load_tsv(nodes_path, edges_path):
    """Returns (labels {id: label}, counts {id: n}, adjacency {id: {id: weight}})."""
    labels, counts = {}, {}
    with open(nodes_path, encoding="utf-8") as f:
        next(f)
        for row in f:
            nid, label, count, _ = row.rstrip("\n").split("\t")
            labels[nid], counts[nid] = label, int(count)
    adj = collections.defaultdict(dict)
    with open(edges_path, encoding="utf-8") as f:
        next(f)
        for row in f:
            a, b, w = row.rstrip("\n").split("\t")
            adj[a][b] = adj[b][a] = int(w)
    return labels, counts, adj


def connections(labels, counts, adj, word, top=25, target=None):
    """How `word` connects to every other node. Returns a dict, or None if absent.

    Direct neighbours by weight; hop distance (fewest links) to all other
    nodes; and, for `target`, the strongest of the shortest paths.
    """
    by_label = {l.lower(): i for i, l in labels.items()}
    src = by_label.get(word.lower())
    if src is None:
        return None
    nbrs = adj.get(src, {})
    weighted = {i: sum(adj.get(i, {}).values()) for i in labels}
    total = sum(counts.values())
    rank = 1 + sum(1 for i in labels if weighted[i] > weighted[src])

    hops, frontier = {src: 0}, [src]
    while frontier:
        nxt = []
        for a in frontier:
            for b in adj.get(a, {}):
                if b not in hops:
                    hops[b] = hops[a] + 1
                    nxt.append(b)
        frontier = nxt
    dist = collections.Counter(h for i, h in hops.items() if i != src)

    result = {
        "node": labels[src], "count": counts[src], "neighbours": len(nbrs),
        "link_weight_total": weighted[src], "rank_by_link_weight": f"{rank} of {len(labels)}",
        "top_neighbours": [(labels[b], w) for b, w in sorted(nbrs.items(), key=lambda x: -x[1])[:top]],
        # Raw weight favours words that are everywhere ("like", "little"). Lift
        # asks how much more often two words meet than their frequencies predict.
        "distinctive_neighbours": [
            (labels[b], w) for b, w in sorted(
                ((b, w) for b, w in nbrs.items() if w >= 2),
                key=lambda x: -x[1] * total / (counts[src] * counts[x[0]]))[:top]],
        "nodes_at_hop": dict(sorted(dist.items())),
        "unreachable": len(labels) - len(hops),
    }
    if target:
        dst = by_label.get(target.lower())
        if dst is None:
            result["path"] = f"no node {target!r}"
        elif dst not in hops:
            result["path"] = "not connected"
        else:
            # Among paths of the fewest hops, the one whose weakest link is strongest:
            # walk the BFS layers keeping, per node, its best predecessor.
            strength, prev = {src: float("inf")}, {}
            for node in sorted((i for i in hops if 0 < hops[i] <= hops[dst]), key=hops.get):
                for p, w in adj[node].items():
                    if hops.get(p) == hops[node] - 1 and p in strength:
                        cand = min(strength[p], w)
                        if cand > strength.get(node, -1):
                            strength[node], prev[node] = cand, p
            path, node = [dst], dst
            while node != src:
                node = prev[node]
                path.append(node)
            path.reverse()
            result["path"] = [labels[i] for i in path] if path else "not connected"
            if path:
                result["path_weights"] = [adj[a][b] for a, b in zip(path, path[1:])]
    return result


def overview(labels, counts, adj, top=40, hubs=8, per_hub=8):
    """The shape of the whole network, with no word chosen: what a model can build a character from.

    Returns a dict: the most connected words, the names (labels kept capitalised:
    people and places), the strongest and the most distinctive word pairs, and
    for the central words their most distinctive neighbours (a theme cluster).
    Everything is sorted with ties broken by label, so the same text always
    gives the same overview.
    """
    total = sum(counts.values()) or 1
    weighted = {i: sum(adj.get(i, {}).values()) for i in labels}
    by_weight = sorted(labels, key=lambda i: (-weighted[i], labels[i]))
    edges = {(a, b): w for a, nb in adj.items() for b, w in nb.items() if a < b}
    lift = lambda a, b, w: w * total / (counts[a] * counts[b])  # meets more often than chance predicts
    strongest = sorted(edges.items(), key=lambda x: (-x[1], labels[x[0][0]], labels[x[0][1]]))[:top]
    distinctive = sorted(((k, w) for k, w in edges.items() if w >= 3),
                         key=lambda x: (-lift(*x[0], x[1]), labels[x[0][0]], labels[x[0][1]]))[:top]
    clusters = []
    for h in by_weight[:hubs]:
        nb = [(b, w) for b, w in adj.get(h, {}).items() if w >= 2]
        nb.sort(key=lambda x: (-lift(h, *x), labels[x[0]]))
        clusters.append((labels[h], [labels[b] for b, _ in nb[:per_hub]]))
    names = sorted((i for i in labels if labels[i][:1].isupper()), key=lambda i: (-counts[i], labels[i]))[:top]
    return {
        "words": len(labels), "links": len(edges),
        "most_connected": [(labels[i], counts[i], weighted[i]) for i in by_weight[:top]],
        "names": [(labels[i], counts[i]) for i in names],
        "strongest_pairs": [(labels[a], labels[b], w) for (a, b), w in strongest],
        "distinctive_pairs": [(labels[a], labels[b], w) for (a, b), w in distinctive],
        "clusters": clusters,
    }


def _self_test():
    import os
    import tempfile
    verse = "Mara watched the tower\nthe tower watched Mara back\nBen played Bach\nBach and the tower\n"
    counts, display, links, where, unit = build_network([("v.txt", verse)])
    assert unit == "line", unit
    assert not STOP & counts.keys()
    assert display["mara"] == "Mara" and display["bach"] == "Bach"  # capitalised mid-line
    assert display["ben"] == "ben"  # only ever line-initial, so the capital proves nothing
    assert links["mara", "tower"] == 2 and links["bach", "ben"] == 1
    with tempfile.TemporaryDirectory() as d:
        n, e = os.path.join(d, "n.tsv"), os.path.join(d, "e.tsv")
        write_tsv(n, e, counts, display, links, where)
        r = connections(*load_tsv(n, e), "tower", target="ben")
    assert r["neighbours"] == 4 and r["unreachable"] == 0, r
    assert [l for l, _ in r["distinctive_neighbours"]] == ["Mara", "watched"], r
    assert r["path"] == ["tower", "Bach", "ben"] and r["path_weights"] == [1, 1], r
    # Every path must be a real chain of links and exactly as short as the BFS distance,
    # on a graph dense enough that stronger but longer detours exist.
    import random
    rng = random.Random(7)
    words = [f"node{chr(97 + i // 26)}{chr(97 + i % 26)}" for i in range(40)]  # letters only: digits are not words
    lines = "\n".join(" ".join(rng.sample(words, 3)) for _ in range(90))
    c2, d2, l2, wh2, _ = build_network([("r.txt", lines)], unit="line")
    with tempfile.TemporaryDirectory() as d:
        n, e = os.path.join(d, "n.tsv"), os.path.join(d, "e.tsv")
        write_tsv(n, e, c2, d2, l2, wh2)
        labels, cnt, adj = load_tsv(n, e)
    ids = {v: k for k, v in labels.items()}
    dist, layer = {ids[words[0]]: 0}, [ids[words[0]]]  # independent BFS for the true distances
    while layer:
        nxt = []
        for a in layer:
            for b in adj[a]:
                if b not in dist:
                    dist[b] = dist[a] + 1
                    nxt.append(b)
        layer = nxt
    for t in words[1:]:
        p = connections(labels, cnt, adj, words[0], target=t)["path"]
        assert isinstance(p, list) and p[0] == words[0] and p[-1] == t, (t, p)
        assert all(ids[b] in adj[ids[a]] for a, b in zip(p, p[1:])), (t, p)
        assert len(p) - 1 == dist[ids[t]], (t, p, dist[ids[t]])
    prose = ("Mara Quell looked at Gull Point through the binoculars, and it seemed to him that "
             "the brown hill was moving very slowly in the light of the late afternoon sun.")
    assert build_network([("p.md", prose)])[4] == "sentence"

    text = ("At night Mara Quell feared the harbour.\nThe harbour lights burned at night.\n"
            "By the harbour Tomas Ferro mended nets.\nAt night Mara Quell kept the lamp lit.\n") * 3
    c3, d3, l3, w3, _ = build_network([("t.txt", text)], unit="line")
    with tempfile.TemporaryDirectory() as d:
        n, e = os.path.join(d, "n.tsv"), os.path.join(d, "e.tsv")
        write_tsv(n, e, c3, d3, l3, w3)
        ov = overview(*load_tsv(n, e), top=5)
        assert ov == overview(*load_tsv(n, e), top=5), "overview is not deterministic"
    assert ov["most_connected"][0][0] in ("harbour", "night"), ov["most_connected"]
    assert ("Quell", 6) in ov["names"] and ("Mara", 6) in ov["names"], ov["names"]
    assert all(n[:1].isupper() for n, _ in ov["names"])
    assert len(ov["strongest_pairs"]) == 5 and ov["clusters"][0][1], ov
    return True


if __name__ == "__main__":
    print("network self-test:", _self_test())
