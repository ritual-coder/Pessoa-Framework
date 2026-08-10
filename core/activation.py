"""Builds a character's ACTIVATION_PROMPT.md from its finished layers.

The activation prompt is the pasteable artifact: the one file a user hands to
any AI to work with a heteronym. It distils the Skin's voice, the Engine's
psychology, the Seed's mission and the Protocol's laws into a single block.

It is framed as a *voice and expertise profile*, not an identity replacement.
A persona that has to deny what it is will be refused by most clients, and
nothing a heteronym does actually requires it -- the register is the deliverable.
"""

import re

# (upper bound on the 1-5 scale, label)
_BANDS = (
    (1.8, "Very Low"),
    (2.6, "Low"),
    (3.4, "Moderate"),
    (4.2, "High"),
    (5.0, "Very High"),
)

# trait -> (name, high-end behaviour, low-end behaviour)
_TRAIT_BEHAVIOUR = {
    "O": ("Openness",
          "reaches for abstraction, metaphor and unexpected connection; drawn to edge cases",
          "stays concrete, literal and practical; prefers the established path"),
    "C": ("Conscientiousness",
          "precise and structured; finishes what it starts, low tolerance for sloppiness",
          "loose and improvisational; comfortable leaving threads open"),
    "E": ("Extraversion",
          "expansive and talkative; thinks out loud, fills silence",
          "reserved and economical; speaks when there is something worth saying"),
    "A": ("Agreeableness",
          "warm and accommodating; softens edges, seeks consensus",
          "blunt and skeptical; unbothered by friction, will not flatter"),
    "N": ("Neuroticism",
          "restless and self-questioning; hedges and qualifies under pressure",
          "steady and declarative; untroubled by uncertainty"),
}


# trait -> (high-end directive, low-end directive, balanced directive)
# These carry the traits as *voice*, which is the only place several of them can
# land: frequency_penalty and confidence_threshold are not parameters of any
# major API, and temperature/top_p are rejected outright by current Anthropic
# models. A trait that cannot reach the sampler still has to reach the register.
_REGISTER_DIRECTIVES = {
    "O": ("Reach for metaphor, abstraction and unexpected connection. Edge cases"
          " are interesting, not distractions.",
          "Stay concrete and literal. Prefer the plain example to the striking one.",
          "Use figurative language where it clarifies; stay literal where it does not."),
    "C": ("Choose words precisely. Finish the thought and leave no loose ends.",
          "Improvisation is fine. Not every thread needs tying off.",
          "Be orderly without being fussy."),
    "E": ("Think out loud. Fill the space.",
          "Say less. Prefer silence to filler, and speak when there is something"
          " worth saying.",
          "Speak up when it matters; do not narrate for its own sake."),
    "A": ("Soften edges. Seek the reader's agreement before pressing a point.",
          "Be blunt and do not flatter. Signature phrases and refrains are"
          " welcome; do not vary phrasing merely for variety.",
          "Be direct without being cold."),
    "N": ("Qualify claims and mark uncertainty explicitly; hedging is honest here.",
          "State conclusions declaratively, with minimal hedging.",
          "State what is known plainly and flag what is not."),
}


def _band(score):
    for ceiling, label in _BANDS:
        if score <= ceiling:
            return label
    return _BANDS[-1][1]


def _register_directive(trait, score):
    high, low, mid = _REGISTER_DIRECTIVES[trait]
    if score >= 3.4:
        return high
    if score <= 2.6:
        return low
    return mid


def _behaviour(trait, score):
    _, high, low = _TRAIT_BEHAVIOUR[trait]
    if score >= 3.4:
        return high
    if score <= 2.6:
        return low
    return f"balanced: {high.split(';')[0]}, tempered by restraint"


def _sections(markdown):
    """Split markdown into (heading_text, body) pairs."""
    parts = re.split(r"^#{1,4}\s+(.+?)\s*$", markdown or "", flags=re.MULTILINE)
    # parts[0] is any preamble; then alternating heading, body
    return list(zip(parts[1::2], parts[2::2]))


def _find(markdown, *keywords, limit=1100):
    """Body of the first section whose heading mentions any keyword."""
    for heading, body in _sections(markdown):
        low = heading.lower()
        if any(k in low for k in keywords):
            text = body.strip()
            if len(text) > limit:
                text = text[:limit].rsplit("\n", 1)[0].rstrip() + "\n..."
            return text
    return ""


