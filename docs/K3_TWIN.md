# The K3-A Pro twin: does the plan survive the controller?

The T1 flies on a JIYI K3-A Pro, a closed controller. FieldPilot cannot upload to it. The pilot
types each flight card into the JIYI app, loads the litres on the card, and the controller flies
its own route. So the question that matters is not "is FieldPilot's plan good?" but "does the
plan still hold once the K3-A Pro flies it?"

`fieldpilot/k3sim.py` answers that before any flight. It flies the card the way the controller
is documented to behave:

| Behaviour | Source | How the twin does it |
|---|---|---|
| Parallel lines over each zone at the card's spacing, boundary to boundary | JIYI app (AB / polygon routes) | the same line pattern FieldPilot plans |
| Flow follows ground speed; the pump stops below 0.5 m/s | T1 brochure | dose per area held by speed; off on turns and in transit |
| The pump does not jump to the commanded flow | bench emulator | 0.15 s dead time, 0.25 s lag, stall duty 0.20 |
| Low liquid: save a breakpoint and return home | brochure ("breakpoint continuation") | the liquid sensor trips at 0.02 L |
| Low battery: return while there is energy to fly home and land | brochure | the T1 energy model, mass^1.5 power, 30 % reserve |
| The next sortie resumes at the breakpoint | brochure | it flies to the breakpoint and continues the route |
| Braking and acceleration at every line end, slow side-step between lines | assumption | 1.5 m/s² and 1.5 m/s (`K3Params`) |

Each line of that table is a parameter. None of it is a flight log.

## What it found, and what was fixed

The first comparison caught two errors in FieldPilot's own plan.

1. **Sorties took about 30 % longer than planned.** The planner flew every line at constant
   speed. It now charges braking, acceleration and the side-step at each line end, so its
   sortie times are within 3–5 % of the twin's. The cost: the T1's predicted work rate is
   8.5 acres/h, not the brochure's 10. The brochure figure counts no line-end time.
2. **The last sortie needs a margin.** The liquid sensor trips with liquid still in the line,
   so a last refill loaded to the litre left the final few metres unsprayed and forced a fourth
   micro-sortie. The last refill now carries 0.1 L + 3 %. Earlier refills stay exact on purpose,
   so their tanks run dry at the planned cut.

## Plan against twin (tests/test_k3sim.py)

| Field | Sorties (plan / twin) | Tank dry vs planned cut | Sortie time | Lowest zone coverage | Left in tank |
|---|---|---|---|---|---|
| 100 × 100 m, 4 zones | 3 / 3 | 1.3, 2.6 m | within 4 % | 99.2 % | 0.34 L |
| 150 × 120 m, 9 zones | 4 / 4 | 1.4, 2.5, 3.9 m | within 4 % | 99.2 % | 0.41 L |
| 200 × 50 m, 4 zones | 3 / 3 | 1.3, 2.6 m | within 4 % | 99.3 % | 0.34 L |
| 60 × 40 m, 6 zones | 1 / 1 | n/a | within 5 % | 98.2 % | 0.27 L |

Each breakpoint lands a little after the planned cut, about 1.3 m more per sortie. While a line
speeds up, the pump lays slightly less per metre than planned, and that roughly cancels the
sensor's residual. The next sortie resumes from the controller's own breakpoint, so nothing is
skipped and nothing is sprayed twice.

## In use

- `python -m fieldpilot.cli fieldday` flies every sortie of a simulated operator day on the
  twin ([REHEARSAL.md](REHEARSAL.md#the-simulated-field-day)).
- On the real aircraft, SprayLog records where each sortie actually stopped. FieldPilot then
  compares that with the twin and the plan, which turns the assumptions above into measured
  numbers.
