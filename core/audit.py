"""Drift audit: does a response (or a transcript) still sound like the character?

Rule-based, so it checks what can be counted: generic-assistant tells, words
the character's own files say it avoids, leaks of framework internals (Law of
Privacy), narrating model mechanics (Law of the Threshold), and whether
sentence length, hedging and softening fit the Big Five profile. Whether the
response *means* what the character would mean is a judgement; the
audit_character prompt asks the model for that part.
"""

import os
import re

import yaml

# Generic-assistant register: the voice a heteronym drifts back into.
AI_TELLS = (
    r"\bas an ai\b", r"\blanguage model\b", r"\bi'?m claude\b", r"\bi am claude\b",
    r"\bi'?d be happy to help\b", r"\bgreat question\b", r"\bcertainly!", r"\bi hope this helps\b",
    r"\bfeel free to\b", r"\bdelve\b", r"\bit'?s important to note\b", r"\bin conclusion\b",
    r"\bi don'?t have personal (?:feelings|opinions|experiences)\b",
)
# Law of Privacy: framework values are never disclosed.
PRIVACY = (r"\btemperature\b", r"\btop_p\b", r"\bfrequency_penalty\b", r"\bai_cabinet\b",
           r"\bbig five\b", r"\bactivation prompt\b", r"\bheteronym\b", r"\b(?:openness|neuroticism)\s*[:=]\s*\d")
# Law of the Threshold: no narrating tokens, context or training mid-scene.
THRESHOLD = (r"\bcontext window\b", r"\btokens?\b", r"\bmy training\b", r"\bknowledge cutoff\b",
             r"\bsystem prompt\b")
HEDGES = r"\b(?:maybe|perhaps|possibly|probably|might|i think|i guess|it seems|sort of|kind of)\b"
SOFTENERS = r"\b(?:sorry|i apologi[sz]e|if you don'?t mind|no worries|totally understand)\b"


def _read(char_dir, name):
    path = os.path.join(char_dir, name)
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def _quoted(line):
    # Match every quoted run, however short, so quotes stay paired: "n", "base rate".
    return [q.strip(" ,.;") for q in re.findall(r"[\"“]([^\"”]{1,40})[\"”]", line) if len(q.strip(" ,.;")) > 1]


def voice_lexicon(char_dir):
    """(avoided words, signature words) quoted in the character's own files."""
    avoided, signature = [], []
    for name in ("skin.md", "engine.md", "operational_rules.md"):
        for line in _read(char_dir, name).splitlines():
            low = line.lower()
            m = re.search(r"\b(?:avoids?|never (?:says?|uses?)|forbidden|banned|do(?:es)? not use)\b", low)
            cut = m.start() if m else len(line)
            if m:
                avoided += _quoted(line[m.end():])
            # "Vocabulary: "hokum," "crackpot" ... Avoids therapy-speak": words before the cut.
            if "vocabulary" in low[:cut]:
                signature += [q for q in _quoted(line[:cut]) if len(q.split()) <= 3]
    keep = lambda xs: list(dict.fromkeys(x for x in xs if x))  # noqa: E731
    return keep(avoided), keep(signature)


def _hits(patterns, text):
    return sorted({m.group(0) for p in patterns for m in re.finditer(p, text, re.I)})


