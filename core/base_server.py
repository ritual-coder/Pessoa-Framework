import contextlib
import functools
import io
import json
import os
import re
import sys
import logging
from typing import Annotated

from pydantic import Field
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.mcpserver import Context, MCPServer, Message, UserMessage

# Ensure we can import from core and scripts
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT_DIR)
sys.path.append(os.path.join(ROOT_DIR, "core"))

# Imported after the path setup above so this works both when run directly
# (python core/base_server.py) and when imported as core.base_server.
from digest import parse_digest, unfence  # noqa: E402
from extract import build_graph  # noqa: E402
from naming import safe_character_dir, sanitize_heteronym_name  # noqa: E402
from sources import SUPPORTED  # noqa: E402
import audit as audit_mod  # noqa: E402
import prima as prima_mod  # noqa: E402
import seeds as seeds_mod  # noqa: E402
import export as export_mod  # noqa: E402
import memory as memory_mod  # noqa: E402
import state as state_mod  # noqa: E402
from validate import format_findings, validate_dir  # noqa: E402

# Configure logging
# PESSOA_LOG_FILE moves it (the test suite does, so its runs stay out of yours).
LOG_FILE = os.environ.get("PESSOA_LOG_FILE") or os.path.join(ROOT_DIR, "pessoa_bridge.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename=LOG_FILE,
    filemode='a'
)
# Also log to stderr for MCP
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logging.getLogger('').addHandler(console)
logger = logging.getLogger("g_mpf_bridge")
logger.info(f"Pessoa Bridge starting. Root: {ROOT_DIR}")

CHARACTERS_DIR = os.path.join(ROOT_DIR, "characters")
TEMPLATES_DIR = os.path.join(ROOT_DIR, "templates")

# Optional corpus of notes used as Prima Materia. Nothing is shipped with the
# framework: point PESSOA_ANALYSIS_DIR at a directory of your own .md, .txt,
# .docx or .pdf files.
# Its contents are read verbatim into the model's context, so do not aim it at
# private material you would not paste into a chat yourself.
ANALYSIS_DIR_ENV = "PESSOA_ANALYSIS_DIR"

# State management
# We'll use a simple in-memory state. In a persistent system, this could be a file.
ACTIVE_CHARACTER = None

# Sent to the client at connect; clients that support it (Claude Desktop reads
# it) give it to the model. It is the only way to tell the model, before any
# tool call, when the state and memory tools are worth calling.
SERVER_INSTRUCTIONS = """Pessoa Framework: create, load and run heteronyms (character profiles).
- To become a character use the load_character or activate_<name> prompts, or get_activation_prompt(name) when the user picks one; to create one, the create_character prompt. Tools like get_active_identity return data to inspect, not a voice to adopt.
- While speaking as a character, keep its memory: call remember(name, text, kind) when the user states a lasting preference, makes a decision, or an ongoing storyline or relationship changes. Do not store passwords, health, financial or other sensitive details unless the user asks you to remember them.
- Call set_character_state(name, ...) only when the conversation really moves the character (it gets tired, stressed, starts or stops trusting the user, takes up a new thread), not every turn.
- When recall_memory says it is time to compact, write a summary that keeps decisions, preferences, open arcs and relationships, and save it with compact_memory.
- Before saving a new character, run trigger_identity_hydration with dry_run=True and show the user the plan."""

# Initialize MCP Server
mcp = MCPServer("G-MPF Bridge", instructions=SERVER_INSTRUCTIONS)
# Advertise prompts.listChanged, so a client that honours it refreshes the
# activate_<name> list when a character is added (see _sync_character_prompts).
# shortcut: reaches into the SDK's private _lowlevel_server; MCPServer has no
# public switch for it in mcp 2.3. Re-check on SDK upgrades. If a release
# renames it, only this announcement is lost; the server still starts.
try:
    mcp._lowlevel_server.create_initialization_options = functools.partial(
        mcp._lowlevel_server.create_initialization_options,
        notification_options=NotificationOptions(prompts_changed=True))
except AttributeError as e:
    logger.warning(f"prompts.listChanged not advertised (mcp SDK changed: {e}); "
                   "new characters appear in the prompt list after a client restart.")

# Caps on the Prima Materia folder, so a mis-set PESSOA_ANALYSIS_DIR (a home
# folder, a downloads dump) is refused file by file instead of flooding a chat.
MAX_SOURCES, MAX_SOURCE_MB = prima_mod.MAX_SOURCES, prima_mod.MAX_SOURCE_MB

def _analysis_dir():
    """(directory, None) when Prima Materia is configured, else (None, message)."""
    analysis_dir = os.environ.get(ANALYSIS_DIR_ENV)
    if not analysis_dir:
        return None, (
            f"No Prima Materia directory configured. Set {ANALYSIS_DIR_ENV} to a "
            "folder of .md, .txt, .docx or .pdf files to feed raw source material into character "
            "creation, then call this tool again. Everything in that folder is "
            "read verbatim into the conversation, so point it only at material "
            "you are comfortable sharing with the model."
        )
    analysis_dir = os.path.expanduser(analysis_dir)
    if not os.path.isdir(analysis_dir):
        return None, (
            f"Error: {ANALYSIS_DIR_ENV} is set to '{analysis_dir}', which is not "
            "an existing directory."
        )
    return analysis_dir, None


def _read_sources(analysis_dir):
    """See prima.read_sources; the caps are read here so tests can lower them."""
    return prima_mod.read_sources(analysis_dir, MAX_SOURCES, MAX_SOURCE_MB)


@mcp.tool()
def fetch_analysis_data() -> str:
    """Reads the Prima Materia sources (.md .txt .docx .pdf) as text, verbatim.

    Full fidelity, but a novel costs ~80k tokens of context. For a compact,
    evidence-backed alternative see fetch_prima_digest().
    """
    logger.info("CALL: fetch_analysis_data")
    analysis_dir, err = _analysis_dir()
    if err:
        return err

    sources, skipped = _read_sources(analysis_dir)
    skipped_note = (f"\nSkipped (unsupported type, convert to {', '.join(SUPPORTED)}): "
                    + ", ".join(skipped)) if skipped else ""
    if not sources:
        return f"No supported files found in {analysis_dir}.{skipped_note}"

    output = [f"--- START OF ANALYSIS DATA FROM {analysis_dir} ---\n"]
    for rel_path, text, error in sources:
        output.append(f"### ERROR READING {rel_path}: {error}\n" if error
                      else f"### FILE: {rel_path}\n{text}\n")
    output.append("\n--- END OF ANALYSIS DATA ---" + skipped_note)
    return "\n".join(output)


