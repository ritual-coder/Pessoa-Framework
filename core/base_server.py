import os
import sys
import logging
from mcp.server.fastmcp import FastMCP

# Ensure we can import from core and scripts
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT_DIR)
sys.path.append(os.path.join(ROOT_DIR, "core"))

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
ANALYSIS_DIR = os.path.join(ROOT_DIR, "..", "BU", "Internet History Analysis")

# State management
# We'll use a simple in-memory state. In a persistent system, this could be a file.
ACTIVE_CHARACTER = None

# Initialize MCP Server
mcp = FastMCP("G-MPF Bridge")

@mcp.tool()
def fetch_analysis_data() -> str:
    """Diagnostic scan of digital footprint archives."""
    logger.info("CALL: fetch_analysis_data")
    
    if not os.path.exists(ANALYSIS_DIR):
        return f"Error: Analysis directory not found at {ANALYSIS_DIR}"

    output = []
    output.append(f"--- START OF ANALYSIS DATA FROM {ANALYSIS_DIR} ---\n")

    for root, dirs, files in os.walk(ANALYSIS_DIR):
        for file in files:
            if file.endswith(".md"):
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, ANALYSIS_DIR)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                        output.append(f"### FILE: {rel_path}\n{content}\n")
                except Exception as e:
                    output.append(f"### ERROR READING {rel_path}: {str(e)}\n")

    output.append("\n--- END OF ANALYSIS DATA ---")
    return "\n".join(output)

@mcp.tool()
def get_framework_templates() -> str:
    """
    Returns the paths and purpose of the Pessoa Framework templates.
    """
    templates_dir = os.path.join(ROOT_DIR, "templates")
    return f"""
    Templates Location: {templates_dir}
    - skin.md: Layer 1 - The biography and voice (The Appearance).
    - engine.md: Layer 1 - The psychological structure (The Depth).
    - big_five.json: Layer 1 - Facet scoring schema.
    - seed.md: Layer 2 - The mission and expertise (The Job).
    - operational_rules.md: Layer 3 - Behavioral constraints (The Protocol).
    - ai_cabinet.yaml: Final LLM behavioral parameters and manifest.
    """

@mcp.tool()
def get_creation_guide() -> str:
    """
    Returns the master instructions for heteronym creation.
    Reference this to understand the Step-by-Step Lifecycle (Soul -> Seed -> Protocol).
    """
    guide_path = os.path.join(ROOT_DIR, "..", "Analysis_and_Design", "PESSOA_LIFECYCLE_GUIDE.md")
    if os.path.exists(guide_path):
        with open(guide_path, "r") as f:
            return f.read()
    return "Lifecycle guide not found."

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
    char_path = os.path.join(CHARACTERS_DIR, name)
    
    if not os.path.exists(char_path):
        return f"Error: Character '{name}' not found at {char_path}"
    
    ACTIVE_CHARACTER = name
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
            with open(file_path, "r") as f:
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
    return f"""
    Internal Status:
    - Root Dir: {ROOT_DIR}
    - Characters Dir: {CHARACTERS_DIR}
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
            
        # Extract name
        import re
        name_match = re.search(r"# (?:The Skin: )?(.*)", files.get("skin.md", ""))
        name = name_match.group(1).strip().replace(" ", "_") if name_match else "New_Heteronym"
        
        # Call the existing creation logic
        from scripts.create_heteronym import create_heteronym
        import json
        scores = json.loads(files.get("big_five.json", "{}"))
        
        create_heteronym(
            name=name,
            engine_content=files.get("engine.md", ""),
            big_five_scores=scores,
            skin_content=files.get("skin.md", ""),
            seed_content=files.get("seed.md", "")
        )
        
        # Save Protocol (Layer 3)
        char_dir = os.path.join(CHARACTERS_DIR, name)
        os.makedirs(char_dir, exist_ok=True)
        with open(os.path.join(char_dir, "operational_rules.md"), "w") as f:
            f.write(files.get("operational_rules.md", ""))
            
        return f"SUCCESS: '{name}' has been hydrated in the local framework."
    except Exception as e:
        import traceback
        err = f"ERROR in hydration: {str(e)}\n{traceback.format_exc()}"
        logger.error(err)
        return err

if __name__ == "__main__":
    logger.info("Pessoa Framework Bridge Active.")
    mcp.run()
