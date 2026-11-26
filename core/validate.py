"""Checks a heteronym's layers against the framework's schema.

Hydration blobs are written by different models and edited by hand, so a
character can look complete and still be broken: a facet left null, a scale
that does not match the numbers, nine laws instead of ten, a cabinet calculated
by an older calculus. validate_layers() finds those before they reach a chat.

Each finding is (level, message): "ERROR" when the character is wrong, "WARN"
when it works but deviates from the framework.

Run from the repo root:  python core/validate.py [name ...]   (no name: all)
"""

import json
import os
import re
import sys

import yaml

from converter import PersonalityConverter, ScoreFormatError, _detect_scale

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The current layout of a character folder. A cabinet without the field
# predates versioning and counts as version 1; scripts/migrate.py upgrades it.
SCHEMA_VERSION = 2

REQUIRED = ("skin.md", "engine.md", "big_five.json", "seed.md", "operational_rules.md")

# The ranges docs/CALCULUS.md §3 fits each parameter to.
PARAM_RANGES = {
    "temperature": (0.30, 0.90),
    "top_p": (0.66, 0.90),
    "frequency_penalty": (0.18, 0.50),
    "max_tokens": (512, 4096),
    "confidence_threshold": (0.72, 0.90),
}

SCALE_RANGES = {"0-1": (0.0, 1.0), "1-5": (1.0, 5.0), "0-100": (0.0, 100.0)}

# The 10 Core Laws of templates/eve_master_prompt.md, by their key word.
LAWS = ("register", "shadow", "tool governance", "privacy", "non-harm",
        "contextual loyalty", "persistence", "threshold", "tool honesty", "evolution")


# IPIP-NEO spellings of the same facets (Stack_And_Dagger uses them): another
# name for a facet, not a missing one.
FACET_ALIASES = {
    "artistic_interest": "artistic_interests", "self_efficacy": "competence",
    "orderliness": "order", "cautiousness": "deliberation", "friendliness": "warmth",
    "activity_level": "activity", "cheerfulness": "positive_emotionality",
}


def _facet_schema():
    with open(os.path.join(ROOT, "templates", "big_five.json"), encoding="utf-8") as f:
        return {t: list(fs) for t, fs in json.load(f)["facets"].items()}


def _given_facets(data, trait):
    given = (data.get("facets") or {}).get(trait) if isinstance(data.get("facets"), dict) else None
    return {FACET_ALIASES.get(k, k): v for k, v in given.items()} if isinstance(given, dict) else {}


def derive_summary(data):
    """Each trait's summary as the mean of its facets, when all of its facets are scored.

    The model writes both blocks, and they drift apart (a summary of 0.18 over
    facets averaging 0.37): the facets are the detailed judgement, so they win.
    Returns (new data, [(trait, written, calculated)] for every summary changed);
    the written values are kept under "summary_written". A trait with an
    unscored facet keeps its written summary.
    """
    out = json.loads(json.dumps(data))
    summary = out.get("summary") if isinstance(out.get("summary"), dict) else {}
    changes = []
    for trait, names in _facet_schema().items():
        given = _given_facets(out, trait)
        values = [given.get(n) for n in names]
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
            continue
        mean = round(sum(values) / len(values), 3)
        written = summary.get(trait)
        if written != mean:
            changes.append((trait, written, mean))
            summary[trait] = mean
    if changes:
        out["summary"] = summary
        out["summary_written"] = {t: w for t, w, _ in changes}
    return out, changes


def evidence_quotes(data):
    """[(trait.facet, quote)] for every quote in the evidence block."""
    ev = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
    return [(f"{t}.{f}", e["quote"]) for t, facets in ev.items() if isinstance(facets, dict)
            for f, e in facets.items() if isinstance(e, dict) and str(e.get("quote") or "").strip()]


def check_evidence_quotes(data, source_text):
    """(verified count, [(trait.facet, quote) not found in source_text])."""
    from digest import normalize
    haystack = normalize(source_text)
    quotes = evidence_quotes(data)
    missing = [(w, q) for w, q in quotes if normalize(q) not in haystack]
    return len(quotes) - len(missing), missing


def _check_evidence(data):
    """Every facet should carry a one-line reason: a score without one cannot be checked."""
    ev = data.get("evidence")
    if not isinstance(ev, dict):
        return [("WARN", "big_five.json has no 'evidence' block: the 30 scores carry no reasons "
                 "(see templates/big_five.json).")]
    missing = []
    for trait, names in _facet_schema().items():
        given = {FACET_ALIASES.get(k, k): v for k, v in (ev.get(trait) or {}).items()} \
            if isinstance(ev.get(trait), dict) else {}
        for n in names:
            e = given.get(n)
            if not (isinstance(e, dict) and str(e.get("reason") or "").strip()):
                missing.append(f"{trait}.{n}")
    if missing:
        return [("WARN", f"{len(missing)} of 30 facets have no reason in 'evidence': "
                 + ", ".join(missing[:6]) + (" ..." if len(missing) > 6 else ""))]
    return []