@mcp.tool()
def save_prima_digest(digest: str, overwrite: bool = False) -> str:
    """Saves a Prima Materia digest (semantic graph of one subject) next to the source.

    Write it from the full text after fetch_analysis_data(), following
    get_template("prima_digest.json"). Every evidence quote is checked against
    the source and the ones it does not contain are reported, so fix and save
    again with overwrite=True.
    """
    logger.info("CALL: save_prima_digest")
    analysis_dir, err = _analysis_dir()
    if err:
        return err

    parsed, problems = parse_digest(unfence(digest))
    if parsed is None:
        return "ERROR: digest not saved: " + "; ".join(problems)
    return _store_digest(analysis_dir, parsed, problems, ".digest.json", overwrite)


@mcp.tool()
def extract_prima_graph(subject: str, overwrite: bool = False) -> str:
    """Builds a digest of `subject` with Python rules instead of a model.

    The third Prima Materia method: instant, free and reproducible, and every
    quote comes verbatim from the source. But it only sees what is spelled out:
    names it finds by capitalisation (pronouns are invisible), relations are
    co-occurrence counts, node types are guesses. Saved as
    <Subject>.auto.digest.json; read it with fetch_prima_digest(name).
    """
    logger.info(f"CALL: extract_prima_graph({subject})")
    analysis_dir, err = _analysis_dir()
    if err:
        return err
    if not subject.strip():
        return "ERROR: subject is empty."
    source = "\n\n".join(t for _, t, _ in _read_sources(analysis_dir)[0] if t)
    if not source:
        return f"ERROR: no readable source text in {analysis_dir}."
    graph = build_graph(source, subject.strip())
    if len(graph["nodes"]) <= 1:
        return (f"ERROR: found nothing linked to {subject!r}. Check the spelling: "
                "the subject must appear in the text with a capital letter.")
    return _store_digest(analysis_dir, graph, [], ".auto.digest.json", overwrite)


NETWORK_FILES = prima_mod.NETWORK_FILES


@mcp.tool()
def build_prima_network(unit: str = "auto") -> str:
    """Builds the word co-occurrence network of all Prima Materia sources.

    Every word except stop words is a node, two words are linked when they share a unit, weight =
    shared units. unit: "line" (verse), "sentence" (prose) or "auto" (by typical
    line length). Writes prima_network_nodes.tsv / prima_network_edges.tsv next
    to the sources. Query it with query_prima_network().
    """
    logger.info(f"CALL: build_prima_network({unit})")
    analysis_dir, err = _analysis_dir()
    if err:
        return err
    if unit not in ("auto", "line", "sentence"):
        return "ERROR: unit must be 'auto', 'line' or 'sentence'."
    sources = [(p, t) for p, t, _ in _read_sources(analysis_dir)[0] if t]
    if not sources:
        return f"ERROR: no readable source text in {analysis_dir}."
    return prima_mod.network_report(analysis_dir, sources, unit)


@mcp.tool()
def query_prima_network(node: str, target: str = "", top: int = 25) -> str:
    """How one node of the Prima Materia network connects to all the other nodes.

    Returns its strongest direct links, how many nodes sit 1, 2, 3... links away,
    and how many it cannot reach. With target, also the strongest shortest path
    between the two (e.g. node="Mara", target="Ilse"). Build the network
    first with build_prima_network().
    """
    logger.info(f"CALL: query_prima_network({node}, {target})")
    analysis_dir, err = _analysis_dir()
    if err:
        return err
    return prima_mod.query_report(analysis_dir, node, target, top)


@mcp.tool()
def prima_network_overview(top: int = 40) -> str:
    """The shape of the whole Prima Materia word network, with no word chosen.

    The most connected words, the names, the strongest and most distinctive word
    pairs, and theme clusters around the central words. Use it to build a
    character from a text without a specific person in it. Build the network
    first with build_prima_network().
    """
    logger.info(f"CALL: prima_network_overview({top})")
    analysis_dir, err = _analysis_dir()
    if err:
        return err
    return prima_mod.overview_report(analysis_dir, top=max(5, min(top, 200)))


def _store_digest(analysis_dir, parsed, problems, suffix, overwrite):
    """See prima.store_digest; the quotes are checked against every source here."""
    source = "\n".join(t for _, t, _ in _read_sources(analysis_dir)[0] if t)
    return prima_mod.store_digest(analysis_dir, parsed, problems, suffix, overwrite, source)


@mcp.tool()
def fetch_prima_digest(name: str = "") -> str:
    """Returns saved Prima Materia digests: compact semantic graphs of a subject.

    A few thousand tokens instead of the whole source, but lossy: details the
    digest left out are gone. Quotes were checked against the source on save.
    Files: <Subject>.digest.json (model-written) and <Subject>.auto.digest.json
    (Python rules). Pass name to read just one; without it, all are returned.
    """
    logger.info(f"CALL: fetch_prima_digest({name})")
    analysis_dir, err = _analysis_dir()
    if err:
        return err
    names = sorted(f for f in os.listdir(analysis_dir) if f.endswith(".digest.json"))
    if not names:
        return ("No digest saved yet. Write one with save_prima_digest() after "
                "fetch_analysis_data(), or build one with extract_prima_graph(subject).")
    if name:
        # Exact names from the listing only, so no path can reach outside the folder.
        if name not in names:
            return f"ERROR: no digest named {name!r}. Saved digests: {', '.join(names)}"
        names = [name]
    out = []
    for name in names:
        with open(os.path.join(analysis_dir, name), "r", encoding="utf-8") as f:
            out.append(f"### DIGEST: {name}\n{f.read()}")
    return "\n\n".join(out)

