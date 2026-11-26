"""Pessoa Studio: a local web page that guides heteronym creation.

    python scripts/studio.py            # opens the page in your browser

The page has no model of its own. It collects the vision, the Prima Materia
(notes, plus any files picked from the computer or from folders, read by one of
the three methods), the
blueprints and the Big Five targets, builds one self-contained EVE prompt to
paste into any Claude chat, then takes the Hydration Blob back, previews it (a
dry run with the validator) and saves it through hydrate_files(), like
scripts/hydrate.py.

The three Prima Materia methods are those of the MCP tools, through the same
code (core/prima.py):
- full text   every readable source file, verbatim;
- AI digest   a saved <Subject>.digest.json, or one Claude writes from a prompt
              the Studio builds, checked quote by quote when it is saved;
- Python graph  extract.build_graph, saved as <Subject>.auto.digest.json.
The word network (build, then query a node or a path) is the optional explore step.

Chosen files are copied into a source set, prima/<set>/ (gitignored), and every
method reads and writes there: the user's own folders are only ever read.

Listens on 127.0.0.1 only. Every request that reads or writes needs the token
embedded in the page, so another website open in the browser cannot reach it.
"""

import argparse
import base64
import contextlib
import http.server
import io
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import webbrowser
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(ROOT, "core"))
sys.path.insert(0, SCRIPT_DIR)

from converter import PersonalityConverter  # noqa: E402
from digest import parse_digest, unfence  # noqa: E402
from extract import build_graph  # noqa: E402
from hydrate import hydrate_files, parse_hydration_blob  # noqa: E402
from naming import sanitize_heteronym_name  # noqa: E402
from validate import derive_summary  # noqa: E402
from sources import SUPPORTED, read_source  # noqa: E402
import prima  # noqa: E402
import seeds  # noqa: E402

TEMPLATES = os.path.join(ROOT, "templates")
PAGE = os.path.join(SCRIPT_DIR, "studio.html")
LOGO = os.path.join(ROOT, "pessoa_framework_logo.png")
TRAITS = ("openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism")
MAX_BODY = 80 * 1024 * 1024  # uploaded files travel as base64 (4/3 of their size)
FULL_TEXT_ADVICE = 60_000    # tokens; above this the wizard recommends a digest or the graph
# shortcut: one fixed ceiling for "fits in one chat" (Claude's 200k context minus the prompt and the
# answer); raise it for a client with a larger context window.
FULL_TEXT_LIMIT = 150_000
SETS_DIR = os.path.join(ROOT, "prima")  # gitignored: copies of the user's source files

# Read into the prompt so it works in a chat without the Pessoa tools.
PROMPT_TEMPLATES = ("skin.md", "engine.md", "big_five.json", "seed_guidance_protocol.md",
                    "operational_rules.md")

DESKTOP_CONFIGS = (
    "~/Library/Application Support/Claude/claude_desktop_config.json",  # macOS
    os.path.join(os.environ.get("APPDATA", "~"), "Claude", "claude_desktop_config.json"),  # Windows
    "~/.config/Claude/claude_desktop_config.json",  # Linux
)

METHOD_NOTES = {
    "full": "the full source text, verbatim. Most faithful; anything not in it is your inference, so say so.",
    "digest": "an AI-written digest (a semantic graph with verified short quotes). It is lossy: what it "
              "left out is gone, so do not invent details it does not support.",
    "graph": "an automatic graph built by Python rules. Every quote is verbatim, but it is shallow: names "
             "found by capitalisation, relations are only co-occurrence counts, node types are guesses. "
             "Treat it as a map of who and where; the voice and psychology are yours to infer, so say "
             "where you are inferring.",
    "network": "an overview of the source's word network, built by Python rules with no model and no chosen "
               "person: which words are central, which names recur, which words meet far more often than "
               "chance, and the theme clusters around the central words. It shows the shape of the text, not "
               "its sentences: build the character from that shape (its world, preoccupations, images and "
               "tone), and say where you are inferring.",
}


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def characters():
    d = os.path.join(ROOT, "characters")
    return sorted(n for n in os.listdir(d) if os.path.isdir(os.path.join(d, n))) if os.path.isdir(d) else []


