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

import argparse
import subprocess

from create_heteronym import create_heteronym
from converter import PersonalityConverter
from digest import unfence as _unfence
from naming import derive_heteronym_name, safe_character_dir, sanitize_heteronym_name
from validate import check_evidence_quotes, derive_summary, format_findings, validate_dir, validate_layers

def parse_hydration_blob(blob_text):
    """
    Parses a single text blob containing multiple markdown-delimited files.
    Looking for blocks like: 
    --- FILE: skin.md ---
    content...
    """
    files = {}

    # Blobs saved on Windows end lines in \r\n, which the pattern below would
    # never match.
    blob_text = blob_text.replace("\r\n", "\n")

    # regex to find blocks delimited by --- FILE: filename ---
    pattern = r"--- FILE: (.*?) ---\n(.*?)(?=\n--- FILE:|$)"
    matches = re.findall(pattern, blob_text, re.DOTALL)

    for filename, content in matches:
        files[filename.strip()] = content.strip()

    return files


# The layers a full heteronym carries; ACTIVATION_PROMPT.md is optional.
REQUIRED = ["skin.md", "engine.md", "big_five.json", "seed.md", "operational_rules.md"]
OPTIONAL = ["ACTIVATION_PROMPT.md"]


def _report_scores(scores, source_text):
    """Set summaries from complete facets and check evidence quotes; print what changed."""
    if not isinstance(scores, dict):
        return scores
    scores, changes = derive_summary(scores)
    for trait, written, calculated in changes:
        print(f"Summary set from facets: {trait} {written} -> {calculated} (mean of its 6 facets)")
    if source_text:
        verified, missing = check_evidence_quotes(scores, source_text)
        if verified or missing:
            print(f"Evidence quotes found in the source: {verified}/{verified + len(missing)}")
        for where, quote in missing[:15]:
            print(f"WARNING: evidence quote not in the source: {where}: {quote!r}")
        if len(missing) > 15:
            print(f"WARNING: ...and {len(missing) - 15} more quotes not in the source")
    return scores


def hydrate_files(files, name=None, dry_run=False, source_text=None):
    """Create a heteronym from parsed blob files. Returns its directory.

    Shared by the CLI below and the MCP server, so both build characters the
    same way. Without a name, one is derived from the Skin. Warnings and the
    validation report are printed, and the MCP server returns them to the model.
    With dry_run, nothing is written: the report says what would be.

    Each trait summary is set to the mean of its facets when all six are
    scored (see validate.derive_summary). With source_text (the Prima Materia),
    the evidence quotes in big_five.json are checked against it.
    """
    missing = [f for f in REQUIRED if f not in files]
    if missing:
        print(f"WARNING: missing layers: {', '.join(missing)}")
    ignored = sorted(set(files) - set(REQUIRED + OPTIONAL))
    if ignored:
        print(f"WARNING: ignored files: {', '.join(ignored)} "
              "(ai_cabinet.yaml is always calculated by the framework)")
    name = sanitize_heteronym_name(name) if name else derive_heteronym_name(
        files.get("skin.md", "")
    )
    scores = _report_scores(json.loads(_unfence(files.get("big_five.json", "{}"))), source_text)
    if dry_run:
        char_dir = safe_character_dir(os.path.join(PESSOA_ROOT, "characters"), name)
        calculus = PersonalityConverter.analyze(scores)  # raises like a real run would
        written = [f for f in REQUIRED + OPTIONAL if files.get(f)] + ["ai_cabinet.yaml"]
        if "ACTIVATION_PROMPT.md" not in written:
            written.append("ACTIVATION_PROMPT.md (composed from the layers)")
        print(f"DRY RUN: nothing written. Would create {char_dir}")
        if os.path.exists(char_dir):
            print(f"WARNING: {name} already exists; a real run would refuse it.")
        print("Files: " + ", ".join(written))
        print(f"Calculated Parameters: {calculus['parameters']}")
        layers = dict(files, **{"big_five.json": json.dumps(scores) if files.get("big_five.json") else ""})
        print(format_findings("Validation", validate_layers(layers)))
        return char_dir
    # ACTIVATION_PROMPT.md is optional: one is composed from the layers when
    # the blob does not carry an authored version.
    char_dir = create_heteronym(
        name=name,
        engine_content=files.get("engine.md", ""),
        big_five_scores=scores,
        skin_content=files.get("skin.md", ""),
        seed_content=files.get("seed.md", ""),
        rules_content=files.get("operational_rules.md", ""),
        activation_content=files.get("ACTIVATION_PROMPT.md", ""),
    )
    print(format_findings("Validation", validate_dir(char_dir)))
    return char_dir