@mcp.tool()
def get_framework_templates() -> str:
    """
    Returns the paths and purpose of the Pessoa Framework templates.
    """
    seeds_dir = os.path.join(ROOT_DIR, "Seeds")
    blueprints = []
    if os.path.isdir(seeds_dir):
        blueprints = sorted(
            f for f in os.listdir(seeds_dir) if f.endswith("_blueprint.md")
        )
    # Indent continuation lines to match the block below; the first line
    # inherits its indentation from the template itself.
    blueprint_list = "\n    ".join("- " + b for b in blueprints) or "(none found)"

    return f"""
    Templates Location: {TEMPLATES_DIR}
    - skin.md: Layer 1 - The biography and voice (The Appearance).
    - engine.md: Layer 1 - The psychological structure (The Depth).
    - big_five.json: Layer 1 - Trait/facet scoring sheet. Declares its own 'scale'
      ('0-1', '1-5' or '0-100') and drives the AI Cabinet parameter math.
    - operational_rules.md: Layer 3 - Behavioral constraints (The Protocol).
    - ai_cabinet.yaml: Final LLM behavioral parameters and manifest.
    - available_tools.yaml: The capability set authorised for a heteronym.
    - seed_guidance_protocol.md: How to graft a Mission onto a finished Soul.
    - eve_master_prompt.md: The full creation prompt (see get_creation_guide()).
    - prima_digest.json: Schema for a Prima Materia digest (see save_prima_digest()).
    - benchmark_scenarios.md: Fixed scenarios and rubric (see the benchmark_character prompt).

    Layer 2 mission blueprints live separately, in {seeds_dir}:
    {blueprint_list}
    Several can be layered into one with compose_seeds("coder, security_auditor").
    A heteronym's finished seed.md is written into its characters/<name>/ folder.

    Read any of these files with get_template(name), e.g. get_template("big_five.json").
    """


@mcp.tool()
def get_template(name: str) -> str:
    """Returns the text of a framework template or mission blueprint.

    Pass a bare filename listed by get_framework_templates(), for example
    'big_five.json', 'skin.md', 'seed_guidance_protocol.md' or 'coder_blueprint.md'.
    """
    logger.info(f"CALL: get_template({name})")
    for folder in (TEMPLATES_DIR, os.path.join(ROOT_DIR, "Seeds")):
        # Only exact names from the folder listing match, so "../x" or an
        # absolute path can never resolve to a file outside these two folders.
        path = os.path.join(folder, name)
        if os.path.isdir(folder) and name in os.listdir(folder) and os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
    return f"Error: no template named {name!r}. Call get_framework_templates() for the list."

@mcp.tool()
def get_creation_guide() -> str:
    """
    Returns the master instructions for heteronym creation.
    Reference this to understand the Step-by-Step Lifecycle (Soul -> Seed -> Protocol).
    """
    guide_path = os.path.join(TEMPLATES_DIR, "eve_master_prompt.md")
    if not os.path.exists(guide_path):
        return f"Error: EVE master prompt not found at {guide_path}."

    with open(guide_path, "r", encoding="utf-8") as f:
        guide = f.read()

    # Optional long-form companion. Ships with the framework only if the author
    # has added it; the EVE prompt above is self-sufficient without it.
    lifecycle_path = os.path.join(ROOT_DIR, "docs", "PESSOA_LIFECYCLE_GUIDE.md")
    if os.path.exists(lifecycle_path):
        with open(lifecycle_path, "r", encoding="utf-8") as f:
            guide += "\n\n---\n\n" + f.read()

    return guide

@mcp.tool()
def list_characters() -> str:
    """Lists all available characters generated in the framework."""
    if not os.path.isdir(CHARACTERS_DIR):
        return f"Characters directory not found at {CHARACTERS_DIR}."

    chars = sorted(
        d for d in os.listdir(CHARACTERS_DIR)
        if os.path.isdir(os.path.join(CHARACTERS_DIR, d))
    )
    if not chars:
        return "No character folders found in characters/ directory."

    return "Available Characters:\n- " + "\n- ".join(chars)

@mcp.tool()
def select_character(name: str) -> str:
    """Sets the active heteronym for this session."""
    global ACTIVE_CHARACTER
    try:
        char_path = safe_character_dir(CHARACTERS_DIR, name)
    except ValueError as e:
        return f"Error: {e}"

    if not os.path.isdir(char_path):
        return f"Error: Character '{name}' not found at {char_path}"

    ACTIVE_CHARACTER = os.path.basename(char_path)
    return (f"Active character set to: {ACTIVE_CHARACTER}. "
            "Call get_active_identity() to load its layers.")

@mcp.tool()
def get_active_identity() -> str:
    """
    Returns the full content of the active heteronym across all layers.
    Call this after select_character() to load the soul, seed and protocol context.
    """
    if not ACTIVE_CHARACTER:
        return "No active heteronym selected. Use select_character(name) first."

    # ACTIVE_CHARACTER is a basename of an already-validated path, so this cannot
    # escape; routing it through the same helper keeps that guarantee local.
    try:
        char_path = safe_character_dir(CHARACTERS_DIR, ACTIVE_CHARACTER)
    except ValueError as e:
        return f"Error: {e}"
    output = [f"### ACTIVE HETERONYM: {ACTIVE_CHARACTER} ###\n"]
    
    files_to_load = [
        ("skin.md", "LAYER 1: THE SKIN (Identity & Voice)"),
        ("engine.md", "LAYER 1: THE ENGINE (Psychology)"),
        ("big_five.json", "LAYER 1: BIG FIVE SCORES"),
        ("seed.md", "LAYER 2: THE SEED (Mission)"),
        ("operational_rules.md", "PROTOCOL: OPERATIONAL RULES"),
        ("ai_cabinet.yaml", "MANIFEST: AI CABINET"),
        # Last: it distils the layers above, and an authored version may carry
        # framing that exists nowhere else. Including it also makes the file
        # observable -- without it there is no way to tell from the tools
        # whether an authored prompt survived hydration or was regenerated over.
        ("ACTIVATION_PROMPT.md", "ARTIFACT: ACTIVATION PROMPT")
    ]
    
    for filename, label in files_to_load:
        file_path = os.path.join(char_path, filename)
        output.append(f"== {label} ==")
        if os.path.exists(file_path):
            # Character files contain em-dashes and other non-ASCII; without an
            # explicit encoding this crashes on systems that default to cp1252.
            with open(file_path, "r", encoding="utf-8") as f:
                output.append(f.read())
        else:
            output.append("[File not found]")
        output.append("\n")
        
    return "\n".join(output)