def default_folder():
    """(folder, where it came from): PESSOA_ANALYSIS_DIR, else Claude Desktop's entry for this server."""
    if os.environ.get("PESSOA_ANALYSIS_DIR"):
        return os.path.expanduser(os.environ["PESSOA_ANALYSIS_DIR"]), "PESSOA_ANALYSIS_DIR"
    for cfg_path in DESKTOP_CONFIGS:
        try:
            with open(os.path.expanduser(cfg_path), encoding="utf-8") as f:
                servers = json.load(f).get("mcpServers", {})
        except (OSError, ValueError):
            continue
        for entry in servers.values():
            launch = " ".join([str(entry.get("command", ""))] + [str(a) for a in entry.get("args", [])])
            folder = (entry.get("env") or {}).get("PESSOA_ANALYSIS_DIR")
            if folder and ("base_server.py" in launch or "START_PESSOA" in launch):
                return os.path.expanduser(folder), "Claude Desktop config"
    return "", ""


def _folder(path):
    path = os.path.expanduser((path or "").strip())
    if not path:
        raise ValueError("enter a folder path first")
    if not os.path.isdir(path):
        raise ValueError(f"not an existing folder: {path}")
    return path


def _texts(folder):
    """[(relative path, text)] of every readable source, and [(path, error)] of the unreadable."""
    sources, _ = prima.read_sources(folder)
    return [(p, t) for p, t, e in sources if t], [(p, e) for p, t, e in sources if e]


# --- Source sets: the files picked for one character, copied into prima/<set>/ ---------
# Files picked in a browser have no folder the Studio could write next to, and a
# user's own folders should never receive digests or networks. So every chosen
# file is copied into a set under the framework (gitignored: it holds the user's
# source text), and all methods read and write there.

