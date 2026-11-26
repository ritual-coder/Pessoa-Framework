"""Layer 2 mission blueprints (Seeds/*_blueprint.md) and their composition.

Shared by the MCP server's compose_seeds tool and scripts/studio.py, so a
composite blueprint reads the same wherever it is built.
"""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEEDS_DIR = os.path.join(ROOT, "Seeds")


def available():
    if not os.path.isdir(SEEDS_DIR):
        return []
    return sorted(f[:-len("_blueprint.md")] for f in os.listdir(SEEDS_DIR) if f.endswith("_blueprint.md"))


def compose(names):
    """One blueprint text from several, primary first. Raises ValueError for unknown names."""
    names = [n.strip().removesuffix(".md").removesuffix("_blueprint") for n in names if n.strip()]
    unknown = [n for n in names if n not in available()]
    if unknown or not names:
        raise ValueError(f"unknown blueprint(s) {unknown or names!r}. Available: {', '.join(available())}")
    parts = [f"# Composite Blueprint: {' + '.join(n.replace('_', ' ').title() for n in names)}\n\n"
             f"Primary: {names[0]}. Fill every section of the primary. Secondary blueprints "
             f"({', '.join(names[1:]) or 'none'}) add expertise and lenses, not new values: where "
             "they conflict with the primary or with the Soul, the primary and the Soul win. Write one "
             "Manifesto that holds all of them in the character's own voice."]
    for i, n in enumerate(names):
        with open(os.path.join(SEEDS_DIR, f"{n}_blueprint.md"), encoding="utf-8") as f:
            body = f.read().strip()
        parts.append(f"## {'PRIMARY' if i == 0 else 'SECONDARY'}: {n}\n" + re.sub(r"^#", "###", body, flags=re.M))
    return "\n\n".join(parts)