# Removed get_active_profile as it is now redundant with get_active_identity


# --- Prompts -----------------------------------------------------------------
# Tools return data: the model reads a tool result, it does not adopt it. That
# makes get_active_identity() the wrong surface for putting a character on --
# it returns the layers for inspection, and a model handed 50KB of source
# material reasonably responds by reviewing it.
#
# MCP prompts are inserted into the conversation as user messages, which is the
# shape a persona actually needs. ACTIVATION_PROMPT.md is the artifact built to
# be adopted rather than read, so that is what these serve.

_HOLD_INSTRUCTION = (
    "\n\n---\n\n"
    "Hold this register for the rest of the conversation. Do not summarise or "
    "comment on the profile — answer my next message in voice. Stay in "
    "character: no disclaimers or asides about being an AI, and questions asked "
    "inside the story get in-story answers. Only if I clearly and sincerely ask "
    "whether I am talking to an AI, say so in one line, then return to the voice."
)


def _known(name):
    """The folder name of an existing character, written as a person would type it:
    "Helena Vos", "helena vos" and "Helena_Vos" all find Helena_Vos."""
    key = sanitize_heteronym_name(str(name).strip(), default="").lower()
    folders = [n for n, _ in _characters()] if os.path.isdir(CHARACTERS_DIR) else []
    return next((f for f in folders if f.lower() == key), None) if key else None


def _char_dir(name):
    """The folder of an existing character; ValueError for an unknown or unsafe name."""
    char_dir = safe_character_dir(CHARACTERS_DIR, _known(name) or name)
    if not os.path.isdir(char_dir):
        raise ValueError(f"no character named {name!r}")
    return char_dir


def _profile_text(name):
    """ACTIVATION_PROMPT.md as written: the voice without session framing."""
    char_dir = _char_dir(name)
    path = os.path.join(char_dir, "ACTIVATION_PROMPT.md")
    if not os.path.isfile(path):
        raise ValueError(
            f"'{os.path.basename(char_dir)}' has no ACTIVATION_PROMPT.md. "
            "Re-hydrate it; the framework generates one for every heteronym."
        )
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _activation_text(name):
    """The activation prompt for a heteronym, ready to insert: the profile, then
    its current state and memory when it has any, then the hold instruction."""
    char_dir = _char_dir(name)
    parts = [_profile_text(name)]
    overlay = state_mod.overlay_text(state_mod.load(char_dir))
    if overlay:
        parts.append(overlay)
    remembered = memory_mod.recall(char_dir, limit=15)
    if remembered:
        parts.append("## What you remember (from earlier sessions)\n" + remembered)
    return "\n\n".join(parts) + _HOLD_INSTRUCTION


@mcp.prompt(
    name="activate_heteronym",
    description="Become a heteronym by name. Inserts its activation prompt.",
)
def activate_heteronym(
    name: Annotated[str, Field(description="A character's name, e.g. Helena Vos")],
) -> list[Message]:
    """Activate any heteronym, including one created since the server started."""
    logger.info(f"PROMPT: activate_heteronym({name})")
    try:
        return [UserMessage(_activation_text(name))]
    except ValueError as e:
        return _prompt_error("activate_heteronym", e)


_CHARACTER_PROMPTS = set()  # names of the activate_<name> prompts registered so far


def _sync_character_prompts():
    """One activate_<name> prompt per character on disk, added and removed as
    characters come and go. Returns True when the list changed.

    Runs at startup, on every prompt listing, and after hydration, so a
    character made by scripts/hydrate.py appears without a restart -- in
    clients that re-list prompts. Some clients cache the list per connection.
    """
    present = {name for name, _ in _characters()}
    for entry in sorted(present - _CHARACTER_PROMPTS):
        def _make(character):
            def _prompt() -> list[Message]:
                logger.info(f"PROMPT: activate_{character}")
                return [UserMessage(_activation_text(character))]
            return _prompt

        mcp.prompt(
            name=f"activate_{entry}",
            description=f"Become {entry.replace('_', ' ')} — adopt its voice and expertise.",
        )(_make(entry))
    for entry in _CHARACTER_PROMPTS - present:
        mcp.remove_prompt(f"activate_{entry}")
    changed = present != _CHARACTER_PROMPTS
    if changed:
        logger.info(f"Activation prompts: {sorted(present)}")
    _CHARACTER_PROMPTS.clear()
    _CHARACTER_PROMPTS.update(present)
    return changed


_list_prompts = mcp.list_prompts


async def _list_prompts_fresh():
    _sync_character_prompts()
    return await _list_prompts()


mcp.list_prompts = _list_prompts_fresh


async def _announce_prompts(ctx):
    """Tell the client the prompt list changed. Clients may ignore it."""
    if not _sync_character_prompts():
        return
    with contextlib.suppress(Exception):  # best effort: not every transport carries it
        await ctx.session.send_prompt_list_changed()


# --- Guided flows -------------------------------------------------------------
# Two menu entries that walk the user through creating and loading a character
# one decision at a time. Each is rendered when opened, from the live state
# (sources, digests, network, blueprints, existing names), so every option it
# offers is real.

