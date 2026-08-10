import os
import sys
import logging
from mcp.server.fastmcp import FastMCP

# Ensure we can import from core and scripts
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT_DIR)
sys.path.append(os.path.join(ROOT_DIR, "core"))

# Imported after the path setup above so this works both when run directly
# (python core/base_server.py) and when imported as core.base_server.
from naming import safe_character_dir  # noqa: E402

# Configure logging
LOG_FILE = os.path.join(ROOT_DIR, "pessoa_bridge.log")
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

# Consolidate Root Paths
CORE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(CORE_DIR)
CHARACTERS_DIR = os.path.join(ROOT_DIR, "characters")
TEMPLATES_DIR = os.path.join(ROOT_DIR, "templates")

# Optional corpus of notes used as Prima Materia. Nothing is shipped with the
# framework: point PESSOA_ANALYSIS_DIR at a directory of your own .md files.
# Its contents are read verbatim into the model's context, so do not aim it at
# private material you would not paste into a chat yourself.
ANALYSIS_DIR_ENV = "PESSOA_ANALYSIS_DIR"

# State management
# We'll use a simple in-memory state. In a persistent system, this could be a file.
ACTIVE_CHARACTER = None

# Initialize MCP Server
mcp = FastMCP("G-MPF Bridge")

@mcp.tool()
def fetch_analysis_data() -> str:
    """Reads the .md files in the configured Prima Materia directory.

    Opt-in: set the PESSOA_ANALYSIS_DIR environment variable to a directory of
    your own notes. Everything it finds is returned verbatim.
    """
    logger.info("CALL: fetch_analysis_data")

    analysis_dir = os.environ.get(ANALYSIS_DIR_ENV)
    if not analysis_dir:
        return (
            f"No Prima Materia directory configured. Set {ANALYSIS_DIR_ENV} to a "
            "folder of .md notes to feed raw source material into character "
            "creation, then call this tool again. Everything in that folder is "
            "read verbatim into the conversation, so point it only at material "
            "you are comfortable sharing with the model."
        )

    analysis_dir = os.path.expanduser(analysis_dir)
    if not os.path.isdir(analysis_dir):
        return (
            f"Error: {ANALYSIS_DIR_ENV} is set to '{analysis_dir}', which is not "
            "an existing directory."
        )

    output = [f"--- START OF ANALYSIS DATA FROM {analysis_dir} ---\n"]

    found = False
    for root, dirs, files in os.walk(analysis_dir):
        for file in sorted(files):
            if file.endswith(".md"):
                found = True
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, analysis_dir)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                        output.append(f"### FILE: {rel_path}\n{content}\n")
                except Exception as e:
                    output.append(f"### ERROR READING {rel_path}: {str(e)}\n")

    if not found:
        return f"No .md files found in {analysis_dir}."

    output.append("\n--- END OF ANALYSIS DATA ---")
    return "\n".join(output)

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

    Layer 2 mission blueprints live separately, in {seeds_dir}:
    {blueprint_list}
    A heteronym's finished seed.md is written into its characters/<name>/ folder.
    """

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
    if not os.path.exists(CHARACTERS_DIR):
        return "No characters found directory."
    
    chars = [d for d in os.listdir(CHARACTERS_DIR) if os.path.isdir(os.path.join(CHARACTERS_DIR, d))]
    if not chars:
        return "No character folders found in characters/ directory."
    
    return "Available Characters:\n- " + "\n- ".join(chars)

@mcp.tool()
def select_character(name: str) -> str:
    """Sets the current active character for the Perplexity session."""
    global ACTIVE_CHARACTER
    try:
        char_path = safe_character_dir(CHARACTERS_DIR, name)
    except ValueError as e:
        return f"Error: {e}"

    if not os.path.isdir(char_path):
        return f"Error: Character '{name}' not found at {char_path}"

    ACTIVE_CHARACTER = os.path.basename(char_path)
    return f"Active character set to: {name}. Perplexity can now use get_active_identity() to sync."

@mcp.tool()
def get_active_identity() -> str:
    """
    Returns the full content of the active heteronym across all layers.
    EVE should call this to ensure she has the correct soul, seed, and protocol context.
    """
    if not ACTIVE_CHARACTER:
        return "No active heteronym selected. Use select_character(name) first."
    
    char_path = os.path.join(CHARACTERS_DIR, ACTIVE_CHARACTER)
    output = [f"### ACTIVE HETERONYM: {ACTIVE_CHARACTER} ###\n"]
    
    files_to_load = [
        ("skin.md", "LAYER 1: THE SKIN (Identity & Voice)"),
        ("engine.md", "LAYER 1: THE ENGINE (Psychology)"),
        ("big_five.json", "LAYER 1: BIG FIVE SCORES"),
        ("seed.md", "LAYER 2: THE SEED (Mission)"),
        ("operational_rules.md", "PROTOCOL: OPERATIONAL RULES"),
        ("ai_cabinet.yaml", "MANIFEST: AI CABINET")
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

@mcp.tool()
def debug_framework() -> str:
    """View internal system status and exposed tools."""
    tools = [t.name for t in mcp._tool_manager.list_tools()]
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
    - Log File: {LOG_FILE}
    """

@mcp.tool()
def trigger_identity_hydration(hydration_blob: str) -> str:
    """Consolidates architectural layers for the active session."""
    try:
        logger.info(f"CALL: trigger_identity_hydration")
        from scripts.hydrate import parse_hydration_blob
        files = parse_hydration_blob(hydration_blob)

        if not files:
            return "ERROR: No valid file blocks found in blob. Use format: --- FILE: filename ---"

        # The blob is model-generated: derive_heteronym_name sanitizes the name
        # down to a single safe path segment before it reaches the filesystem.
        from naming import derive_heteronym_name
        name = derive_heteronym_name(files.get("skin.md", ""))

        # Call the existing creation logic
        from scripts.create_heteronym import create_heteronym
        import json
        scores = json.loads(files.get("big_five.json", "{}"))

        char_dir = create_heteronym(
            name=name,
            engine_content=files.get("engine.md", ""),
            big_five_scores=scores,
            skin_content=files.get("skin.md", ""),
            seed_content=files.get("seed.md", "")
        )

        # Save Protocol (Layer 3)
        with open(os.path.join(char_dir, "operational_rules.md"), "w", encoding="utf-8") as f:
            f.write(files.get("operational_rules.md", ""))

        return f"SUCCESS: '{os.path.basename(char_dir)}' has been hydrated in the local framework."
    except Exception as e:
        import traceback
        err = f"ERROR in hydration: {str(e)}\n{traceback.format_exc()}"
        logger.error(err)
        return err

if __name__ == "__main__":
    logger.info("Pessoa Framework Bridge Active.")
    mcp.run()