def read_folder(path):
    """Layer files from a folder (e.g. a character exported elsewhere) as a files dict."""
    files = {}
    for name in REQUIRED + OPTIONAL:
        p = os.path.join(path, name)
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                files[name] = f.read().strip()
    return files


def read_source_text(path):
    """The text of a Prima Materia file, or of every readable file in a folder."""
    from prima import read_sources
    from sources import read_source
    if os.path.isdir(path):
        return "\n\n".join(t for _, t, _ in read_sources(path)[0] if t)
    return read_source(path)


def read_clipboard():
    """The clipboard as text: pbpaste (macOS), PowerShell (Windows), xclip/wl-paste (Linux)."""
    for cmd in (["pbpaste"], ["powershell", "-command", "Get-Clipboard"],
                ["wl-paste"], ["xclip", "-selection", "clipboard", "-o"]):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            continue
    sys.exit("Error: no clipboard tool found (pbpaste, PowerShell, wl-paste or xclip).")


def main():
    ap = argparse.ArgumentParser(description="Create a heteronym from a hydration blob.")
    ap.add_argument("source", nargs="?", default="-",
                    help="blob file, a folder of layer files, or - for stdin (default)")
    ap.add_argument("--clipboard", action="store_true", help="read the blob from the clipboard")
    ap.add_argument("--name", help="folder name (default: derived from the Skin)")
    ap.add_argument("--dry-run", action="store_true", help="show what would be written; write nothing")
    ap.add_argument("--source", dest="prima_source", metavar="PATH",
                    help="Prima Materia file or folder: check the evidence quotes against it")
    args = ap.parse_args()

    print("🌊 Pessoa Framework: Character Hydration Tool")
    print("--------------------------------------------")
    if args.clipboard:
        files = parse_hydration_blob(read_clipboard())
    elif args.source != "-" and os.path.isdir(args.source):
        files = read_folder(args.source)
    elif args.source != "-":
        with open(args.source, encoding="utf-8") as f:
            files = parse_hydration_blob(f.read())
    else:
        print("Paste your 'Hydration Blob' below (Press Ctrl-D or Ctrl-Z on a new line to finish):")
        blob_text = sys.stdin.read()
        if not blob_text:
            sys.exit("Error: No content provided.")
        files = parse_hydration_blob(blob_text)

    if not files:
        sys.exit("Error: No valid file blocks found. Use format: --- FILE: filename.md ---")

    # Extract name from skin or use default
    default_name = derive_heteronym_name(files.get("skin.md", ""))

    # A piped blob leaves stdin at EOF: only prompt when stdin is an
    # interactive terminal and no name was given, and accept the derived name otherwise.
    entered = args.name or ""
    if not entered and args.source == "-" and not args.clipboard and sys.stdin.isatty():
        try:
            entered = input(f"Enter Heteronym Name [{default_name}]: ").strip()
        except EOFError:
            pass
    if not entered:
        print(f"Using derived name: {default_name}")

    source_text = read_source_text(args.prima_source) if args.prima_source else None
    try:
        char_dir = hydrate_files(files, entered or default_name, dry_run=args.dry_run,
                                 source_text=source_text)
        if not args.dry_run:
            print(f"\n✅ SUCCESS: '{os.path.basename(char_dir)}' has been hydrated "
                  "and manifest is active.")

    except Exception as e:
        sys.exit(f"❌ HYDRATION FAILED: {e}")

if __name__ == "__main__":
    main()
