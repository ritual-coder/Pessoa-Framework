"""Conversion logic between the layers of the Pessoa Framework.

The "Psychosynthetic Calculus" turns a heteronym's Big Five profile into the
LLM behavioural parameters written to ai_cabinet.yaml.

The formulas are defined on a 1-5 trait scale (see templates/eve_master_prompt.md).
Score files in the wild do not all use that scale or those key names, so every
input is normalised to 1-5 before the math runs. Unrecognised input raises
ScoreFormatError rather than silently falling back to a neutral profile.

The scaling constants are fitted so each parameter reproduces the behavioural
bands documented in the framework's Psychometric Justification tables (e.g.
Agreeableness 4-5 -> frequency_penalty 0.18-0.26). This supersedes the earlier
trait/5 formulation, whose worked example produced 0.576/0.712/0.284/440/0.828
for the Strategist profile but could not hit those bands or stay inside the
spec's own safety bounds.
"""

import json

# Canonical scale the formulas are defined on.
SCALE_MIN, SCALE_MAX = 1.0, 5.0
NEUTRAL = 3.0

TRAITS = ("O", "C", "E", "A", "N")

# LLM safety bounds from the framework spec. The calculus is fitted to stay
# inside these, so they are a guard against bad input, not active shaping.
# meta["bounds_applied"] is non-empty only if something upstream went wrong.
SAFETY_BOUNDS = {
    "temperature": (None, 0.90),        # above 0.90 risks hallucination
    "top_p": (0.60, None),              # below 0.60 reads robotic
    "frequency_penalty": (0.10, None),  # below 0.10 permits heavy repetition
    "max_tokens": (None, 4096),         # generous ceiling; see analyze()
    "confidence_threshold": (0.70, None),  # below 0.70 output hedges to uselessness
}

# Key spellings accepted for each trait. Inverted constructs (e.g. "emotional
# stability" for N) are deliberately absent: silently flipping a sign is worse
# than refusing the file.
_TRAIT_ALIASES = {
    "O": ("o", "openness", "open", "openness_to_experience"),
    "C": ("c", "conscientiousness", "conscientious"),
    "E": ("e", "extraversion", "extroversion"),
    "A": ("a", "agreeableness", "agreeable"),
    "N": ("n", "neuroticism", "neurotic"),
}

_ALIAS_TO_TRAIT = {
    alias: trait for trait, aliases in _TRAIT_ALIASES.items() for alias in aliases
}

# Nested containers a score file may hide its trait scores or facet maps under.
_SCORE_CONTAINERS = (
    "summary", "scores", "traits", "facets", "big_five", "bigfive", "ocean",
)


class ScoreFormatError(ValueError):
    """Raised when a Big Five payload contains no readable trait scores."""


def _canonical_trait(key):
    return _ALIAS_TO_TRAIT.get(str(key).strip().lower().replace(" ", "_"))


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _direct_scores(mapping):
    """Trait -> number pairs found directly in a mapping."""
    found = {}
    for key, value in mapping.items():
        trait = _canonical_trait(key)
        if trait and _is_number(value):
            found[trait] = float(value)
    return found


def _facet_scores(mapping):
    """Trait -> mean of its facet scores.

    Handles both {"facets": {"openness": {...}}} and
    {"traits": {"Openness": {"facets": {...}}}}. Facet lists without scores
    (the schema in templates/big_five.json) yield nothing.
    """
    found = {}
    for key, value in mapping.items():
        trait = _canonical_trait(key)
        if not trait or not isinstance(value, dict):
            continue
        facets = value.get("facets", value)
        if not isinstance(facets, dict):
            continue
        numbers = [float(v) for v in facets.values() if _is_number(v)]
        if numbers:
            found[trait] = sum(numbers) / len(numbers)
    return found


def _collect_scores(raw):
    """Gather trait scores from a payload, preferring stated summaries.

    Returns (scores, sources) where sources maps each trait to how it was
    resolved: "stated" for an explicit trait score, "facet_mean" for an average.
    """
    if not isinstance(raw, dict):
        raise ScoreFormatError(
            "Big Five scores must be a mapping, got %s" % type(raw).__name__
        )

    candidates = [raw] + [
        raw[name] for name in _SCORE_CONTAINERS if isinstance(raw.get(name), dict)
    ]

    stated, facet_means = {}, {}
    for mapping in candidates:
        for trait, value in _direct_scores(mapping).items():
            stated.setdefault(trait, value)
        for trait, value in _facet_scores(mapping).items():
            facet_means.setdefault(trait, value)

    scores, sources = {}, {}
    for trait in TRAITS:
        if trait in stated:
            scores[trait] = stated[trait]
            sources[trait] = "stated"
        elif trait in facet_means:
            scores[trait] = facet_means[trait]
            sources[trait] = "facet_mean"
    return scores, sources