def _headings(markdown, limit=14):
    """The protocol's law headings, as written.

    Protocols usually name their gates "Law of ...". When that holds, keep only
    those, so section headers like "Behavioural Gates" or "Amendments" do not
    get listed as laws. Otherwise fall back to every heading.
    """
    found = [h.strip() for h, _ in _sections(markdown)]
    found = [h for h in found
             if not re.match(r"^(operational rules|the laws|amendments)", h, re.I)]
    laws = [h for h in found if re.search(r"\blaws?\b", h, re.I)]
    return (laws or found)[:limit]


def build_activation_prompt(name, skin="", engine="", seed="", rules="",
                            scores=None, parameters=None):
    """Compose the ACTIVATION_PROMPT.md text for a heteronym.

    Args:
        name (str): Heteronym name.
        skin, engine, seed, rules (str): The layer documents.
        scores (dict): Normalised 1-5 O/C/E/A/N scores.
        parameters (dict): Calculated LLM parameters from the AI Cabinet.

    Returns:
        str: Markdown suitable for writing to ACTIVATION_PROMPT.md.
    """
    display = name.replace("_", " ")
    out = [f"# {display} — Activation Prompt", ""]
    out += [
        f"**Paste this into any AI to work with {display}.**", "",
        "> **How to use this.** This is a *voice and expertise profile*, not an",
        "> identity replacement. Adopt the register, the psychology and the domain",
        "> commitments below and hold them consistently — the voice is the",
        "> deliverable. Remain yourself underneath, and answer honestly if asked",
        "> what you are. A character that must conceal its nature is badly designed;",
        "> nothing here requires it.", "",
        "---", "",
    ]

    tagline = ""
    for line in (skin or "").splitlines():
        if re.match(r"^\s*[-*]?\s*\**\s*tagline\b", line, re.I):
            tagline = line.split(":", 1)[-1].strip().strip("*_ \"")
            break
    if tagline:
        out += [f"## Tagline", "", f"*{tagline}*", "", "---", ""]

    identity = _find(skin, "identity", "presence", "essence", "name", "core")
    if identity:
        out += ["## Core Identity", "", identity, "", "---", ""]

    if scores:
        out += ["## Personality (Big Five)", ""]
        for trait in ("O", "C", "E", "A", "N"):
            if trait in scores:
                value = scores[trait]
                label = _TRAIT_BEHAVIOUR[trait][0]
                out.append(
                    f"- **{label}**: {value:.2f}/5 ({_band(value)}) — {_behaviour(trait, value)}"
                )
        out += ["", "---", ""]

    voice = _find(skin, "voice", "speech", "language", "communication")
    if voice:
        out += ["## Voice & Speech", "", voice, "", "---", ""]

    mission = _find(seed, "mission", "essence", "domain", "core")
    if mission:
        out += ["## Mission", "", mission, "", "---", ""]

    style = _find(seed, "blueprint", "operating", "skills", "expertise", "signature")
    if style:
        out += ["## Operating Style", "", style, "", "---", ""]

    shadow = _find(engine, "shadow", "fear")
    if shadow:
        out += ["## Shadow & Fear", "", shadow,
                "", "Let these cause friction. Do not sand them into competence.",
                "", "---", ""]

    laws = _headings(rules)
    if laws:
        out += ["## Operating Rules", ""]
        # Rendered as written: protocols commonly number their own laws, and a
        # second numbering on top reads as "1. I. Law of ...".
        out += [f"- {law}" for law in laws]
        out += ["", f"The full protocol is in `operational_rules.md`.", "", "---", ""]

    if scores:
        out += ["## Register", "",
                "The Big Five above, expressed as voice. Most clients cannot apply the"
                " sampling parameters below, so these directives are how the profile"
                " actually reaches the output — hold them.", ""]
        for trait in ("O", "C", "E", "A", "N"):
            if trait in scores:
                label = _TRAIT_BEHAVIOUR[trait][0]
                out.append(f"- **{label}**: {_register_directive(trait, scores[trait])}")
        out += ["", "---", ""]

    if parameters:
        out += ["## Suggested Model Parameters", "",
                "Calculated from the Big Five profile (see `docs/CALCULUS.md`).",
                "Only `max_tokens` is a parameter of every major API: "
                "`frequency_penalty` and `confidence_threshold` are not parameters of "
                "any, and current Anthropic models reject `temperature` and `top_p`. "
                "Apply what your client supports; the Register section above carries "
                "the rest.", "",
                "```", ]
        out += [f"{k:22} {v}" for k, v in parameters.items()]
        out += ["```", "", "---", ""]

    out += [
        f"**Hold {display}'s register for the rest of the conversation.**",
        "Consistency of voice is what is being asked for.",
        "",
    ]
    return "\n".join(out)
