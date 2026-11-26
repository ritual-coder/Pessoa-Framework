"""Runtime-aware exports of a heteronym, next to its ACTIVATION_PROMPT.md.

ai_cabinet.yaml is a manifest, not a request body (docs/CALCULUS.md §8), and
each API takes a different subset of it. These exports put each parameter
where a given runtime can use it, and say plainly which ones it cannot:

- anthropic.json   Messages API body: system + max_tokens. temperature/top_p
                   are kept apart, because current Claude models reject them.
- openai.json      Chat Completions body: a system message plus the four
                   sampling parameters OpenAI-compatible endpoints accept.
- generic.md       The activation prompt with the parameters as a YAML block,
                   for any chat window or local runner.
- profile.json     Everything as data: cabinet, state, activation text.

Written to characters/<name>/exports/. A model name is left for the user to
fill in: which model to run is their choice, not the character's.
"""

import json
import os

import yaml

TARGETS = ("anthropic", "openai", "generic", "json")
MODEL_PLACEHOLDER = "<your model id>"


def _read(char_dir, name):
    with open(os.path.join(char_dir, name), encoding="utf-8") as f:
        return f.read()


def build(char_dir, target, params=None):
    """(filename, text) for one target. params overrides the cabinet's (e.g. state-adjusted)."""
    cabinet = yaml.safe_load(_read(char_dir, "ai_cabinet.yaml")) or {}
    system = _read(char_dir, "ACTIVATION_PROMPT.md").strip()
    p = params or cabinet.get("parameters") or {}
    name = os.path.basename(char_dir)
    if target == "anthropic":
        body = {
            "request": {"model": MODEL_PLACEHOLDER, "max_tokens": p.get("max_tokens", 1024),
                        "system": system, "messages": [{"role": "user", "content": "<first message>"}]},
            "optional_sampling": {k: p[k] for k in ("temperature", "top_p") if k in p},
            "notes": [
                "Send 'request' as the body. Add optional_sampling only on models that accept "
                "it: current Claude models return 400 for temperature/top_p.",
                "frequency_penalty and confidence_threshold have no Anthropic parameter; the "
                "activation prompt carries them as register.",
            ],
        }
        return "anthropic.json", json.dumps(body, indent=2, ensure_ascii=False)
    if target == "openai":
        body = {
            "request": {"model": MODEL_PLACEHOLDER,
                        "messages": [{"role": "system", "content": system},
                                     {"role": "user", "content": "<first message>"}],
                        **{k: p[k] for k in ("temperature", "top_p", "frequency_penalty") if k in p},
                        "max_completion_tokens": p.get("max_tokens", 1024)},
            "notes": [
                "Reasoning models take role 'developer' instead of 'system' and ignore or "
                "reject temperature/top_p; older endpoints call max_completion_tokens max_tokens.",
                "confidence_threshold is not an API parameter; the prompt carries it as register.",
            ],
        }
        return "openai.json", json.dumps(body, indent=2, ensure_ascii=False)
    if target == "generic":
        params_yaml = yaml.safe_dump({"parameters": p}, sort_keys=False).strip()
        return "generic.md", (f"{system}\n\n---\n\n<!-- Sampling for runners that accept it "
                              f"(e.g. local models). -->\n```yaml\n{params_yaml}\n```\n")
    if target == "json":
        state_path = os.path.join(char_dir, "state.json")
        state = json.loads(_read(char_dir, "state.json")) if os.path.isfile(state_path) else None
        return "profile.json", json.dumps({"name": name, "cabinet": cabinet, "effective_parameters": p,
                                           "state": state, "activation_prompt": system},
                                          indent=2, ensure_ascii=False, default=str)
    raise ValueError(f"unknown target {target!r}; use {', '.join(TARGETS)} or all")


def export(char_dir, target="all", params=None):
    """Write the export(s) to char_dir/exports/. Returns the written paths."""
    out_dir = os.path.join(char_dir, "exports")
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for t in (TARGETS if target == "all" else (target,)):
        filename, text = build(char_dir, t, params)
        path = os.path.join(out_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        written.append(path)
    return written


def _self_test():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "ai_cabinet.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"parameters": {"temperature": 0.8, "top_p": 0.7, "frequency_penalty": 0.3,
                                           "max_tokens": 1200, "confidence_threshold": 0.8}}, f)
        with open(os.path.join(d, "ACTIVATION_PROMPT.md"), "w", encoding="utf-8") as f:
            f.write("# Voice\nShort sentences.\n")
        paths = export(d)
        assert sorted(os.path.basename(x) for x in paths) == ["anthropic.json", "generic.md",
                                                               "openai.json", "profile.json"]
        a = json.loads(_read(d, "exports/anthropic.json"))
        assert a["request"]["max_tokens"] == 1200 and "temperature" not in a["request"]
        assert a["optional_sampling"] == {"temperature": 0.8, "top_p": 0.7}
        o = json.loads(_read(d, "exports/openai.json"))["request"]
        assert o["messages"][0] == {"role": "system", "content": "# Voice\nShort sentences."}
        assert o["frequency_penalty"] == 0.3 and "confidence_threshold" not in o
        assert "temperature: 0.8" in _read(d, "exports/generic.md")
    return True


if __name__ == "__main__":
    print("export self-test:", _self_test())
