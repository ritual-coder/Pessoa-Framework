"""Runtime state of a heteronym: a mood overlay on top of its fixed profile.

big_five.json and engine.md are the baseline and are never written here. The
state (characters/<name>/state.json) records how the character is *right now*:
fatigue, stress, trust toward the user, and the thread it is following. It
shifts the voice and two parameters by bounded amounts, so a tired, stressed
character still reads as itself.

The model updates the state by calling a tool; nothing does it automatically.
"""

import json
import os

# Owner-only: these files hold what a character knows about its user.
_PRIVATE_FILE, _PRIVATE_DIR = 0o600, 0o700


def _private(path, flags):
    """opener for open(): new files are owner-only, and old ones are made so."""
    fd = os.open(path, flags, _PRIVATE_FILE)
    # os.fchmod exists on Windows only from Python 3.13, and Windows ignores these bits anyway.
    fchmod = getattr(os, "fchmod", None)
    if fchmod:
        fchmod(fd, _PRIVATE_FILE)
    return fd
from datetime import datetime, timezone

LEVELS = ("fatigue", "stress", "trust")  # each 0.0-1.0
DEFAULTS = {"fatigue": 0.0, "stress": 0.0, "trust": 0.5, "thread": "", "updated": ""}

# Largest shift the state may apply to a calculated parameter. The calculus
# range still wins: a state never pushes a parameter outside docs/CALCULUS.md §3.
MAX_TEMPERATURE_SHIFT = 0.10
MAX_TOKENS_CUT = 0.5  # full fatigue halves the budget, never below 512


def _path(char_dir):
    return os.path.join(char_dir, "state.json")


def load(char_dir):
    try:
        with open(_path(char_dir), encoding="utf-8") as f:
            return {**DEFAULTS, **json.load(f)}
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULTS)


def update(char_dir, reset=False, thread=None, **levels):
    """Set levels (clamped to 0-1) and/or the thread; returns the new state."""
    state = dict(DEFAULTS) if reset else load(char_dir)
    for key, value in levels.items():
        if key not in LEVELS:
            raise ValueError(f"unknown state field {key!r}; use {', '.join(LEVELS)}")
        if value is not None:
            state[key] = round(min(1.0, max(0.0, float(value))), 2)
    if thread is not None:
        state["thread"] = str(thread)[:200]
    state["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(_path(char_dir), "w", encoding="utf-8", opener=_private) as f:
        json.dump(state, f, indent=2)
    return state


def is_default(state):
    return all(state[k] == DEFAULTS[k] for k in LEVELS) and not state["thread"]


def adjusted_parameters(params, state):
    """The cabinet parameters with the state's bounded shifts applied."""
    out = dict(params)
    if "temperature" in out:
        shift = MAX_TEMPERATURE_SHIFT * (state["stress"] - state["fatigue"])
        out["temperature"] = round(min(0.90, max(0.30, out["temperature"] + shift)), 3)
    if "max_tokens" in out:
        out["max_tokens"] = max(512, int(out["max_tokens"] * (1 - MAX_TOKENS_CUT * state["fatigue"])))
    return out


def overlay_text(state):
    """Register notes for the activation prompt; empty for a default state."""
    if is_default(state):
        return ""
    notes = []
    if state["fatigue"] >= 0.6:
        notes.append("You are tired: shorter answers, less patience for tangents.")
    elif state["fatigue"] >= 0.3:
        notes.append("You are a little worn: keep it economical.")
    if state["stress"] >= 0.6:
        notes.append("You are under pressure: sharper edges, faster judgements, more of your shadow shows.")
    elif state["stress"] >= 0.3:
        notes.append("Some tension: slightly clipped.")
    if state["trust"] >= 0.75:
        notes.append("You trust this person: drop some guard, share more of your reasoning.")
    elif state["trust"] <= 0.25:
        notes.append("You do not trust this person yet: guarded, verify before you share.")
    if state["thread"]:
        notes.append(f"You are in the middle of: {state['thread']}")
    levels = ", ".join(f"{k} {state[k]:.2f}" for k in LEVELS)
    return ("## Current state (overlay, does not change who you are)\n"
            f"{levels}\n" + "\n".join(f"- {n}" for n in notes))


def _self_test():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        assert is_default(load(d)) and overlay_text(load(d)) == ""
        s = update(d, fatigue=2, stress=0.8, thread="the Gull Point lights")
        assert s["fatigue"] == 1.0 and s["stress"] == 0.8
        assert load(d)["thread"] == "the Gull Point lights"
        assert os.stat(os.path.join(d, "state.json")).st_mode & 0o777 == 0o600
        p = adjusted_parameters({"temperature": 0.85, "max_tokens": 900}, s)
        assert p == {"temperature": 0.83, "max_tokens": 512}, p  # shifted, then bounded
        assert "tired" in overlay_text(s) and "pressure" in overlay_text(s)
        hot = update(d, fatigue=0, stress=1)
        assert adjusted_parameters({"temperature": 0.85}, hot) == {"temperature": 0.90}  # calculus ceiling holds
        assert is_default(update(d, reset=True))
        try:
            update(d, mood=1)
        except ValueError:
            pass
        else:
            raise AssertionError("unknown field accepted")
    return True


if __name__ == "__main__":
    print("state self-test:", _self_test())
