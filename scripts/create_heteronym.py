import os
import sys
import json

# The core modules live in ../core. Callers that import this module normally set
# the path up first, but doing it here too means the script also runs directly.
_CORE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"
)
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

import yaml  # noqa: E402
from activation import build_activation_prompt  # noqa: E402
from converter import NEUTRAL, PersonalityConverter  # noqa: E402
from naming import safe_character_dir, sanitize_heteronym_name  # noqa: E402

def create_heteronym(name, engine_content, big_five_scores, skin_content=None,
                     seed_content=None, rules_content=None, activation_content=None):
    """
    Creates a new heteronym folder and populates it with Pessoa Framework files.
    
    Args:
        name (str): Heteronym name.
        engine_content (str): Layer 1: The Engine (Psychological structure).
        big_five_scores (dict): Dictionary of facet scores for Layer 1.
        skin_content (str): Layer 1: The Skin (Biography and Voice).
        seed_content (str): Layer 2: The Seed (Mission and Expertise).
    """
    # Run the calculus first: a profile we cannot score should fail before
    # anything is written to disk, not halfway through.
    calculus = PersonalityConverter.analyze(big_five_scores)
    params = calculus["parameters"]

    # The name may come from LLM output or a pasted blob: reduce it to a single
    # safe path segment, then confirm it lands inside characters/ before writing.
    name = sanitize_heteronym_name(name)

    # Determine target directory relative to this script
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    char_dir = safe_character_dir(os.path.join(BASE_DIR, "characters"), name)
    os.makedirs(char_dir, exist_ok=True)

    # 1. Save Engine (Layer 1)
    with open(os.path.join(char_dir, "engine.md"), "w") as f:
        f.write(engine_content)

    # 2. Save Big Five Scores (Layer 1)
    with open(os.path.join(char_dir, "big_five.json"), "w") as f:
        json.dump(big_five_scores, f, indent=2)

    # 3. Save AI Cabinet (Layer 3)
    # Build the 5-Pillar System Prompt Architecture
    ai_cabinet = {
        "name": name,
        "parameters": params,
        # The 1-5 scores the parameters were actually derived from, so a
        # cabinet can be audited against the profile that produced it.
        "big_five_normalized": calculus["scores"],
        "calculus": calculus["meta"],
        "system_prompt_architecture": {
            "identity_anchor": "Distilled from engine.md and skin.md",
            "personality_profile": "Calculated from Big Five scores",
            "communication_style": "Defined by Skin voice patterns",
            "capabilities_expertise": "Defined by the Seed",
            "boundaries_constraints": "Defined by the Shadow/Fear in engine.md"
        },
        "status": "ready"
    }
    
    with open(os.path.join(char_dir, "ai_cabinet.yaml"), "w") as f:
        yaml.dump(ai_cabinet, f, sort_keys=False)
        
    # 4. Save Skin (Layer 1)
    if skin_content:
        with open(os.path.join(char_dir, "skin.md"), "w") as f:
            f.write(skin_content)

    # 5. Save Seed (Layer 2)
    if seed_content:
        with open(os.path.join(char_dir, "seed.md"), "w") as f:
            f.write(seed_content)

    # 6. Save Protocol (Layer 3) when supplied, so the activation prompt below
    # can distil it. Callers that write it themselves may omit it.
    if rules_content:
        with open(os.path.join(char_dir, "operational_rules.md"), "w") as f:
            f.write(rules_content)

    # 7. Activation prompt: the pasteable artifact. An authored version always
    # wins; otherwise compose one from the layers so no character ships without.
    if not activation_content:
        activation_content = build_activation_prompt(
            name=name,
            skin=skin_content or "",
            engine=engine_content or "",
            seed=seed_content or "",
            rules=rules_content or "",
            scores=calculus["scores"],
            parameters=params,
        )
    with open(os.path.join(char_dir, "ACTIVATION_PROMPT.md"), "w") as f:
        f.write(activation_content)

    print(f"Heteronym '{name}' created successfully in {char_dir}")
    meta = calculus["meta"]
    scale_note = "declared" if meta["scale_declared"] else "inferred"
    print(f"Big Five ({meta['scale']} scale, {scale_note}) -> {calculus['scores']}")
    if meta["missing_traits"]:
        print(f"WARNING: no score found for {', '.join(meta['missing_traits'])}; "
              f"used neutral {NEUTRAL}. Check big_five.json.")
    print(f"Calculated Parameters: {params}")
    return char_dir

if __name__ == "__main__":
    # This module is normally imported. The --demo flag writes one throwaway
    # character as a smoke test; creating it as a side effect of running the
    # script with no arguments was surprising, so it is now opt-in.
    if "--demo" in sys.argv[1:]:
        demo_scores = {"O": 2.3, "C": 4.7, "E": 3.0, "A": 1.8, "N": 1.2}
        create_heteronym(
            name="Strategist_Demo",
            engine_content="# The Strategist Engine\n...",
            big_five_scores=demo_scores,
            skin_content="# The Strategist Skin\n...",
            seed_content="# The Strategist Seed (Mission: Security Analyst)\n...",
            rules_content="## Law of Deliberation\nMeasure before cutting.\n",
        )
    else:
        print("This module is imported by scripts/hydrate.py and the MCP server.")
        print("To create a character from a hydration blob:")
        print("    python scripts/hydrate.py < blob.txt")
        print("To write a throwaway demo character:")
        print("    python scripts/create_heteronym.py --demo")

