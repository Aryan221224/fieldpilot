# Architecture decisions

Short records of the choices a judge or a reviewer is most likely to question.

## 1. The language model orchestrates; it does not decide

Asked directly, an LLM will recommend a treatment and quote a yield for any situation,
including impossible ones. FieldPilot gives Nemotron tools and makes the tools the only source
of numbers. The decision "is this spray worth it" belongs to the crop twin; the decision "may
this fly" belongs to plain code and a human. The model's job is sequencing, explanation,
bilingual communication and pulling in local context. `eval/grounding_eval.py` measures what
happens when you let the model decide instead.

## 2. Discovery, not hard-coded model IDs

The Token Factory catalog changes and public lists disagree. The client reads `/v1/models` and
picks each role by pattern: Nemotron 3 Super (then Lightning, Ultra) for the agent; Nemotron
3.5 Super VL (then other Nemotron VL, Cosmos-Reason, Nano Omni, other VLMs) for perception;
Nemotron 3.5 Lightning (then Nano) for bookings and the brief. The chosen IDs are recorded in
every run log, and every call is written to a usage ledger ([MODELS.md](MODELS.md)).

## 3. Native tool calling with a JSON fallback

If the endpoint rejects `tools=` or the model answers in prose, the client switches to a
JSON-action protocol. If the model stops early or exceeds the step budget, the deterministic
policy completes the run and the proposal says so. The operator always gets a legal answer.

## 4. WOFOST, not a neural surrogate

A season runs in ~0.15 s on one core; an ensemble is seconds. There is no speed problem for a
surrogate to solve, and a surrogate would add an unvalidated approximation between the
observation and the recommendation.

## 5. WOFOST 7.2 for disease questions, 8.1 for nitrogen

Disease is coupled as daily leaf-area loss through `set_variable('LAI')`. Only the 7.2
leaf-dynamics module implements that; on 8.1 the call returns `{}` and silently does nothing,
which would make every disease look harmless. Nitrogen questions need 8.1's N balance. All
options for one zone use one engine, so comparisons stay paired. There is a regression test.

## 6. Phenology from one photo

The vision model reports a BBCH stage; the weather record holds the thermal time. Walking back
through the weather until accumulated effective temperature (via the cultivar's DTSMTB, with
photoperiod slowing) matches the stage gives an effective emergence date. A wrong or unknown
sowing date stops mattering.

## 7. CVaR, not the mean

The objective is the mean margin of the worst 20 % of simulated seasons. A treatment must beat
doing nothing there by at least ₹500/ha, and in at least 60 % of seasons, or the answer is "no
action". This is how the system says no to a late-season spray that the mean would approve.

## 8. Conformal gate on a VLM

VLM confidences are uncalibrated. Split conformal turns them into prediction sets with a
coverage guarantee that holds however badly calibrated the model is; only singleton sets may
drive an action. Until ≥ 20 labelled photos exist the gate is marked UNCALIBRATED and uses a
conservative threshold. Human adjudications are logged and become calibration data.

## 9. pymavlink, not MAVSDK

One library for PX4 and ArduPilot, no gRPC server on the companion computer, and the mission
protocol is small enough to own — which lets the tests run the real handshake against a fake
vehicle.

## 10. The console has no external assets

The map is SVG drawn from the field geometry; no tiles, no CDN. It works on a laptop in a
field with no signal, which is where it is used.

## Operator desk and efficiency engine (one operator, many farms, any country)

```
 bookings (any language: text, voice-note transcripts)
        │  Nemotron on Token Factory: JSON fields only
        ▼
 intake.py   area must be written in the message (digits or number words) · units in code
             (bigha / alqueire = the operator's local size, flagged) · pin or geocoded place
        ▼
 dayplan.py  live Open-Meteo hourly forecast per farm × the operator's rule pack (rules.py:
             India SOP-2023, or wind 1-4.5 m/s + Delta T 2-10 good practice)
             × sorties per job (sorties.plan_sorties, same planner the drone flies)
             × battery sets, chargers, charge time × road time  →  greedy value-per-hour
             schedule; deferred jobs get their next legal window
        ▼
 desk.py     same bookings planned the common way (booking order, whole field, full tanks,
             corner take-off, no weather check) → comparison; Nemotron writes the brief in the
             operator's language, rejected if any number is not in the plan or the script is wrong
        ▼
 /desk       timeline · comparison · brief (browser text-to-speech) · printable day sheet
```

The sortie planner also changes the single-farm path: a route that does not fit one battery
or one tank is cut into sorties at the exact metre the limit is reached, and `approve` flies
them one at a time, re-running weather, preflight and the SOP before each take-off.

`eval/efficiency_bench.py` runs the same comparison for five regions on live forecasts and
reports effective hectares per battery cycle, sorties outside the window, hectares sprayed
beyond need and leftover mix, with and without field scans.
