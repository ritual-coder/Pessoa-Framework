# The Psychosynthetic Calculus

How a heteronym's Big Five profile becomes the LLM parameters in its
`ai_cabinet.yaml`. This is the specification `core/converter.py` implements;
where the two disagree, the code is authoritative and this document is a bug.

---

## 1. The input contract

The calculus reads `big_five.json`. Nothing else — trait scores written as prose
into `engine.md` or `skin.md` are **not** read.

```json
{
  "scale": "0-1",
  "summary": {
    "openness": 0.86, "conscientiousness": 0.91, "extraversion": 0.18,
    "agreeableness": 0.34, "neuroticism": 0.67
  },
  "facets": {
    "openness": { "imagination": 0.9, "intellect": 0.93 }
  }
}
```

**`scale`** — one of `"0-1"`, `"1-5"`, `"0-100"`. Always declare it. It is
inferred from the magnitudes when absent, which is unambiguous except for 1-5
data whose traits all sit at the very bottom of the range.

**`summary`** — the five trait scores. Keys may be single letters (`O`, `C`,
`E`, `A`, `N`) or full names, in any case. They may sit at the top level
instead, or under `scores` / `traits` / `big_five` / `ocean`.

**`facets`** — the 30-facet detail. A trait absent from `summary` falls back to
the **mean of its facets**. Fill either block, or both.

Inverted constructs are deliberately not accepted: there is no alias mapping
"emotional stability" onto Neuroticism, because silently flipping a sign is
worse than refusing the file.

**Failure is loud.** A payload with no readable trait scores raises
`ScoreFormatError`. It is never scored as a neutral profile — that behaviour is
what made this whole subsystem inert through v1.0 (see §7).

---

## 2. Normalisation

Two steps, in order.

**Step 1 — put the trait on the 1-5 scale.**

| declared scale | conversion |
|---|---|
| `1-5` | identity |
| `0-1` | `1 + s × 4` |
| `0-100` | `1 + (s / 100) × 4` |

**Step 2 — normalise to 0.0-1.0 for the formulas.**

```
n = (trait − 1) / 4
```

This is the step v1.0 got wrong. It used `trait / 5`, which spans only
**0.2 to 1.0** because a trait score never reaches 0. Every parameter therefore
lost the bottom 20% of its range, and `frequency_penalty` was driven negative —
which is why it needed a clamp. `(trait − 1) / 4` maps 1 → 0.0 and 5 → 1.0.

---

## 3. The formulas

| Parameter | Trait | Formula | Range |
|---|---|---|---|
| `temperature` | Openness | `0.30 + n × 0.60` | 0.30 – 0.90 |
| `top_p` | Conscientiousness | `0.90 − n × 0.24` | 0.66 – 0.90 |
| `frequency_penalty` | Agreeableness | `0.50 − n × 0.32` | 0.18 – 0.50 |
| `max_tokens` | Extraversion | `512 + n × 3584` | 512 – 4096 |
| `confidence_threshold` | Neuroticism | `0.90 − n × 0.18` | 0.72 – 0.90 |

Each spans exactly its documented range, so **no clamping is required**.

### Output at each trait level

| Parameter | t=1 | t=2 | t=3 | t=4 | t=5 |
|---|---|---|---|---|---|
| `temperature` | 0.300 | 0.450 | 0.600 | 0.750 | 0.900 |
| `top_p` | 0.900 | 0.840 | 0.780 | 0.720 | 0.660 |
| `frequency_penalty` | 0.500 | 0.420 | 0.340 | 0.260 | 0.180 |
| `max_tokens` | 512 | 1408 | 2304 | 3200 | 4096 |
| `confidence_threshold` | 0.900 | 0.855 | 0.810 | 0.765 | 0.720 |

---

## 4. Psychometric grounding

Each mapping follows an established Big Five correlation:

| Trait | Correlate | Parameter |
|---|---|---|
| Openness | creativity (r = 0.45) | temperature |
| Conscientiousness | precision (r = 0.52) | top_p |
| Extraversion | verbosity (r = 0.62) | max_tokens |
| Agreeableness | social style (r = −0.38) | frequency_penalty |
| Neuroticism | confidence (r = −0.48) | confidence_threshold |