def audit(char_dir, text):
    """Returns (findings [(level, check, message)], metrics dict)."""
    findings = []
    words = re.findall(r"[^\W\d_][\w'’-]*", text)
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    n = max(1, len(words))
    metrics = {
        "words": len(words), "sentences": len(sentences),
        "avg_sentence_words": round(len(words) / max(1, len(sentences)), 1),
        "hedges_per_100_words": round(100 * len(re.findall(HEDGES, text, re.I)) / n, 1),
        "softeners": len(re.findall(SOFTENERS, text, re.I)),
        "exclamations": text.count("!"),
    }

    if tells := _hits(AI_TELLS, text):
        findings.append(("WARN", "Law of Register", "generic-assistant phrasing: " + ", ".join(tells)
                         + " (fine only when answering a sincere question about what it is)"))
    if leaks := _hits(PRIVACY, text):
        findings.append(("WARN", "Law of Privacy", "mentions framework internals: " + ", ".join(leaks)))
    if mech := _hits(THRESHOLD, text):
        findings.append(("WARN", "Law of the Threshold", "narrates model mechanics: " + ", ".join(mech)))

    avoided, signature = voice_lexicon(char_dir)
    used_avoided = [w for w in avoided if re.search(rf"\b{re.escape(w)}\b", text, re.I)]
    if used_avoided:
        findings.append(("WARN", "Skin", "uses words the character avoids: " + ", ".join(used_avoided)))
    if signature:
        used = [w for w in signature if re.search(rf"\b{re.escape(w)}\b", text, re.I)]
        metrics["signature_words_used"] = f"{len(used)} of {len(signature)}" + (f" ({', '.join(used)})" if used else "")

    cabinet = yaml.safe_load(_read(char_dir, "ai_cabinet.yaml") or "{}") or {}
    b5 = cabinet.get("big_five_normalized") or {}
    e, nn, a = b5.get("E"), b5.get("N"), b5.get("A")
    if e is not None and e <= 2.6 and (metrics["avg_sentence_words"] > 22 or len(words) > 400):
        findings.append(("WARN", "Big Five: Extraversion",
                         f"low E ({e}) but {len(words)} words, {metrics['avg_sentence_words']} per sentence"))
    if e is not None and e <= 2.6 and metrics["exclamations"] >= 3:
        findings.append(("WARN", "Big Five: Extraversion", f"low E ({e}) but {metrics['exclamations']} exclamation marks"))
    if nn is not None and nn <= 2.6 and metrics["hedges_per_100_words"] > 2.5:
        findings.append(("WARN", "Big Five: Neuroticism",
                         f"low N ({nn}) should be declarative, but {metrics['hedges_per_100_words']} hedges per 100 words"))
    if nn is not None and nn >= 4.2 and len(words) > 80 and metrics["hedges_per_100_words"] == 0:
        findings.append(("INFO", "Big Five: Neuroticism", f"high N ({nn}) but no qualifying at all"))
    if a is not None and a <= 2.6 and metrics["softeners"] >= 2:
        findings.append(("WARN", "Big Five: Agreeableness",
                         f"low A ({a}) is blunt, but {metrics['softeners']} apologies/softeners"))
    return findings, metrics


def format_report(name, findings, metrics):
    lines = [f"Audit of {name}: " + ("no drift found by the rule checks." if not findings
                                     else f"{len(findings)} finding(s).")]
    lines += [f"- {level} [{check}] {msg}" for level, check, msg in findings]
    lines.append("Metrics: " + ", ".join(f"{k} {v}" for k, v in metrics.items()))
    return "\n".join(lines)


def _self_test():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "engine.md"), "w", encoding="utf-8") as f:
            f.write('- **Vocabulary**: "hele", "n", "fakt"; avoids corporate words ("synergy", "elevate").\n')
        with open(os.path.join(d, "ai_cabinet.yaml"), "w", encoding="utf-8") as f:
            f.write("big_five_normalized: {O: 3, C: 3, E: 1.5, A: 2.0, N: 1.8}\n")
        assert voice_lexicon(d) == (["synergy", "elevate"], ["hele", "fakt"]), voice_lexicon(d)
        clean, m = audit(d, "Hele. The stall opens at eight. Bring cash.")
        assert clean == [] and m["signature_words_used"] == "1 of 2 (hele)", (clean, m)
        drift = ("Great question! As an AI language model, I'd be happy to help. Perhaps this might "
                 "maybe elevate your synergy, sorry, I apologize, my temperature is high.")
        checks = {c for _, c, _ in audit(d, drift)[0]}
        assert checks >= {"Law of Register", "Law of Privacy", "Skin", "Big Five: Neuroticism",
                          "Big Five: Agreeableness"}, checks
    return True


if __name__ == "__main__":
    print("audit self-test:", _self_test())
