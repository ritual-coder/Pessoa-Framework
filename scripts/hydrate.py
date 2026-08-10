import os
import sys
import json
import re

# Add parent directories to path for imports
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PESSOA_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PESSOA_ROOT)
sys.path.insert(0, os.path.join(PESSOA_ROOT, "core"))
sys.path.insert(0, SCRIPT_DIR)

from create_heteronym import create_heteronym
from naming import derive_heteronym_name, sanitize_heteronym_name

def parse_hydration_blob(blob_text):
    """
    Parses a single text blob containing multiple markdown-delimited files.
    Looking for blocks like: 
    --- FILE: skin.md ---
    content...
    """
    files = {}
    
    # regex to find blocks delimited by --- FILE: filename ---
    pattern = r"--- FILE: (.*?) ---\n(.*?)(?=\n--- FILE:|$)"
    matches = re.findall(pattern, blob_text, re.DOTALL)
    
    for filename, content in matches:
        files[filename.strip()] = content.strip()
        
    return files

def main():
    print("🌊 Pessoa Framework: Character Hydration Tool")
    print("--------------------------------------------")
    print("Paste your 'Hydration Blob' below (Press Ctrl-D or Ctrl-Z on a new line to finish):")
    
    blob_text = sys.stdin.read()
    
    if not blob_text:
        print("Error: No content provided.")
        return

    files = parse_hydration_blob(blob_text)
    
    if not files:
        print("Error: No valid file blocks found. Use format: --- FILE: filename.md ---")
        return
        
    # Required files for hydration
    required = ["skin.md", "engine.md", "big_five.json", "seed.md", "operational_rules.md"]
    missing = [f for f in required if f not in files]
    
    if missing:
        print(f"Warning: Missing layers: {missing}")

    # Extract name from skin or use default
    default_name = derive_heteronym_name(files.get("skin.md", ""))

    # The blob was read from stdin above, so stdin is at EOF: only prompt when
    # it is an interactive terminal, and accept the derived name otherwise.
    entered = ""
    if sys.stdin.isatty():
        try:
            entered = input(f"Enter Heteronym Name [{default_name}]: ").strip()
        except EOFError:
            pass
    if not entered:
        print(f"Using derived name: {default_name}")

    name = sanitize_heteronym_name(entered) if entered else default_name

    try:
        # Prep scores
        scores = json.loads(files.get("big_five.json", "{}"))

        # Call the existing creation logic
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

        print(f"\n✅ SUCCESS: '{os.path.basename(char_dir)}' has been hydrated "
              "and manifest is active.")

    except Exception as e:
        print(f"❌ HYDRATION FAILED: {str(e)}")

if __name__ == "__main__":
    main()
