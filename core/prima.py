"""Prima Materia: reading a source folder, saving digests, the word network.

Shared by the MCP tools in base_server.py and by scripts/studio.py, so both
read a folder, check digest quotes and query the network the same way.
"""

import collections
import json
import logging
import os

from digest import check_quotes
from naming import sanitize_heteronym_name
from network import build_network, connections, load_tsv, overview, write_tsv
from sources import SUPPORTED, read_source

logger = logging.getLogger("g_mpf_bridge")

# Caps on the Prima Materia folder, so a mis-set PESSOA_ANALYSIS_DIR (a home
# folder, a downloads dump) is refused file by file instead of flooding a chat.
MAX_SOURCES = int(os.environ.get("PESSOA_MAX_SOURCES", "200"))
MAX_SOURCE_MB = float(os.environ.get("PESSOA_MAX_SOURCE_MB", "50"))

NETWORK_FILES = ("prima_network_nodes.tsv", "prima_network_edges.tsv")


def read_sources(analysis_dir, max_sources=None, max_mb=None):
    """Read every supported file (.md .txt .docx .pdf) under analysis_dir.

    Returns ([(relative path, text or None, error or None)], [skipped paths]).
    Hidden files and saved digests are not sources, so they are not "skipped".
    """
    max_sources = MAX_SOURCES if max_sources is None else max_sources
    max_mb = MAX_SOURCE_MB if max_mb is None else max_mb
    sources, skipped = [], []
    for root, dirs, files in os.walk(analysis_dir):
        for file in sorted(files):
            rel_path = os.path.relpath(os.path.join(root, file), analysis_dir)
            if file.startswith(".") or file.endswith(".digest.json") or file in NETWORK_FILES:
                continue
            if not file.lower().endswith(SUPPORTED):
                skipped.append(rel_path)
                continue
            if len(sources) >= max_sources:
                skipped.append(f"{rel_path} (over the {max_sources}-file cap, PESSOA_MAX_SOURCES)")
                continue
            path = os.path.join(root, file)
            try:
                mb = os.path.getsize(path) / 1e6
                if mb > max_mb:
                    raise ValueError(f"{mb:.0f} MB is over the {max_mb:g} MB cap (PESSOA_MAX_SOURCE_MB)")
                text = read_source(path)
                logger.info(f"Prima Materia read: {rel_path} ({mb:.2f} MB, {len(text):,} chars)")
                sources.append((rel_path, text, None))
            except Exception as e:
                logger.info(f"Prima Materia not read: {rel_path}: {e}")
                sources.append((rel_path, None, str(e)))
    return sources, skipped


def digests(analysis_dir):
    return sorted(f for f in os.listdir(analysis_dir) if f.endswith(".digest.json"))


def store_digest(analysis_dir, parsed, problems, suffix, overwrite, source_text):
    """Write a digest next to the sources after checking its quotes; return the report."""
    path = os.path.join(analysis_dir, sanitize_heteronym_name(parsed["subject"]) + suffix)
    if os.path.exists(path) and not overwrite:
        return (f"ERROR: {os.path.basename(path)} already exists. "
                "Pass overwrite=True to replace it.")

    verified, missing = check_quotes(parsed, source_text)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(parsed, f, indent=2, ensure_ascii=False)

    total = verified + len(missing)
    lines = [f"SAVED: {path}",
             f"nodes: {len(parsed['nodes'])}, edges: {len(parsed['edges'])}, "
             f"quotes verified against the source: {verified}/{total}"]
    lines += [f"WARNING: {p}" for p in problems]
    if missing:
        lines.append("Quotes NOT found in the source (fix or remove, then overwrite):")
        lines += [f"- {where}: {q!r}" for where, q in missing[:15]]
        if len(missing) > 15:
            lines.append(f"- ...and {len(missing) - 15} more")
    return "\n".join(lines)


def network_report(analysis_dir, sources, unit):
    """Build and save the word network of sources [(path, text)]; return the report."""
    counts, display, links, where, used = build_network(sources, unit)
    nodes_path, edges_path = (os.path.join(analysis_dir, f) for f in NETWORK_FILES)
    write_tsv(nodes_path, edges_path, counts, display, links, where)
    degree = collections.Counter()
    for (a, b), w in links.items():
        degree[a] += w
        degree[b] += w
    return "\n".join([
        f"SAVED: {nodes_path}", f"SAVED: {edges_path}",
        f"unit: {used} | sources: {len(sources)} | words: {len(counts)} | links: {len(links)}",
        "most connected: " + ", ".join(f"{display[w]} ({n})" for w, n in degree.most_common(20)),
        "Ask how any word connects to the rest with query_prima_network(node).",
    ])


def query_report(analysis_dir, node, target="", top=25):
    """How one node connects to the rest of the saved network, as text."""
    nodes_path, edges_path = (os.path.join(analysis_dir, f) for f in NETWORK_FILES)
    if not os.path.exists(nodes_path):
        return "ERROR: no network yet. Run build_prima_network() first."
    r = connections(*load_tsv(nodes_path, edges_path), node, top=max(1, min(top, 200)),
                    target=target or None)
    if r is None:
        return f"ERROR: no node {node!r} (stop words are not nodes; check the spelling)."
    lines = [f"NODE: {r['node']} | used {r['count']}x | {r['neighbours']} direct links | "
             f"total link weight {r['link_weight_total']} (rank {r['rank_by_link_weight']})",
             "strongest links (shared units): " + ", ".join(f"{l} ({w})" for l, w in r["top_neighbours"]),
             "most distinctive links (meet far more than chance, 2+ shared): "
             + (", ".join(f"{l} ({w})" for l, w in r["distinctive_neighbours"]) or "none"),
             "reach: " + ", ".join(f"{n} nodes at {h} link{'s' if h > 1 else ''}"
                                   for h, n in r["nodes_at_hop"].items())
             + f"; unreachable: {r['unreachable']}"]
    if target:
        p = r["path"]
        lines.append(f"path to {target}: " + (" -> ".join(p) + f"  (link weights {r['path_weights']})"
                                              if isinstance(p, list) else p))
    return "\n".join(lines)


def overview_report(analysis_dir, top=40):
    """The whole network's shape as text, with no word chosen (see network.overview)."""
    nodes_path, edges_path = (os.path.join(analysis_dir, f) for f in NETWORK_FILES)
    if not os.path.exists(nodes_path):
        return "ERROR: no network yet. Run build_prima_network() first."
    o = overview(*load_tsv(nodes_path, edges_path), top=top)
    pairs = lambda ps: ", ".join(f"{a} + {b} ({w})" for a, b, w in ps) or "none"
    return "\n".join([
        f"WORD NETWORK OVERVIEW: {o['words']} words, {o['links']} links. A word is a node; two words are "
        "linked when they share a sentence (or a verse line); the number is how many they share.",
        "Most connected words (times used, total link weight): "
        + ", ".join(f"{w} ({n}, {lw})" for w, n, lw in o["most_connected"]),
        "Names (capitalised mid-sentence: people, places): " + (", ".join(f"{w} ({n})" for w, n in o["names"]) or "none"),
        "Strongest pairs: " + pairs(o["strongest_pairs"]),
        "Most distinctive pairs (meet far more often than chance, 3+ shared): " + pairs(o["distinctive_pairs"]),
        "Theme clusters (a central word: its most distinctive neighbours):",
        *[f"- {hub}: {', '.join(nb) or '(no strong links)'}" for hub, nb in o["clusters"]],
    ])