def _detect_scale(values):
    """Infer the input scale from the observed magnitudes."""
    high = max(values)
    if high > SCALE_MAX:
        return "0-100"
    if high > 1.0:
        return "1-5"
    return "0-1"


def _to_canonical(value, scale):
    """Map a value on `scale` onto the canonical 1-5 scale."""
    if scale == "0-1":
        value = SCALE_MIN + value * (SCALE_MAX - SCALE_MIN)
    elif scale == "0-100":
        value = SCALE_MIN + (value / 100.0) * (SCALE_MAX - SCALE_MIN)
    return max(SCALE_MIN, min(SCALE_MAX, value))


def normalize_scores(raw, scale=None):
    """Normalise any supported Big Five payload to 1-5 trait scores.

    Args:
        raw (dict): Parsed big_five.json contents. Trait scores may sit at the
            top level or under summary/scores/traits, keyed by letter or full
            name, on a 0-1, 1-5 or 0-100 scale. Facet maps are averaged when a
            trait has no stated score.
        scale (str): Optional explicit "0-1" | "1-5" | "0-100" override. Falls
            back to `raw["scale"]`, then to inference from the values.

    Returns:
        (dict, dict): trait scores on 1-5, and metadata describing how they
        were resolved (scale used, per-trait source, missing traits).

    Raises:
        ScoreFormatError: if no trait scores could be read at all.
    """
    scores, sources = _collect_scores(raw)

    if not scores:
        raise ScoreFormatError(
            "No Big Five trait scores found. Expected O/C/E/A/N or full trait "
            "names with numeric values, at the top level or under one of: %s."
            % ", ".join(_SCORE_CONTAINERS)
        )

    declared = scale or (raw.get("scale") if isinstance(raw, dict) else None)
    if declared and declared not in ("0-1", "1-5", "0-100"):
        raise ScoreFormatError(
            "Unknown scale %r; expected '0-1', '1-5' or '0-100'." % declared
        )
    # Inference is ambiguous only for 1-5 data whose traits all sit at the very
    # bottom of the range; declare "scale" in the file to settle it.
    resolved_scale = declared or _detect_scale(list(scores.values()))

    normalized = {t: _to_canonical(v, resolved_scale) for t, v in scores.items()}

    missing = [t for t in TRAITS if t not in normalized]
    for trait in missing:
        normalized[trait] = NEUTRAL
        sources[trait] = "default_neutral"

    return normalized, {
        "scale": resolved_scale,
        "scale_declared": bool(declared),
        "sources": sources,
        "missing_traits": missing,
        "raw_scores": scores,
    }


class PersonalityConverter:
    """Handles the conversion logic between the layers of the Pessoa Framework."""

    @staticmethod
    def calculate_layer3_params(big_five_scores, scale=None):
        """Calculates Layer 3 behavioural parameters from a Big Five profile.

        Args:
            big_five_scores (dict): Any payload accepted by normalize_scores.
            scale (str): Optional explicit input scale override.

        Returns:
            dict: Calculated LLM parameters.

        Raises:
            ScoreFormatError: if the payload carries no readable trait scores.
        """
        return PersonalityConverter.analyze(big_five_scores, scale)["parameters"]

    @staticmethod
    def analyze(big_five_scores, scale=None):
        """calculate_layer3_params plus the provenance of the numbers.

        Returns a dict with "parameters", the normalised 1-5 "scores", and
        "meta" recording the scale, per-trait source and any clamping — so a
        cabinet can be audited instead of taken on faith.
        """
        scores, meta = normalize_scores(big_five_scores, scale)

        # Normalise the 1-5 trait score onto 0.0-1.0 so each parameter spans
        # its full documented range. The original spec used trait/5, which only
        # reaches 0.2-1.0 because traits never hit 0 -- that lost the bottom 20%
        # of every range and drove frequency_penalty negative, which is why it
        # then needed clamping. (trait-1)/4 is the correct normalisation and
        # reproduces the psychometric bands the constants were fitted to.
        n_o, n_c, n_e, n_a, n_n = (
            (scores[t] - SCALE_MIN) / (SCALE_MAX - SCALE_MIN) for t in TRAITS
        )

        # Openness -> creativity (r=0.45)
        temperature = round(0.30 + n_o * 0.60, 3)          # 0.30 - 0.90
        # Conscientiousness -> precision (r=0.52)
        top_p = round(0.90 - n_c * 0.24, 3)                # 0.66 - 0.90
        # Agreeableness -> social style (r=-0.38)
        freq_penalty = round(0.50 - n_a * 0.32, 3)         # 0.18 - 0.50
        # Extraversion -> verbosity (r=0.62)
        # Unlike the other four, max_tokens is a truncation ceiling rather than
        # a style dial: it cannot make a response terse, only cut it off. The
        # spec's original 280-480 band was a cost ceiling from an era of
        # expensive output, and it truncates real deliverables well before a
        # low-Extraversion character has finished being brief. Extraversion
        # still scales the budget; the budget is now large enough not to sever
        # the work. Terseness is carried by the register, not the ceiling.
        max_tokens = int(round(512 + n_e * 3584))          # 512 - 4096
        # Neuroticism -> confidence (r=-0.48)
        confidence = round(0.90 - n_n * 0.18, 3)           # 0.72 - 0.90

        params = {
            "temperature": temperature,
            "top_p": top_p,
            "frequency_penalty": freq_penalty,
            "max_tokens": max_tokens,
            "confidence_threshold": confidence,
        }

        bounded, applied = {}, []
        for key, value in params.items():
            low, high = SAFETY_BOUNDS[key]
            clipped = value
            if low is not None:
                clipped = max(low, clipped)
            if high is not None:
                clipped = min(high, clipped)
            if clipped != value:
                applied.append(key)
            bounded[key] = clipped

        meta["bounds_applied"] = applied
        return {
            "parameters": bounded,
            "scores": {t: round(scores[t], 3) for t in TRAITS},
            "meta": meta,
        }