def _check_big_five(text):
    out = []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return [("ERROR", f"big_five.json is not valid JSON: {e}")], None
    try:
        calculus = PersonalityConverter.analyze(data)
    except ScoreFormatError as e:
        return [("ERROR", f"big_five.json: {e}")], None

    meta = calculus["meta"]
    if not meta["scale_declared"]:
        out.append(("WARN", f"big_five.json declares no 'scale'; read as {meta['scale']}."))
    if meta["missing_traits"]:
        out.append(("ERROR", "big_five.json has no score for "
                    f"{', '.join(meta['missing_traits'])}; neutral 3.0 was used."))
    scale = meta["scale"]
    low, high = SCALE_RANGES[scale]

    facets = data.get("facets") if isinstance(data.get("facets"), dict) else {}
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    for value_name, value in list(summary.items()):
        if isinstance(value, (int, float)) and not low <= value <= high:
            out.append(("ERROR", f"summary.{value_name} = {value} is outside the {scale} scale."))
    missing, bad = [], []
    for trait, names in _facet_schema().items():
        given = facets.get(trait) if isinstance(facets.get(trait), dict) else {}
        given = {FACET_ALIASES.get(k, k): v for k, v in given.items()}
        numbers = []
        for name in names:
            v = given.get(name)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                missing.append(f"{trait}.{name}")
            elif not low <= v <= high:
                bad.append(f"{trait}.{name}={v}")
            else:
                numbers.append(v)
        stated = summary.get(trait)
        # A stated trait far from its own facets means one of the two is wrong.
        if numbers and isinstance(stated, (int, float)):
            mean = sum(numbers) / len(numbers)
            if abs(mean - stated) > 0.15 * (high - low):
                out.append(("WARN", f"{trait}: summary {stated} but facet mean {mean:.2f}."))
    if missing:
        out.append(("WARN", f"{len(missing)} of 30 facets unscored: "
                    + ", ".join(missing[:6]) + (" ..." if len(missing) > 6 else "")))
    if bad:
        out.append(("ERROR", f"facets outside the {scale} scale: " + ", ".join(bad[:6])))
    # The values must look like the declared scale: 0-1 data declared as 1-5 is
    # read as nearly-all "Very Low", silently.
    values = [v for v in meta["raw_scores"].values()]
    if meta["scale_declared"] and _detect_scale(values) != scale and scale != "0-100":
        out.append(("WARN", f"scores look like the {_detect_scale(values)} scale "
                    f"but big_five.json declares {scale}."))
    out += _check_evidence(data)
    return out, calculus


def _check_rules(text):
    out = []
    lower = text.lower()
    numbered = {int(n) for n in re.findall(r"^\s*(?:#+\s*|\*\*)?(\d+)[.)]", text, re.M)}
    named = [law for law in LAWS if law in lower]
    if not {*range(1, 11)} <= numbered and len(named) < 10:
        out.append(("WARN", "operational_rules.md does not carry the 10 Core Laws "
                    f"({len(named)} named, numbered items {sorted(numbered)[:12] or 'none'})."))
    for section in ("can do", "cannot do"):
        if section not in lower:
            out.append(("WARN", f"operational_rules.md has no '{section.upper()}' section."))
    return out


def _check_cabinet(text, calculus):
    out = []
    try:
        cabinet = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        return [("ERROR", f"ai_cabinet.yaml is not valid YAML: {e}")]
    version = cabinet.get("schema_version", 1)
    if version < SCHEMA_VERSION:
        out.append(("WARN", f"schema_version {version} (current {SCHEMA_VERSION}); "
                    "preview the upgrade with: python scripts/migrate.py <name>"))
    params = cabinet.get("parameters") or {}
    for key, (low, high) in PARAM_RANGES.items():
        v = params.get(key)
        if not isinstance(v, (int, float)):
            out.append(("ERROR", f"ai_cabinet.yaml has no numeric {key}."))
        elif not low <= v <= high:
            out.append(("ERROR", f"{key} = {v} is outside its calculus range {low}-{high}."))
    if calculus and params and params != calculus["parameters"]:
        drift = [k for k in calculus["parameters"] if params.get(k) != calculus["parameters"][k]]
        out.append(("WARN", "ai_cabinet.yaml differs from what big_five.json calculates now "
                    f"({', '.join(drift)}): edited by hand, or made by an older calculus."))
    return out


def validate_layers(files):
    """files: {filename: text}. Returns [(level, message)]; empty means clean."""
    out = [("ERROR", f"missing layer: {f}") for f in REQUIRED if not files.get(f, "").strip()]
    calculus = None
    if files.get("big_five.json"):
        found, calculus = _check_big_five(files["big_five.json"])
        out += found
    if files.get("operational_rules.md"):
        out += _check_rules(files["operational_rules.md"])
    if files.get("ai_cabinet.yaml"):
        out += _check_cabinet(files["ai_cabinet.yaml"], calculus)
    return out


