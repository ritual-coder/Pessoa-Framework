import os
import sys
import json
import re
import yaml

# Add parent directories to path for imports
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PESSOA_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PESSOA_ROOT)
sys.path.insert(0, os.path.join(PESSOA_ROOT, "core"))
sys.path.insert(0, SCRIPT_DIR)

from create_heteronym import create_heteronym

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
    name_match = re.search(r"# (?:The Skin: )?(.*)", files.get("skin.md", ""))
    default_name = name_match.group(1).strip().replace(" ", "_") if name_match else "New_Heteronym"
    
    name = input(f"Enter Heteronym Name [{default_name}]: ").strip() or default_name

    try:
        # Prep scores
        scores = json.loads(files.get("big_five.json", "{}"))
        
        # Call the existing creation logic
        create_heteronym(
            name=name,
            engine_content=files.get("engine.md", ""),
            big_five_scores=scores,
            skin_content=files.get("skin.md", ""),
            seed_content=files.get("seed.md", "")
        )
        
        # Save Protocol (Layer 3)
        char_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "characters", name)
        with open(os.path.join(char_dir, "operational_rules.md"), "w") as f:
            f.write(files.get("operational_rules.md", ""))
            
        print(f"\n✅ SUCCESS: '{name}' has been hydrated and manifest is active.")
        
    except Exception as e:
        print(f"❌ HYDRATION FAILED: {str(e)}")

if __name__ == "__main__":
    main()