def _set_dir(name):
    name = (name or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
        raise ValueError(f"not a valid source set name: {name!r}")
    path = os.path.join(SETS_DIR, name)
    if not os.path.isdir(path):
        raise ValueError(f"no source set named {name!r}")
    return path


def new_set():
    os.makedirs(SETS_DIR, exist_ok=True)
    base = "set-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    name, n = base, 2
    while os.path.exists(os.path.join(SETS_DIR, name)):
        name, n = f"{base}-{n}", n + 1
    os.makedirs(os.path.join(SETS_DIR, name))
    return name


def list_sets():
    if not os.path.isdir(SETS_DIR):
        return []
    return sorted((d for d in os.listdir(SETS_DIR) if os.path.isdir(os.path.join(SETS_DIR, d))), reverse=True)


def _unique(folder, filename):
    base, ext = os.path.splitext(filename)
    path, n = os.path.join(folder, filename), 2
    while os.path.exists(path):
        path, n = os.path.join(folder, f"{base} ({n}){ext}"), n + 1
    return path


def _files_changed(folder):
    # The word network is derived from the files: drop it so it is never read stale.
    for f in prima.NETWORK_FILES:
        with contextlib.suppress(FileNotFoundError):
            os.remove(os.path.join(folder, f))


def _check_new_file(name, size):
    if not name.lower().endswith(SUPPORTED):
        raise ValueError(f"unsupported type; use {', '.join(SUPPORTED)}")
    if size / 1e6 > prima.MAX_SOURCE_MB:
        raise ValueError(f"over the {prima.MAX_SOURCE_MB:g} MB cap (PESSOA_MAX_SOURCE_MB)")


def _keep_if_readable(path):
    """A file that yields no text (a scanned PDF, a broken .docx) is removed again, with the reason."""
    try:
        read_source(path)
    except Exception:
        os.remove(path)
        raise


def add_uploads(set_name, uploads):
    """Save files picked in the browser ([{name, data: base64}]) into the set."""
    folder, added, errors = _set_dir(set_name), [], []
    for u in uploads:
        name = os.path.basename(str(u.get("name", "")).replace("\\", "/"))
        try:
            data = base64.b64decode(u.get("data", ""))
            _check_new_file(name, len(data))
            path = _unique(folder, name)
            with open(path, "wb") as f:
                f.write(data)
            _keep_if_readable(path)
            added.append(os.path.basename(path))
        except Exception as e:  # reported per file on the page
            errors.append({"name": name, "error": str(e)})
    if added:
        _files_changed(folder)
    return {"added": added, "errors": errors, **scan_set(set_name)}


def add_paths(set_name, paths):
    """Copy files chosen from a browsed folder (absolute paths) into the set."""
    folder, added, errors = _set_dir(set_name), [], []
    for p in paths:
        p = os.path.expanduser(str(p))
        name = os.path.basename(p)
        try:
            if not os.path.isfile(p):
                raise ValueError("no such file")
            _check_new_file(name, os.path.getsize(p))
            path = _unique(folder, name)
            shutil.copyfile(p, path)
            _keep_if_readable(path)
            added.append(os.path.basename(path))
        except Exception as e:  # reported per file on the page
            errors.append({"name": name, "error": str(e)})
    if added:
        _files_changed(folder)
    return {"added": added, "errors": errors, **scan_set(set_name)}


def remove_file(set_name, rel):
    folder = _set_dir(set_name)
    listed = [p for p, _, _ in prima.read_sources(folder)[0]]
    if rel not in listed:  # names from the listing only: no path can leave the set
        raise ValueError(f"{rel!r} is not a file of this set")
    os.remove(os.path.join(folder, rel))
    _files_changed(folder)
    return scan_set(set_name)


def browse(folder):
    """The supported files of any folder (read only), so some of them can be added to a set."""
    folder = _folder(folder)
    sources, skipped = prima.read_sources(folder)
    return {"folder": folder, "skipped": skipped,
            "files": [{"path": os.path.join(folder, p), "rel": p, "tokens": len(t) // 4 if t else 0,
                       "error": e} for p, t, e in sources]}


def scan_set(set_name):
    folder = _set_dir(set_name)
    sources, _ = prima.read_sources(folder)
    found = []
    for name in prima.digests(folder):
        try:
            d = json.loads(_read(os.path.join(folder, name)))
            found.append({"name": name, "subject": d.get("subject", "?"), "nodes": len(d.get("nodes", [])),
                          "edges": len(d.get("edges", [])), "auto": name.endswith(".auto.digest.json")})
        except (OSError, ValueError) as e:
            found.append({"name": name, "error": str(e), "auto": name.endswith(".auto.digest.json")})
    files = [{"path": p, "tokens": len(t) // 4 if t else 0, "error": e} for p, t, e in sources]
    return {"set": set_name, "location": folder, "files": files, "digests": found,
            "tokens": sum(f["tokens"] for f in files), "advice_tokens": FULL_TEXT_ADVICE,
            "limit_tokens": FULL_TEXT_LIMIT,
            "network": os.path.exists(os.path.join(folder, prima.NETWORK_FILES[0]))}


def digest_prompt(set_name, subject):
    """A prompt for Claude to write <Subject>.digest.json from the full text."""
    if not subject.strip():
        raise ValueError("enter the subject (the character's name as the text spells it)")
    texts, _ = _texts(_set_dir(set_name))
    if not texts:
        raise ValueError("no readable source text in this set")
    full = "\n\n".join(f"### FILE: {p}\n{t}" for p, t in texts)
    if sum(len(t) for _, t in texts) // 4 > FULL_TEXT_LIMIT:
        raise ValueError(f"the set is over ~{FULL_TEXT_LIMIT:,} tokens, more than one Claude chat holds: "
                         "remove some files, or use the Python graph")
    return ("Write a Prima Materia digest of " + subject.strip() + " from the full source text below, "
            "following the template. Copy every quote exactly from the text: each one is checked against "
            "the source when the digest is saved, and quotes it does not contain are reported. Answer with "
            "the digest JSON only, in one code block, and do not call any tool to save it.\n\n"
            "===== TEMPLATE: prima_digest.json =====\n" + _read(os.path.join(TEMPLATES, "prima_digest.json"))
            + "\n\n===== FULL SOURCE TEXT =====\n" + full)


def save_digest(set_name, raw, overwrite=False):
    folder = _set_dir(set_name)
    raw = unfence(raw.strip())
    if "{" in raw and not raw.lstrip().startswith("{"):  # prose around the JSON: keep the outermost object
        raw = raw[raw.find("{"):raw.rfind("}") + 1]
    parsed, problems = parse_digest(raw)
    if parsed is None:
        raise ValueError("digest not saved: " + "; ".join(problems))
    source = "\n".join(t for _, t in _texts(folder)[0])
    return prima.store_digest(folder, parsed, problems, ".digest.json", overwrite, source)


def python_graph(set_name, subject, overwrite=False):
    folder = _set_dir(set_name)
    if not subject.strip():
        raise ValueError("enter the subject (the character's name as the text spells it)")
    source = "\n\n".join(t for _, t in _texts(folder)[0])
    if not source:
        raise ValueError("no readable source text in this set")
    graph = build_graph(source, subject.strip())
    if len(graph["nodes"]) <= 1:
        raise ValueError(f"found nothing linked to {subject!r}. Check the spelling: "
                         "the subject must appear in the text with a capital letter.")
    return prima.store_digest(folder, graph, [], ".auto.digest.json", overwrite, source)


def network(set_name, unit):
    if unit not in ("auto", "line", "sentence"):
        raise ValueError("unit must be auto, line or sentence")
    folder = _set_dir(set_name)
    texts, _ = _texts(folder)
    if not texts:
        raise ValueError("no readable source text in this set")
    return prima.network_report(folder, texts, unit)


def prima_section(p):
    """(placeholder text for EVE, prompt section) from the page's Prima Materia choices."""
    notes, method = (p.get("notes") or "").strip(), p.get("method") or "none"
    parts, used = [], []
    if notes:
        parts.append("===== PRIMA MATERIA: NOTES FROM THE USER =====\n" + notes)
        used.append("the user's notes")
    if method != "none":
        folder = _set_dir(p.get("set"))
        if method == "full":
            texts, bad = _texts(folder)
            if not texts:
                raise ValueError("no readable source text in this set")
            tokens = sum(len(t) for _, t in texts) // 4
            if tokens > FULL_TEXT_LIMIT:
                raise ValueError(f"the full text is ~{tokens:,} tokens, more than one Claude chat holds "
                                 f"(~{FULL_TEXT_LIMIT:,}): use the AI digest or the Python graph")
            body = "\n\n".join(f"### FILE: {path}\n{t}" for path, t in texts)
            if bad:
                body += "\n\n(Not readable, so not included: " + ", ".join(path for path, _ in bad) + ")"
            label = f"FULL TEXT ({len(texts)} files)"
        elif method == "network":
            if not os.path.exists(os.path.join(folder, prima.NETWORK_FILES[0])):
                texts, _ = _texts(folder)
                if not texts:
                    raise ValueError("no readable source text in this set")
                prima.network_report(folder, texts, p.get("unit") or "auto")
            body, label = prima.overview_report(folder), "WORD NETWORK OVERVIEW"
        elif method in ("digest", "graph"):
            name = p.get("digest") or ""
            if name not in prima.digests(folder):  # names from the listing only: no path can leave the set
                raise ValueError("pick a saved digest in step 2, or build one there first")
            body, label = _read(os.path.join(folder, name)), f"DIGEST {name}"
        else:
            raise ValueError(f"unknown method {method!r}")
        parts.append(f"===== PRIMA MATERIA: {label} =====\nThis is {METHOD_NOTES[method]}\n\n{body}")
        used.append(METHOD_NOTES[method].split(".")[0])
    findings = (p.get("findings") or "").strip()
    if findings:
        parts.append("===== WORD NETWORK FINDINGS (how the subject connects in the source) =====\n" + findings)
        used.append("word-network findings")
    placeholder = ("included below: " + "; ".join(used)) if used else "none: build from the vision only"
    return placeholder, parts


def parameters(scores):
    """Cabinet parameters for 1-5 trait targets, from the framework's own calculus."""
    return PersonalityConverter.analyze({"scale": "1-5", "summary": {t: float(scores[t]) for t in TRAITS}})["parameters"]


def source_text(p):
    """Everything the evidence quotes may come from: the notes and every readable file in the set."""
    parts = [(p.get("notes") or "").strip()]
    if (p.get("set") or "").strip():
        parts += [t for _, t in _texts(_set_dir(p["set"]))[0]]
    return "\n\n".join(x for x in parts if x)


def _personality_brief(scores):
    """The Big Five instruction: automatic scoring, or slider targets the facets steer toward."""
    common = ("Write big_five.json on the 1-5 scale with all 30 facets and the 'evidence' block: a one-line "
              "reason for every facet and, where the Prima Materia shows it, a short verbatim quote (or null). "
              "Each trait summary is recalculated from its six facets when the character is saved.")
    if not scores:
        return ("Personality: score it yourself from the description and the Prima Materia, facet by facet, "
                "on each facet's own evidence. There are no targets. " + common)
    targets = ", ".join(f"{t} {float(scores[t]):.1f}" for t in TRAITS)
    return (f"Personality: the user set Big Five targets on the 1-5 scale: {targets}. Still score each of the "
            "30 facets from the description and the Prima Materia, but keep each trait's facet mean within "
            "0.3 of its target. Where the source points elsewhere, follow the target and say so in that "
            "facet's reason. " + common)


def build_prompt(vision, blueprints, scores=None, prima_choice=None, name=""):
    if not vision.strip():
        raise ValueError("describe the character first (step 1)")
    seed = seeds.compose(blueprints)
    placeholder, prima_parts = prima_section(prima_choice or {})
    eve = (_read(os.path.join(TEMPLATES, "eve_master_prompt.md"))
           .replace("{USER_VISION_PROMPT}", vision.strip() + (f" Name: {name.strip()}." if name.strip() else ""))
           .replace("{PRIMA_MATERIA_TEXT_OR_DATA}", placeholder)
           .replace("{BLUEPRINT_TYPE}", " + ".join(blueprints) + " (composite below)"))
    parts = [
        "I am creating a Pessoa heteronym with the Pessoa Studio. Everything you need is in this "
        "message: follow the EVE protocol below and use the templates, blueprint and Prima Materia "
        "included. Do not call any Pessoa tool to fetch or save anything: it is all here.\n\n"
        + _personality_brief(scores) + "\n\n"
        "Do not ask me questions unless something essential is missing. Answer with the complete "
        "Hydration Blob in one code block (format in section 6), so I can paste it back into the "
        "Studio, which saves it.",
        "===== EVE MASTER PROMPT =====\n" + eve,
        "===== BLUEPRINT =====\n" + seed,
    ]
    parts += [f"===== TEMPLATE: {t} =====\n{_read(os.path.join(TEMPLATES, t))}" for t in PROMPT_TEMPLATES]
    return "\n\n".join(parts + prima_parts)


def extract_blob(text):
    """The blob part of a pasted answer: from the first file marker, without an outer code fence."""
    start = text.find("--- FILE:")
    if start < 0:
        raise ValueError("no '--- FILE: name ---' blocks found: paste Claude's whole answer")
    body = text[start:]
    if "```" in text[:start]:  # the blob sat inside a code block: drop its closing fence and what follows
        end = body.rfind("\n```")
        if end >= 0:
            body = body[:end]
    return body


def hydrate(blob, name, dry_run, prima_choice=None):
    """(report text, character folder, resulting 1-5 traits and parameters) via hydrate_files."""
    files = parse_hydration_blob(extract_blob(blob))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        char_dir = hydrate_files(files, name.strip() or None, dry_run=dry_run,
                                 source_text=source_text(prima_choice or {}) or None)
    result = {"traits": None, "parameters": None, "activation": ""}
    if files.get("big_five.json"):
        calculus = PersonalityConverter.analyze(derive_summary(json.loads(unfence(files["big_five.json"])))[0])
        result.update(traits={t: calculus["scores"][k] for t, k in zip(TRAITS, "OCEAN")},
                      parameters=calculus["parameters"])
    activation = os.path.join(char_dir, "ACTIVATION_PROMPT.md")
    if not dry_run and os.path.isfile(activation):
        result["activation"] = _read(activation)
    # A missing layer usually means the chat cut Claude's answer off; the page says how to recover.
    result["missing"] = [f for f in ("skin.md", "engine.md", "big_five.json", "seed.md", "operational_rules.md")
                         if f not in files]
    return out.getvalue().strip(), char_dir, result


def route(path, req):
    """One API call -> JSON-able dict. Raises on bad input; the handler reports it."""
    if path == "/api/info":
        folder, source = default_folder()
        return {"blueprints": seeds.available(), "characters": characters(),
                "folder": folder, "folder_source": source}
    if path == "/api/params":
        return {"parameters": parameters(req["scores"])}
    if path == "/api/name":
        raw = (req.get("name") or "").strip()
        folder = sanitize_heteronym_name(raw) if raw else ""
        return {"folder": folder, "taken": bool(folder) and folder in characters()}
    if path == "/api/sets":
        return {"sets": list_sets()}
    if path == "/api/set_new":
        return scan_set(new_set())
    if path == "/api/set":
        return scan_set(req.get("set"))
    if path == "/api/set_upload":
        return add_uploads(req.get("set"), req.get("files", []))
    if path == "/api/set_add_paths":
        return add_paths(req.get("set"), req.get("paths", []))
    if path == "/api/set_remove":
        return remove_file(req.get("set"), req.get("file", ""))
    if path == "/api/browse":
        return browse(req.get("folder"))
    if path == "/api/digest_prompt":
        text = digest_prompt(req.get("set"), req.get("subject", ""))
        return {"prompt": text, "chars": len(text)}
    if path == "/api/save_digest":
        return {"report": save_digest(req.get("set"), req.get("digest", ""), bool(req.get("overwrite")))}
    if path == "/api/graph":
        return {"report": python_graph(req.get("set"), req.get("subject", ""), bool(req.get("overwrite")))}
    if path == "/api/network":
        return {"report": network(req.get("set"), req.get("unit", "auto"))}
    if path == "/api/overview":
        network(req.get("set"), req.get("unit") or "auto")  # rebuilt each time, so a changed unit applies
        return {"report": prima.overview_report(_set_dir(req.get("set")))}
    if path == "/api/query":
        if not (req.get("node") or "").strip():
            raise ValueError("enter a word to look up")
        return {"report": prima.query_report(_set_dir(req.get("set")), req["node"].strip(),
                                             (req.get("target") or "").strip())}
    if path == "/api/prompt":
        text = build_prompt(req.get("vision", ""), req.get("blueprints", []), req.get("scores"),
                            req.get("prima") or {}, req.get("name", ""))
        return {"prompt": text, "chars": len(text)}
    if path in ("/api/preview", "/api/save"):
        report, char_dir, result = hydrate(req.get("blob", ""), req.get("name", ""), path == "/api/preview",
                                           req.get("prima") or {})
        return {"report": report, "name": os.path.basename(char_dir), **result}
    raise LookupError("not found")


class Handler(http.server.BaseHTTPRequestHandler):
    token = ""

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, bytes):
            data = body
        else:
            data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
            ctype += "; charset=utf-8"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _local_host(self):
        # Blocks DNS rebinding: a foreign hostname resolving to 127.0.0.1.
        port = self.server.server_address[1]
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def do_GET(self):
        if not self._local_host():
            return self._send(403, {"error": "forbidden host"})
        if self.path in ("/", "/index.html"):
            return self._send(200, _read(PAGE).replace("__TOKEN__", self.token), "text/html")
        if self.path in ("/logo.png", "/favicon.ico") and os.path.isfile(LOGO):
            with open(LOGO, "rb") as f:
                return self._send(200, f.read(), "image/png")
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._local_host() or self.headers.get("X-Studio-Token") != self.token:
            return self._send(403, {"error": "forbidden: reload the Studio page"})
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._send(413, {"error": "request too large"})
        try:
            return self._send(200, route(self.path, json.loads(self.rfile.read(length) or b"{}")))
        except LookupError:
            return self._send(404, {"error": "not found"})
        except Exception as e:  # shown on the page and in this terminal, never dropped
            self.log_error("%s failed: %s", self.path, e)
            return self._send(400, {"error": f"{type(e).__name__}: {e}"})

    def log_message(self, fmt, *args):
        sys.stderr.write("studio: " + fmt % args + "\n")


def serve(port=0, open_browser=True):
    Handler.token = secrets.token_urlsafe(24)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"Pessoa Studio: {url}  (Ctrl-C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStudio stopped.")
    finally:
        httpd.server_close()


def _self_test():
    global SETS_DIR
    p = parameters({t: 3 for t in TRAITS})
    assert p["temperature"] == 0.6 and p["max_tokens"] == 2304, p
    mid = {t: 3 for t in TRAITS}
    text = build_prompt("A tired lighthouse keeper.", ["coder", "editor"], mid, name="Ana")
    auto = build_prompt("A tired lighthouse keeper.", ["coder"])
    assert "There are no targets" in auto and "targets on the 1-5 scale" not in auto
    assert "'evidence' block" in auto and "within 0.3 of its target" in text
    for expect in ("Name: Ana.", "PRIMARY: coder", "SECONDARY: editor", "openness 3.0",
                   "TEMPLATE: big_five.json", "===== EVE MASTER PROMPT", "none: build from the vision only"):
        assert expect in text, expect
    assert "{USER_VISION_PROMPT}" not in text and "PRIMA MATERIA:" not in text
    fenced = "Here it is:\n```text\n--- FILE: skin.md ---\nx\n```json\n{}\n```\n--- FILE: seed.md ---\ny\n```\nEnjoy!"
    assert parse_hydration_blob(extract_blob(fenced)) == {"skin.md": "x\n```json\n{}\n```", "seed.md": "y"}

    story = ("Mara Quell walked to the harbour. Mara Quell feared the harbour at night. "
             "Tomas Ferro waited at the harbour for Mara Quell.\n\n") * 4
    saved_sets = SETS_DIR
    with tempfile.TemporaryDirectory() as d:
        SETS_DIR = os.path.join(d, "prima")
        try:
            # Two "user folders": only some of their files go into the set, and they are never written to.
            a, b = os.path.join(d, "folder_a"), os.path.join(d, "folder_b")
            for folder, files in ((a, {"story.txt": story, "unrelated.txt": "Shopping list."}),
                                  (b, {"story.txt": "Tomas Ferro mended nets.", "x.exe": "binary"})):
                os.makedirs(folder)
                for n, t in files.items():
                    with open(os.path.join(folder, n), "w", encoding="utf-8") as f:
                        f.write(t)
            before = {f: sorted(os.listdir(f)) for f in (a, b)}
            seen = browse(a)
            assert [f["rel"] for f in seen["files"]] == ["story.txt", "unrelated.txt"], seen
            sname = scan_set(new_set())["set"]
            r = add_paths(sname, [os.path.join(a, "story.txt"), os.path.join(b, "story.txt"),
                                  os.path.join(b, "x.exe"), os.path.join(d, "missing.txt")])
            assert r["added"] == ["story.txt", "story (2).txt"], r
            assert [e["name"] for e in r["errors"]] == ["x.exe", "missing.txt"], r["errors"]
            r = add_uploads(sname, [{"name": "notes.md", "data": base64.b64encode(b"She hums.").decode()},
                                    {"name": "big.pdf", "data": ""}, {"name": "a.exe", "data": ""}])
            assert r["added"] == ["notes.md"] and len(r["errors"]) == 2, r
            assert "empty" in r["errors"][0]["error"].lower() and not os.path.exists(os.path.join(SETS_DIR, sname, "big.pdf"))
            assert sorted(f["path"] for f in scan_set(sname)["files"]) == ["notes.md", "story (2).txt", "story.txt"]
            assert sname in list_sets()

            full = build_prompt("x", ["coder"], mid, {"method": "full", "set": sname, "notes": "Hums."})
            assert "PRIMA MATERIA: FULL TEXT (3 files)" in full and "### FILE: story (2).txt" in full
            assert "Shopping list" not in full and "NOTES FROM THE USER =====\nHums." in full
            assert "SAVED:" in python_graph(sname, "Mara Quell")
            g_name = [x for x in scan_set(sname)["digests"] if x["auto"]][0]["name"]
            assert g_name == "Mara_Quell.auto.digest.json"
            g = build_prompt("x", ["coder"], mid, {"method": "graph", "set": sname, "digest": g_name})
            assert "PRIMA MATERIA: DIGEST Mara_Quell.auto.digest.json" in g and "shallow" in g
            good = {"subject": "Mara Quell", "source": "story", "nodes": [{"id": "m", "label": "Mara",
                    "evidence": [{"quote": "mended nets", "where": "b"}]}], "edges": [], "voice_samples": []}
            assert "verified against the source: 1/1" in save_digest(sname, "Here:\n```json\n" + json.dumps(good) + "\n```")
            good["nodes"][0]["evidence"][0]["quote"] = "loved the mountains"
            assert "already exists" in save_digest(sname, json.dumps(good))
            assert "NOT found" in save_digest(sname, json.dumps(good), overwrite=True)
            assert "words:" in network(sname, "auto") and scan_set(sname)["network"]
            assert "NODE: harbour" in prima.query_report(_set_dir(sname), "harbour", "Tomas")
            assert "TEMPLATE: prima_digest.json" in digest_prompt(sname, "Mara Quell")
            ov = build_prompt("x", ["coder"], None, {"method": "network", "set": sname})
            assert "PRIMA MATERIA: WORD NETWORK OVERVIEW" in ov and "Most connected words" in ov
            assert "Theme clusters" in ov and "build the character from that shape" in ov
            assert "Mara Quell walked" not in ov  # the overview carries words and links, not the text
            remove_file(sname, "notes.md")  # changing the files drops the derived network
            assert not scan_set(sname)["network"] and len(scan_set(sname)["files"]) == 2
            # The overview method builds a missing network itself.
            assert "WORD NETWORK OVERVIEW" in build_prompt("x", ["coder"], None, {"method": "network", "set": sname})
            assert scan_set(sname)["network"]
            assert {f: sorted(os.listdir(f)) for f in (a, b)} == before, "a user folder was written to"

            facets = json.loads(_read(os.path.join(TEMPLATES, "big_five.json")))["facets"]
            sheet = {"scale": "1-5", "summary": {t: 3 for t in TRAITS} | {"extraversion": 1.0},
                     "facets": {t: {f: 3 for f in fs} for t, fs in facets.items()},
                     "evidence": {"neuroticism": {"anxiety": {"reason": "r", "quote": "feared the harbour at night"},
                                                  "anger": {"reason": "r", "quote": "smashed the lamp"}}}}
            blob = ("--- FILE: skin.md ---\n- **Name**: Mara Quell\n--- FILE: engine.md ---\ne\n"
                    f"--- FILE: big_five.json ---\n{json.dumps(sheet)}\n--- FILE: seed.md ---\ns\n"
                    "--- FILE: operational_rules.md ---\nr\n")
            rep, char_dir, result = hydrate(blob, "Studio_Self_Test", True, {"set": sname, "notes": "x"})
            assert not os.path.exists(char_dir), "dry run wrote a folder"
            assert "Summary set from facets: extraversion 1.0 -> 3.0" in rep, rep
            assert "Evidence quotes found in the source: 1/2" in rep and "'smashed the lamp'" in rep, rep
            assert result["traits"]["extraversion"] == 3.0 and result["parameters"]["max_tokens"] == 2304
            assert result["missing"] == [] and result["activation"] == ""
            # A reply cut off after big_five.json, then a second reply resending from seed.md.
            cut = "Sure:\n```text\n" + blob.split("--- FILE: seed.md")[0] + "--- FILE: seed.md ---\nhalf"
            assert hydrate(cut.split("--- FILE: seed.md")[0], "X", True)[2]["missing"] == ["seed.md", "operational_rules.md"]
            joined = cut + "\n\nContinuing:\n```text\n--- FILE: seed.md ---\ns\n--- FILE: operational_rules.md ---\nr\n```"
            files = parse_hydration_blob(extract_blob(joined))
            assert files["seed.md"] == "s" and files["operational_rules.md"] == "r", files

            limit = FULL_TEXT_LIMIT
            globals()["FULL_TEXT_LIMIT"] = 1
            try:
                build_prompt("x", ["coder"], mid, {"method": "full", "set": sname})
                raise AssertionError("over-limit full text accepted")
            except ValueError as e:
                assert "more than one Claude chat holds" in str(e)
            finally:
                globals()["FULL_TEXT_LIMIT"] = limit
        finally:
            SETS_DIR = saved_sets
    assert route("/api/name", {"name": "Tomás Brisa"}) == {"folder": "Tomas_Brisa", "taken": "Tomas_Brisa" in characters()}
    for bad in (lambda: build_prompt(" ", ["coder"], mid),
                lambda: build_prompt("x", ["nope"], mid),
                lambda: _set_dir("../characters"),
                lambda: _set_dir("no-such-set"),
                lambda: extract_blob("no blob here"),
                lambda: browse("/no/such/folder")):
        try:
            bad()
        except ValueError:
            pass
        else:
            raise AssertionError("bad input accepted")
    return True


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Guided heteronym creation in your browser.")
    ap.add_argument("--port", type=int, default=0, help="port (default: any free one)")
    ap.add_argument("--no-browser", action="store_true", help="print the URL, do not open it")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        print("studio self-test:", _self_test())
    else:
        serve(args.port, not args.no_browser)
