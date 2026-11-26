"""Upgrade characters to the current schema, showing the change first.

Only ai_cabinet.yaml is migrated: it is the one file the framework calculates,
so it can be rebuilt from big_five.json without touching anything an author
wrote. Version 1 -> 2 adds schema_version and recalculates the parameters with
the current calculus (docs/CALCULUS.md).

    python scripts/migrate.py                 # preview every character (writes nothing)
    python scripts/migrate.py Stack_And_Dagger --apply

--apply keeps the old file as ai_cabinet.yaml.bak.
"""

import argparse
import difflib
import json
import os
import shutil
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
sys.path[:0] = [os.path.join(ROOT, "core"), SCRIPT_DIR]

import yaml  # noqa: E402
from converter import PersonalityConverter  # noqa: E402
from create_heteronym import build_cabinet  # noqa: E402
from digest import unfence  # noqa: E402

CHARACTERS = os.path.join(ROOT, "characters")


def plan(char_dir):
    """(old text, new text) of ai_cabinet.yaml for one character."""
    cabinet_path = os.path.join(char_dir, "ai_cabinet.yaml")
    old = ""
    if os.path.isfile(cabinet_path):
        with open(cabinet_path, encoding="utf-8") as f:
            old = f.read()
    with open(os.path.join(char_dir, "big_five.json"), encoding="utf-8") as f:
        scores = json.loads(unfence(f.read()))
    name = (yaml.safe_load(old) or {}).get("name") if old else None
    new = yaml.dump(build_cabinet(name or os.path.basename(char_dir),
                                  PersonalityConverter.analyze(scores)), sort_keys=False)
    return old, new


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("names", nargs="*", help="character folders (default: all)")
    ap.add_argument("--apply", action="store_true", help="write the change (default: preview)")
    args = ap.parse_args(argv)
    names = args.names or sorted(d for d in os.listdir(CHARACTERS)
                                 if os.path.isdir(os.path.join(CHARACTERS, d)))
    changed = 0
    for n in names:
        char_dir = os.path.join(CHARACTERS, n)
        try:
            old, new = plan(char_dir)
        except (OSError, ValueError) as e:
            print(f"{n}: SKIPPED ({e})")
            continue
        if old == new:
            print(f"{n}: up to date.")
            continue
        changed += 1
        print("".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                           f"{n}/ai_cabinet.yaml", f"{n}/ai_cabinet.yaml (migrated)")))
        if args.apply:
            path = os.path.join(char_dir, "ai_cabinet.yaml")
            if os.path.exists(path):
                shutil.copy2(path, path + ".bak")
            with open(path, "w", encoding="utf-8") as f:
                f.write(new)
            print(f"{n}: migrated (old file kept as ai_cabinet.yaml.bak).")
    if changed and not args.apply:
        print(f"\n{changed} character(s) would change. Nothing was written; re-run with --apply.")
    return changed


if __name__ == "__main__":
    main()