def validate_dir(char_dir):
    files = {}
    for name in REQUIRED + ("ai_cabinet.yaml", "ACTIVATION_PROMPT.md"):
        path = os.path.join(char_dir, name)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                files[name] = f.read()
    out = validate_layers(files)
    if "ai_cabinet.yaml" not in files:
        out.append(("ERROR", "missing ai_cabinet.yaml (re-hydrate to calculate it)"))
    if "ACTIVATION_PROMPT.md" not in files:
        out.append(("ERROR", "missing ACTIVATION_PROMPT.md"))
    return out


def format_findings(name, findings):
    if not findings:
        return f"{name}: valid."
    return f"{name}:\n" + "\n".join(f"  {level}: {msg}" for level, msg in findings)


def _self_test():
    with open(os.path.join(ROOT, "templates", "big_five.json"), encoding="utf-8") as f:
        sheet = json.load(f)
    sheet["evidence"] = {}
    for t in sheet["summary"]:
        sheet["summary"][t] = 0.5
        sheet["facets"][t] = {k: 0.5 for k in sheet["facets"][t]}
        sheet["evidence"][t] = {k: {"reason": "r"} for k in sheet["facets"][t]}
    laws = "\n".join(f"{i}. Law of {law.title()}" for i, law in enumerate(LAWS, 1))
    files = {f: "x" for f in REQUIRED}
    files["big_five.json"] = json.dumps(sheet)
    files["operational_rules.md"] = f"## CAN DO\n- a\n## CANNOT DO\n- b\n{laws}\n"
    assert validate_layers(files) == [], validate_layers(files)

    sheet["facets"]["openness"]["imagination"] = None
    sheet["facets"]["neuroticism"]["anger"] = 7
    files["big_five.json"] = json.dumps(sheet)
    files["operational_rules.md"] = "1. one\n2. two\n"
    msgs = " | ".join(m for _, m in validate_layers(files))
    for expect in ("1 of 30 facets unscored: openness.imagination", "neuroticism.anger=7",
                   "10 Core Laws", "'CAN DO'"):
        assert expect in msgs, (expect, msgs)

    sheet["facets"]["openness"]["imagination"] = 0.5
    sheet["facets"]["neuroticism"]["anger"] = 0.5
    sheet["evidence"]["openness"]["imagination"] = {"reason": " "}
    msgs = " | ".join(m for _, m in _check_big_five(json.dumps(sheet))[0])
    assert "1 of 30 facets have no reason in 'evidence': openness.imagination" in msgs, msgs
    del sheet["evidence"]
    assert "no 'evidence' block" in " | ".join(m for _, m in _check_big_five(json.dumps(sheet))[0])

    # Summaries follow complete facets; a trait with a gap keeps its written one.
    sheet["summary"]["extraversion"] = 0.18
    sheet["facets"]["extraversion"] = dict(sheet["facets"]["extraversion"], warmth=0.8)
    sheet["summary"]["openness"] = 0.9
    sheet["facets"]["openness"]["imagination"] = None
    derived, changes = derive_summary(sheet)
    assert changes == [("extraversion", 0.18, 0.55)], changes
    assert derived["summary"]["extraversion"] == 0.55 and derived["summary"]["openness"] == 0.9
    assert derived["summary_written"] == {"extraversion": 0.18} and sheet["summary"]["extraversion"] == 0.18
    assert derive_summary(derived)[1] == []
    sheet["evidence"] = {"openness": {"intellect": {"reason": "r", "quote": "Reads   the methodology first"}},
                         "neuroticism": {"anger": {"reason": "r", "quote": "never shouts"}}}
    assert check_evidence_quotes(sheet, "She reads the methodology first.") == (1, [("neuroticism.anger", "never shouts")])

    calc = PersonalityConverter.analyze({"scale": "1-5", "O": 3, "C": 3, "E": 3, "A": 3, "N": 3})
    old = yaml.safe_dump({"parameters": dict(calc["parameters"], temperature=0.95)})
    msgs = " | ".join(m for _, m in _check_cabinet(old, calc))
    assert "schema_version 1" in msgs and "temperature = 0.95 is outside" in msgs, msgs
    assert "differs from what big_five.json calculates" in msgs, msgs
    return True


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        print("validate self-test:", _self_test())
        sys.exit(0)
    chars = os.path.join(ROOT, "characters")
    names = sys.argv[1:] or sorted(d for d in os.listdir(chars) if os.path.isdir(os.path.join(chars, d)))
    errors = 0
    for n in names:
        findings = validate_dir(os.path.join(chars, n))
        errors += sum(1 for level, _ in findings if level == "ERROR")
        print(format_findings(n, findings))
    sys.exit(1 if errors else 0)