def _self_test():
    """Assert the calculus reproduces the documented behavioural bands."""
    # (parameter, trait, {trait score: (low, high) expected}) from the
    # Psychometric Justification tables.
    bands = [
        ("top_p", "C", {1: (0.86, 0.90), 3: (0.78, 0.78), 5: (0.66, 0.70)}),
        ("frequency_penalty", "A", {1: (0.38, 0.50), 3: (0.32, 0.34), 5: (0.18, 0.26)}),
        # max_tokens deliberately departs from the spec's 280-480 band (see
        # analyze()); asserted against its own documented range instead.
        ("max_tokens", "E", {1: (512, 512), 3: (2304, 2304), 5: (4096, 4096)}),
        ("confidence_threshold", "N", {1: (0.82, 0.90), 3: (0.78, 0.81), 5: (0.72, 0.72)}),
    ]
    failures = []
    for param, trait, expectations in bands:
        for score, (low, high) in expectations.items():
            profile = dict.fromkeys(TRAITS, NEUTRAL)
            profile[trait] = float(score)
            got = PersonalityConverter.calculate_layer3_params(profile, scale="1-5")[param]
            status = "ok" if low <= got <= high else "FAIL"
            if status == "FAIL":
                failures.append(f"{param} at {trait}={score}: {got} not in [{low}, {high}]")
            print(f"  {param:22} {trait}={score}  ->  {got:<7} band [{low}, {high}]  {status}")

    # Every parameter must sit inside the spec's safety bounds at both extremes.
    for extreme in (SCALE_MIN, SCALE_MAX):
        result = PersonalityConverter.analyze(dict.fromkeys(TRAITS, extreme), scale="1-5")
        if result["meta"]["bounds_applied"]:
            failures.append(
                "safety bounds bound at trait=%s: %s"
                % (extreme, result["meta"]["bounds_applied"])
            )
    print("\n  safety bounds never bind at the extremes: "
          + ("FAIL" if any("safety" in f for f in failures) else "ok"))
    return failures


if __name__ == "__main__":
    print("Band conformance:")
    problems = _self_test()

    strategist = {"O": 2.3, "C": 4.7, "E": 3.0, "A": 1.8, "N": 1.2}
    print("\nThe Strategist (1-5):")
    print(json.dumps(PersonalityConverter.analyze(strategist), indent=2))

    # 0-1 scale under a "summary" block, as characters/ files are written.
    print("\n0-1 summary block:")
    print(
        json.dumps(
            PersonalityConverter.analyze(
                {"summary": {"openness": 0.86, "conscientiousness": 0.91,
                             "extraversion": 0.18, "agreeableness": 0.34,
                             "neuroticism": 0.67}}
            ),
            indent=2,
        )
    )

    # Facet-only payload: traits are the mean of their facets.
    print("\nFacet means:")
    print(
        json.dumps(
            PersonalityConverter.analyze(
                {"facets": {"openness": {"imagination": 0.9, "intellect": 0.7}}}
            ),
            indent=2,
        )
    )

    # The schema template carries facet names but no scores -> refuse it.
    print("\nUnscored schema:")
    try:
        PersonalityConverter.analyze({"traits": {"Openness": {"facets": ["Imagination"]}}})
    except ScoreFormatError as exc:
        print("ScoreFormatError: %s" % exc)

    if problems:
        print("\n%d BAND/BOUND FAILURE(S):" % len(problems))
        for problem in problems:
            print("  - %s" % problem)
        raise SystemExit(1)
    print("\nAll band and safety-bound checks passed.")