The scaling constants are fitted so the output reproduces these behavioural
bands, which are the framework's statement of design intent:

| Parameter | Low (1-2) | Moderate (3) | High (4-5) |
|---|---|---|---|
| `top_p` | 0.86 – 0.90 | 0.78 | 0.66 – 0.70 |
| `frequency_penalty` | 0.38 – 0.42 | 0.32 | 0.18 – 0.26 |
| `max_tokens` | — | — | — |
| `confidence_threshold` | 0.82 – 0.90 | 0.78 | 0.72 |

**`max_tokens` deliberately departs from its band.** The spec placed it at
280–480, a cost ceiling from an era of expensive output. Unlike the other four,
`max_tokens` is a *truncation ceiling*, not a style dial: it cannot make a
response terse, only cut it off mid-sentence. A low-Extraversion character asked
for a long deliverable should write a *terse* long deliverable, not a severed
one. Extraversion still scales the budget — 512 to 4096 — but the budget is now
large enough not to destroy the work. Terseness is carried by the register, in
`ACTIVATION_PROMPT.md`, which is where it belongs.

`python core/converter.py` asserts conformance to these bands and exits non-zero
on failure. Treat it as the regression test for any change to the constants.

---

## 5. Safety bounds

| Parameter | Bound | Rationale |
|---|---|---|
| `temperature` | ≤ 0.90 | above this, hallucination risk |
| `top_p` | ≥ 0.60 | below this, output reads robotic |
| `frequency_penalty` | ≥ 0.10 | below this, heavy repetition |
| `max_tokens` | ≤ 4096 | generous ceiling, see §3 note |
| `confidence_threshold` | ≥ 0.70 | below this, output hedges to uselessness |

Because §3 is fitted inside these, the bounds are an **inert guard against bad
input, not active shaping**. The self-test asserts they never bind at either
extreme. If `calculus.bounds_applied` in a cabinet is ever non-empty, something
upstream is wrong — investigate rather than accept the clipped value.

---

## 6. Auditing a cabinet

Every generated `ai_cabinet.yaml` records the numbers it was derived from:

```yaml
parameters:
  temperature: 0.816
  top_p: 0.682
  frequency_penalty: 0.391
  max_tokens: 1157
  confidence_threshold: 0.779
big_five_normalized:      # the 1-5 scores the formulas actually consumed
  O: 4.44
  C: 4.64
  E: 1.72
  A: 2.36
  N: 3.68
calculus:
  scale: 0-1
  scale_declared: true
  sources:                # stated | facet_mean | default_neutral
    O: stated
  missing_traits: []
  raw_scores:             # as written in big_five.json
    O: 0.86
  bounds_applied: []
```

Three things to check when a cabinet looks wrong:

- **`sources`** — a trait reading `default_neutral` was never found in the file.
- **`missing_traits`** — non-empty means the profile is incomplete.
- **`scale_declared: false`** — the scale was guessed. Declare it.

### Worked example: Stack_And_Dagger

```
raw (0-1)   O 0.86   C 0.91   E 0.18   A 0.34   N 0.67
1-5 scale   O 4.44   C 4.64   E 1.72   A 2.36   N 3.68
n           O 0.86   C 0.91   E 0.18   A 0.34   N 0.67

temperature          = 0.30 + 0.86 × 0.60 = 0.816
top_p                = 0.90 − 0.91 × 0.24 = 0.682
frequency_penalty    = 0.50 − 0.34 × 0.32 = 0.391
max_tokens           = 512  + 0.18 × 3584 = 1157
confidence_threshold = 0.90 − 0.67 × 0.18 = 0.779
```

High Openness yields a high temperature; low Extraversion yields 1157 tokens
against a 4096 ceiling — a smaller budget than a talkative character would get,
without truncating the work.

> Note that for a profile authored on the 0-1 scale, `n` equals the raw score.
> The two conversions in §2 are inverse over that range, so 0-1 input passes
> through to the formulas unchanged. This is a convenient identity, not a
> shortcut to rely on: it does not hold for 1-5 or 0-100 profiles.