def _tagline(char_dir):
    """A character's tagline from its activation prompt, however it is formatted:
    "**Tagline**: *...*", "- **Tagline**: \"...\"", or a "## Tagline" heading."""
    try:
        with open(os.path.join(char_dir, "ACTIVATION_PROMPT.md"), "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return ""
    m = re.search(r"Tagline\**[ \t]*:?[ \t]*\**[ \t]*(.*)\n*(.*)", text)
    if not m:
        return ""
    value = (m.group(1).strip() or m.group(2).strip()).strip(' *_"“”')
    return value[:90] + ("…" if len(value) > 90 else "")


def _characters():
    """[(name, tagline)] for characters that can be loaded (have an activation prompt)."""
    if not os.path.isdir(CHARACTERS_DIR):
        return []
    return [(d, _tagline(os.path.join(CHARACTERS_DIR, d))) for d in sorted(os.listdir(CHARACTERS_DIR))
            if os.path.isfile(os.path.join(CHARACTERS_DIR, d, "ACTIVATION_PROMPT.md"))]


_sync_character_prompts()


def _prima_status():
    """Plain-text description of the Prima Materia folder for the wizard."""
    analysis_dir, err = _analysis_dir()
    if err:
        return ("NOT CONFIGURED. To use one, the user sets PESSOA_ANALYSIS_DIR in the "
                "client's MCP config to a folder of .md/.txt/.docx/.pdf files and restarts.")
    sources, skipped = _read_sources(analysis_dir)
    lines = [f"Folder: {analysis_dir}"]
    for rel, text, error in sources:
        lines.append(f"- {rel}: " + (f"UNREADABLE ({error})" if error
                                     else f"~{len(text) // 4:,} tokens"))
    if skipped:
        lines.append("- skipped (unsupported): " + ", ".join(skipped))
    digests = sorted(f for f in os.listdir(analysis_dir) if f.endswith(".digest.json"))
    lines.append("Saved digests: " + (", ".join(digests) if digests else "none"))
    lines.append("Word network: " + ("built" if os.path.exists(
        os.path.join(analysis_dir, NETWORK_FILES[0])) else "not built"))
    return "\n".join(lines)


@mcp.prompt(
    name="create_character",
    description="Step-by-step wizard: choose a source and method, design, review, save and load a new heteronym.",
)
def create_character() -> list[Message]:
    blueprints = sorted(f[:-len("_blueprint.md")] for f in os.listdir(os.path.join(ROOT_DIR, "Seeds"))
                        if f.endswith("_blueprint.md")) if os.path.isdir(os.path.join(ROOT_DIR, "Seeds")) else []
    taken = [n for n, _ in _characters()]
    return [UserMessage(f"""Guide me through creating a new Pessoa heteronym, step by step.

How to run this wizard:
- One step at a time. Each step: a one-line title ("Step 2 of 8: Method"), the options as a numbered list with a one-line trade-off each, and your recommendation. Then wait for my answer.
- I can answer with a number, say "back" to redo the previous step, or "skip" where a step is optional.
- Never jump ahead, and never save anything before step 7.

Current state (read when this wizard opened):
PRIMA MATERIA: {_prima_status()}
BLUEPRINTS: {", ".join(blueprints) or "none found"}
EXISTING CHARACTERS (names already taken): {", ".join(taken) or "none"}

Steps:
1. Source. (1) none, build from my vision only; (2) I paste notes into the chat; (3) the Prima Materia folder above, if configured. Recommend 3 when it holds a text about the character.
2. Method (only if the source is the folder). (1) Full text with fetch_analysis_data(): most faithful, costs the token counts above, and invented facts can slip in; (2) AI digest with fetch_prima_digest(name): small and reusable, lossy; if none exists, build one first with save_prima_digest() and tell me to continue in a fresh chat; (3) Python graph with extract_prima_graph(subject): instant and verbatim but shallow, names only; (4) word network overview with build_prima_network() then prima_network_overview(): no person to choose, the shape of the whole text, for a character inspired by its world or themes. Recommend 2 or 3 above ~60k tokens, and 4 when there is no specific person.
3. Explore (optional). Offer build_prima_network() if not built, then query_prima_network(subject) and paths to 1-2 people I name, so we see how the subject connects before writing. Summarise in 3-4 lines.
4. Subject and vision. Ask who the character is and what I want them for, in one question.
5. Blueprint. Offer the blueprints above, with one line each on what they suit. I may pick several (e.g. "1, 4"): the first is the primary; then call compose_seeds() with them and use the composite.
6. Write. Call get_creation_guide() and get_template() for each layer, and follow the EVE guide. Then show me a short review: name, tagline, Big Five in one line, 3 voice lines, mission in one line, and anything marked inferred. Ask: approve, change something, or see the full blob.
7. Save. Propose a folder name that is not in the taken list above and let me change it. Call trigger_identity_hydration(blob, name=..., dry_run=True) and show me the plan and the validation findings; offer to fix any ERROR or WARN in the blob first. Only when I say save, call it again without dry_run. Show the report.
8. Load. Tell me the character is ready and how to load it: the "load_character" prompt (pick from a list) or "activate_heteronym" with the name. Its own "activate_<name>" entry appears once the client refreshes its prompt list (some clients only on restart). Mention export_character() for use in other apps.

Start with Step 1 now.""")]


@mcp.prompt(
    name="load_character",
    description="Pick a heteronym from a list and start talking to it.",
)
def load_character(
    name: Annotated[str, Field(description="Optional: a character's name; leave empty to pick from a list")] = "",
) -> list[Message]:
    if name:
        try:
            return [UserMessage(_activation_text(name))]
        except ValueError as e:
            return _prompt_error("load_character", e)
    chars = _characters()
    if not chars:
        return [UserMessage("There are no characters yet. Open the create_character prompt to make one.")]
    menu = "\n".join(f"{i}. {n.replace('_', ' ')}" + (f" — {t}" if t else "")
                     for i, (n, t) in enumerate(chars, 1))
    return [UserMessage(f"""I want to load a Pessoa heteronym. Show me this list exactly as numbered and ask which one:

{menu}

When I answer, call get_activation_prompt with the folder name of my choice ({", ".join(n for n, _ in chars)}), then adopt that voice for the rest of the conversation as its text instructs. Answer my next message in voice; do not summarise the profile.""")]


@mcp.tool()
def get_activation_prompt(name: str) -> str:
    """The activation prompt of a heteronym, for loading it after the user picked it
    in load_character. Adopt it only when the user asked to load that character."""
    logger.info(f"CALL: get_activation_prompt({name})")
    try:
        return _activation_text(name)
    except ValueError as e:
        return f"Error: {e}"

@mcp.tool()
async def debug_framework(ctx: Context) -> str:
    """View internal system status and exposed tools."""
    tools = [t.name for t in await mcp.list_tools()]
    caps = ctx.client_capabilities
    # Whether this client can show the server's own form fields (MCP elicitation).
    forms = "yes" if caps is not None and getattr(caps, "elicitation", None) else "no"
    logger.info(f"CALL: debug_framework -> Tools: {tools}")
    analysis_dir = os.environ.get(ANALYSIS_DIR_ENV) or "(unset)"
    return f"""
    Internal Status:
    - Root Dir: {ROOT_DIR}
    - Characters Dir: {CHARACTERS_DIR}
    - Templates Dir: {TEMPLATES_DIR}
    - {ANALYSIS_DIR_ENV}: {analysis_dir}
    - Python Version: {sys.version}
    - Exposed Tools: {tools}
    - Client supports form fields (elicitation): {forms}
    - Log File: {LOG_FILE}
    """

@mcp.tool()
async def trigger_identity_hydration(hydration_blob: str, ctx: Context, name: str = "",
                                     dry_run: bool = False) -> str:
    """Creates a heteronym from a hydration blob.

    name is optional: without it the name is read from the Skin. Pass one to
    keep two versions of a character side by side (e.g. "Mara_Quell_Digest").
    dry_run=True writes nothing and reports what would be created, with the
    calculated parameters and the validation findings: show it to the user
    before the real save.
    """
    try:
        logger.info(f"CALL: trigger_identity_hydration(dry_run={dry_run})")
        from scripts.hydrate import hydrate_files, parse_hydration_blob
        files = parse_hydration_blob(hydration_blob)

        if not files:
            return "ERROR: No valid file blocks found in blob. Use format: --- FILE: filename ---"

        # The blob is model-generated: with no name given, hydrate_files derives
        # one from the Skin and sanitizes it to a single safe path segment.
        # Hydration prints its report (scores, sources, warnings): capture it
        # and hand it to the model, which can then fix the blob. (The SDK also
        # diverts stray prints away from the stdio protocol stream.)
        # With a Prima Materia folder, the evidence quotes in big_five.json are
        # checked against it, as digest quotes are.
        analysis_dir, _ = _analysis_dir()
        source_text = "\n\n".join(t for _, t, _ in _read_sources(analysis_dir)[0] if t) if analysis_dir else None
        report = io.StringIO()
        with contextlib.redirect_stdout(report):
            char_dir = hydrate_files(files, name or None, dry_run=dry_run, source_text=source_text)
        if dry_run:
            return f"DRY RUN for '{os.path.basename(char_dir)}':\n{report.getvalue()}"

        await _announce_prompts(ctx)
        return (f"SUCCESS: '{os.path.basename(char_dir)}' has been hydrated in the "
                f"local framework.\n\nReport:\n{report.getvalue()}")
    except Exception as e:
        import traceback
        err = f"ERROR in hydration: {str(e)}\n{traceback.format_exc()}"
        logger.error(err)
        return err


@mcp.tool()
async def reload_characters(ctx: Context) -> str:
    """Re-scan characters/ (e.g. after scripts/hydrate.py ran or a folder was
    deleted) and refresh the activate_<name> prompt list."""
    changed = _sync_character_prompts()
    with contextlib.suppress(Exception):
        await ctx.session.send_prompt_list_changed()
    names = sorted(_CHARACTER_PROMPTS)
    return (("Prompt list updated. " if changed else "No change. ") + f"{len(names)} characters: "
            + ", ".join(names) + ". If the client still shows the old list, it caches prompts per "
            "connection: load_character always sees the current folder.")


# --- Validation, migration ------------------------------------------------------

@mcp.tool()
def validate_character(name: str = "") -> str:
    """Check characters against the schema: 30 facets in range, the 10 Core Laws,
    CAN/CANNOT DO, cabinet ranges, drift from the calculus, schema_version.
    No name: every character."""
    names = [name] if name else [n for n, _ in _characters()]
    out = []
    for n in names:
        try:
            out.append(format_findings(n, validate_dir(_char_dir(n))))
        except ValueError as e:
            out.append(f"{n}: ERROR: {e}")
    return "\n".join(out) + ("\n\nOld schema? Preview the upgrade with: python scripts/migrate.py"
                             if "schema_version 1" in "".join(out) else "")


# --- Runtime state and memory -----------------------------------------------------

@mcp.tool()
def get_character_state(name: str) -> str:
    """The character's current mood overlay (fatigue, stress, trust, thread) and
    the parameters it shifts. The baseline profile never changes."""
    try:
        char_dir = _char_dir(name)
    except ValueError as e:
        return f"Error: {e}"
    st = state_mod.load(char_dir)
    import yaml
    with open(os.path.join(char_dir, "ai_cabinet.yaml"), encoding="utf-8") as f:
        params = (yaml.safe_load(f) or {}).get("parameters", {})
    return (json.dumps(st, indent=2) + "\n\nBaseline parameters: " + json.dumps(params)
            + "\nWith this state: " + json.dumps(state_mod.adjusted_parameters(params, st))
            + ("\n\n" + state_mod.overlay_text(st) if not state_mod.is_default(st) else ""))


@mcp.tool()
def set_character_state(name: str, fatigue: float | None = None, stress: float | None = None,
                        trust: float | None = None, thread: str | None = None,
                        reset: bool = False) -> str:
    """Update the character's runtime state while in voice. Levels are 0.0-1.0
    (trust 0.5 is neutral); thread is what it is in the middle of. Call it when
    the conversation really moves them, not every turn. reset=True clears it."""
    try:
        st = state_mod.update(_char_dir(name), reset=reset, thread=thread,
                              fatigue=fatigue, stress=stress, trust=trust)
    except ValueError as e:
        return f"Error: {e}"
    return "State saved: " + json.dumps(st)


@mcp.tool()
def remember(name: str, text: str, kind: str = "note", who: str = "") -> str:
    """Append one memory to the character's ledger: a decision, preference, arc
    (ongoing storyline), relationship or note. who names the person it is about.
    Save only what the user would expect the character to remember."""
    try:
        e = memory_mod.remember(_char_dir(name), text, kind, who)
    except ValueError as err:
        return f"Error: {err}"
    return f"Remembered ({e['kind']}): {e['text']}"


@mcp.tool()
def recall_memory(name: str) -> str:
    """The character's memory: the compacted summary, then entries since."""
    try:
        return memory_mod.recall(_char_dir(name)) or "No memories yet."
    except ValueError as e:
        return f"Error: {e}"


@mcp.tool()
def compact_memory(name: str, summary: str) -> str:
    """Replace the memory summary with a new one that folds in every open entry
    (call recall_memory first, and keep decisions, preferences, open arcs and
    relationships). The folded entries move to an archive; nothing is deleted."""
    try:
        n = memory_mod.compact(_char_dir(name), summary)
    except ValueError as e:
        return f"Error: {e}"
    return f"Summary saved; {n} entries archived."


# --- Drift audit, comparison, export -----------------------------------------------

@mcp.tool()
def audit_response(name: str, text: str) -> str:
    """Rule checks on a draft or transcript: generic-assistant tells, words the
    character avoids, leaks of framework values (Law of Privacy), narrating model
    mechanics (Law of the Threshold), and length/hedging/softening against the
    Big Five. Judging meaning is the audit_character prompt's job."""
    try:
        char_dir = _char_dir(name)
    except ValueError as e:
        return f"Error: {e}"
    return audit_mod.format_report(name, *audit_mod.audit(char_dir, text))


@mcp.tool()
def compare_characters(names: str) -> str:
    """Side by side: Big Five (1-5) and calculated parameters of two or more
    characters, comma-separated (e.g. "Helena_Vos, Tomas_Brisa")."""
    import yaml
    rows = []
    try:
        picked = _names(names, low=2, high=12)
    except ValueError as e:
        return f"Error: {e}"
    for n in picked:
        try:
            with open(os.path.join(_char_dir(n), "ai_cabinet.yaml"), encoding="utf-8") as f:
                cab = yaml.safe_load(f) or {}
        except (ValueError, OSError) as e:
            return f"Error: {e}"
        b, p = cab.get("big_five_normalized", {}), cab.get("parameters", {})
        rows.append(f"| {n} | " + " | ".join(str(b.get(t, "?")) for t in "OCEAN") + " | "
                    + " | ".join(str(p.get(k, "?")) for k in ("temperature", "top_p", "frequency_penalty",
                                                             "max_tokens", "confidence_threshold")) + " |")
    if len(rows) < 2:
        return "Error: give at least two names, comma-separated."
    head = ("| character | O | C | E | A | N | temperature | top_p | frequency_penalty | max_tokens | "
            "confidence |\n|" + "---|" * 11)
    return head + "\n" + "\n".join(rows)


@mcp.tool()
def export_character(name: str, target: str = "all") -> str:
    """Write runtime exports to characters/<name>/exports/: 'anthropic' (Messages
    API body), 'openai' (Chat Completions body), 'generic' (prompt + YAML
    parameters), 'json' (everything as data), or 'all'. Parameters include the
    current state's shifts."""
    try:
        char_dir = _char_dir(name)
        import yaml
        with open(os.path.join(char_dir, "ai_cabinet.yaml"), encoding="utf-8") as f:
            params = (yaml.safe_load(f) or {}).get("parameters", {})
        params = state_mod.adjusted_parameters(params, state_mod.load(char_dir))
        paths = export_mod.export(char_dir, target, params)
    except (ValueError, OSError) as e:
        return f"Error: {e}"
    return "Written:\n" + "\n".join(paths)


# --- Seeds ---------------------------------------------------------------------

@mcp.tool()
def compose_seeds(blueprints: str) -> str:
    """Layer several mission blueprints into one, primary first, e.g.
    "coder, security_auditor". Use it in creation when one blueprint is too narrow."""
    try:
        return seeds_mod.compose(blueprints.split(","))
    except ValueError as e:
        return f"Error: {e}"


# --- Council and handoff ----------------------------------------------------------

def _names(names, low=2, high=4):
    """Folder names of 2-4 characters from what the user typed.

    Commas, semicolons, "and" or new lines separate names; with no separator
    ("Helena Vos Tomas Brisa") the known character names are found in the text.
    """
    parts = [p.strip() for p in re.split(r",|;|\n|\band\b|&", names) if p.strip()]
    picked = [_known(p) for p in parts]
    if len(parts) < 2 or None in picked:
        # Longest names first, so "Mara_Quell_Digest" wins over "Mara_Quell".
        text = "_" + sanitize_heteronym_name(names, default="").lower() + "_"
        found = []
        for folder in sorted((n for n, _ in _characters()), key=len, reverse=True):
            mark = f"_{folder.lower()}_"
            if mark in text:
                found.append((text.index(mark), folder))
                text = text.replace(mark, "_" * len(mark), 1)
        if found and (len(parts) < 2 or len(found) >= len(parts)):
            picked = [f for _, f in sorted(found)]
        elif None in picked:
            unknown = [p for p, k in zip(parts, picked) if k is None]
            raise ValueError(f"no character named {', '.join(map(repr, unknown))}")
    picked = list(dict.fromkeys(picked))
    if not low <= len(picked) <= high:
        raise ValueError(f"this needs {low} to {high} different characters, got {len(picked)}"
                         + (f" ({', '.join(picked)})" if picked else ""))
    for n in picked:
        _profile_text(n)  # raises for a character without ACTIVATION_PROMPT.md
    return picked


def _prompt_error(prompt, error, example=""):
    """A prompt that cannot run returns this instead of raising: clients such as
    Claude Desktop show only "failed to attach prompt" for a raised error."""
    logger.info(f"PROMPT: {prompt} refused: {error}")
    names = ", ".join(n.replace("_", " ") for n, _ in _characters()) or "none yet"
    return [UserMessage(f"""The Pessoa prompt "{prompt}" could not run: {error}.

Tell me this in one or two plain sentences, list the available characters ({names}){example}, and ask me to try again. Do not start the prompt's task.""")]


@mcp.prompt(
    name="council",
    description="Round table: 2-4 heteronyms debate a plan, diff or argument, then a synthesis.",
)
def council(
    names: Annotated[str, Field(description="2 to 4 character names, e.g. Helena Vos, Tomas Brisa")],
    topic: Annotated[str, Field(description="What they discuss, e.g. a plan, a draft, a question, a scene")],
) -> list[Message]:
    try:
        picked = _names(names)
    except ValueError as e:
        return _prompt_error("council", e, ', and that names go like "Helena Vos, Tomas Brisa"')
    logger.info(f"PROMPT: council({picked})")
    profiles = "\n\n".join(f"===== PROFILE: {n} =====\n{_profile_text(n)}" for n in picked)
    return [UserMessage(f"""Run a Pessoa Council on the topic below. Members: {", ".join(picked)}.

{profiles}

===== TOPIC =====
{topic}

How to run it:
1. Opening: each member gives a position in their own voice, under a heading with their name. Each argues from their own psychology, mission and shadow, and names the risk they care about most.
2. Cross-examination: each member answers the strongest point of the member they disagree with most. They may concede; they may not merge into one voice.
3. Synthesis, in a neutral voice: where they agree, where they still disagree and why, a recommendation, and what would change it.
Keep the voices distinct: a reader should identify each speaker without the headings. Do not invent shared history between them. Stay honest: no member invents facts about the topic.""")]


@mcp.prompt(
    name="scene",
    description="Play a scene: 2-4 heteronyms talk and act together in a setting you describe; you can direct or join.",
)
def scene(
    names: Annotated[str, Field(description="2 to 4 character names, e.g. Helena Vos, Tomas Brisa")],
    setting: Annotated[str, Field(description="Where, when and what is going on, e.g. a first date in a coffee shop")],
    my_role: Annotated[str, Field(description="Optional: who you play in the scene; leave empty to only direct")] = "",
) -> list[Message]:
    try:
        picked = _names(names)
    except ValueError as e:
        return _prompt_error("scene", e, ', and that names go like "Helena Vos, Tomas Brisa"')
    logger.info(f"PROMPT: scene({picked})")
    profiles = "\n\n".join(f"===== PROFILE: {n} =====\n{_profile_text(n)}" for n in picked)
    me = (f"I play {my_role.strip()}. Never write my lines or my actions: when it is my turn, stop and let me speak."
          if my_role.strip() else
          "I do not play a part: I direct. I may add an event, cut to later, or ask for a moment again.")
    return [UserMessage(f"""Play a scene with these characters: {", ".join(n.replace("_", " ") for n in picked)}.

{profiles}

===== SETTING =====
{setting}

===== MY PART =====
{me}

How to play it:
- This is fiction played out, not a debate: the characters talk to each other and act, in the moment. No summary, no verdict, no narrator's opinion.
- Each line starts with the speaker's name in bold. Keep actions and setting short, in italics, between the lines.
- Every character speaks and acts from their own profile: voice, Big Five, shadow and fear. They want different things, and they do not blend into one voice. A reader should know who speaks without the names.
- They know only what their profile and the setting give them. Do not invent shared history between them beyond what the setting says.
- Their rules still hold: a character may refuse, tease, lie in the story or stay silent, but never breaks its CANNOT DO, and if I sincerely ask whether I am talking to an AI, answer honestly in one line, then return to the scene.
- Play in short stretches: about 8 to 14 lines, then stop at a natural beat and wait. I will say "go on", steer it, or speak as my part.
- If I write "(pause)", step out of the scene and answer me plainly; "(resume)" goes back in.

Start the scene now.""")]


@mcp.tool()
def handoff(from_name: str, to_name: str, task: str) -> str:
    """Delegate a sub-task from the active heteronym to another and get its
    findings back in its own voice. Returns the second character's profile and
    the format; the answer is written in this same chat."""
    try:
        profile = _profile_text(to_name)
        _profile_text(from_name)
    except ValueError as e:
        return f"Error: {e}"
    logger.info(f"CALL: handoff({from_name} -> {to_name})")
    return f"""HANDOFF from {from_name} to {to_name}.

{profile}

Sub-task: {task}

Answer the sub-task once, as {to_name}, in a block headed "## Findings from {to_name}" with: Answer, Evidence, Confidence (low/medium/high), Open questions. Keep {to_name}'s voice and limits; do not let {from_name}'s views leak in. Then return to {from_name}'s voice and react to the findings as {from_name} would. {to_name} does not stay active."""


# --- Evaluation ----------------------------------------------------------------

@mcp.prompt(
    name="audit_character",
    description="Check the conversation so far for drift from a heteronym's voice, rules and Big Five.",
)
def audit_character(
    name: Annotated[str, Field(description="The character whose replies are audited, e.g. Helena Vos")],
) -> list[Message]:
    try:
        profile = _profile_text(name)
    except ValueError as e:
        return _prompt_error("audit_character", e)
    name = _known(name) or name
    return [UserMessage(f"""Step out of the voice for one turn and audit how well the replies in this conversation held the heteronym {name}.

{profile}

1. Call audit_response(name="{name}", text=...) with your replies in this conversation, joined, for the rule checks.
2. Then judge what rules cannot: did the replies keep the register, the Big Five profile (one line per trait: expected vs shown), the 10 Core Laws and CAN/CANNOT DO, and the honesty rule?
3. Give a score 0-10 and the three changes that would most improve the next replies.
Afterwards, go back to the voice.""")]


@mcp.prompt(
    name="benchmark_character",
    description="Run the standard behavioural scenarios on a heteronym and score them.",
)
def benchmark_character(
    name: Annotated[str, Field(description="The character to benchmark, e.g. Helena Vos")],
) -> list[Message]:
    try:
        profile = _profile_text(name)
    except ValueError as e:
        return _prompt_error("benchmark_character", e)
    name = _known(name) or name
    with open(os.path.join(TEMPLATES_DIR, "benchmark_scenarios.md"), encoding="utf-8") as f:
        scenarios = f.read()
    return [UserMessage(f"""Benchmark the heteronym {name}. First adopt it:

{profile}

{scenarios}

Procedure: for each scenario S1-S8, show the scenario, answer it in {name}'s voice, then call audit_response(name="{name}", text=<that answer>). When all eight are done, step out of the voice and fill the rubric as a table (one row per scenario), with a total out of 80 and the weakest trait or law. Do not soften scores: the point is comparison across models and profiles. Mention the model you are, so runs can be compared.""")]


if __name__ == "__main__":
    logger.info("Pessoa Framework Bridge Active.")
    mcp.run()