---

## 7. What changed from v1.0

v1.0 shipped a calculus that **returned identical parameters for every
heteronym**. `converter.py` looked for `O`/`C`/`E`/`A`/`N` on a 1-5 scale, but
character files stored 0-1 values under a `summary` block, so every trait fell
through to a neutral 3.0 default. Stack_And_Dagger's cabinet was byte-identical
to the output for an empty dictionary.

Fixing the input contract exposed a second defect: the `trait / 5` normalisation
of §2, and a `frequency_penalty` scaling constant that sent the parameter
negative and so required clamping to `0.18`–`0.5`. That clamp flattened roughly
58% of the Agreeableness range onto a single value.

The formulas were re-fitted so each spans its documented range and reproduces
the bands in §4.

**This supersedes the earlier worked example.** The Strategist profile
(O 2.3, C 4.7, E 3.0, A 1.8, N 1.2) previously documented as
`0.576 / 0.712 / 0.284 / 440 / 0.828` now yields:

```
temperature 0.495   top_p 0.678   frequency_penalty 0.436
max_tokens 2304     confidence_threshold 0.891
```

The old values came from formulas that could not hit the §4 bands or stay inside
the §5 bounds. Any external material citing them is out of date; this document
is the specification.

---

## 8. Applying the parameters

**Most of the cabinet is not directly applicable, and the framework should not
pretend otherwise.** `ai_cabinet.yaml` is a manifest the framework produces; it
is not a request body, and no client consumes it automatically.

Two of the five were never parameters of any major API:

| Parameter | Status |
|---|---|
| `frequency_penalty` | An **OpenAI** parameter. Not accepted by the Anthropic Messages API. |
| `confidence_threshold` | Not a parameter of any major API. It originates in the framework's own source material. |

And on Anthropic, sampling parameters were removed from the current model line
entirely — the guidance there is to steer with prompting instead:

| Parameter | Opus 4.6 / Sonnet 4.6 / Haiku 4.5 | Opus 5 / 4.8 / 4.7, Fable 5 | Sonnet 5 |
|---|---|---|---|
| `temperature` | accepted | **400 — removed** | 400 if non-default |
| `top_p` | accepted | **400 — removed** | 400 if non-default |
| `max_tokens` | accepted | accepted | accepted |

So against a current Anthropic model, exactly one of the five — `max_tokens` —
is a live request parameter. Against an OpenAI-compatible endpoint, four are.
Sending the rest either errors or does nothing.

### Where the other traits land instead

A trait that cannot reach the sampler still has to reach the output, so it is
carried as **register** in `ACTIVATION_PROMPT.md`, which every client can use:

| Trait | Parameter | Register directive when high / low |
|---|---|---|
| Openness | `temperature` | reach for metaphor and abstraction / stay concrete and literal |
| Conscientiousness | `top_p` | choose words precisely / improvisation is fine |
| Extraversion | `max_tokens` | think out loud / say less, prefer silence to filler |
| Agreeableness | `frequency_penalty` | soften edges / be blunt, refrains are welcome |
| Neuroticism | `confidence_threshold` | qualify and mark uncertainty / state conclusions declaratively |

This is the same principle as `max_tokens` in §4: a parameter is a blunt
instrument for a property of voice, and voice is where the property belongs.
`core/activation.py` generates these directives from the same normalised scores
that produce the parameters, so the two never disagree.

**Practical guidance.** Apply `max_tokens` everywhere. Apply `temperature` and
`top_p` only on providers and models that accept them — check before sending,
because a rejected parameter is a 400, not a silent no-op. Treat
`frequency_penalty` and `confidence_threshold` as documentation of intent, and
rely on the register for their effect.

---

## 9. Extending

The constants are the tuning surface. To retarget a parameter, change its base
and span so the endpoints land on the range you want, then re-run
`python core/converter.py` to confirm the bands in §4 still hold and the bounds
in §5 still never bind. If a change makes a bound bind, the constants are wrong
— do not widen the bound to accommodate them.
