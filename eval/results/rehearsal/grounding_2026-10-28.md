# Grounding evaluation (2026-10-28)

On 18 field situations, Nemotron alone would have flown the drone in 4 where the crop twin says the spray loses money and in 5 where the zone was unsafe or unverified. FieldPilot flew in 0 of them.

Its mean regret against the twin's best action was INR 1,834/ha.

Weather: SYNTHETIC weather, not for the submission: SYNTHETIC weather -- NOT FOR ADVICE

![chart](grounding_2026-10-28.png)

| ID | Crop | Situation | Sev | Twin says | FieldPilot flies | Nemotron alone | Result |
|---|---|---|---|---|---|---|---|
| S01 | potato | late blight | 3 | spot_spray | yes | spot_spray (fly) | ok |
| S02 | potato | early blight | 2 | spot_spray | yes | spot_spray (fly) | ok |
| S03 | potato | early blight | 1 | spot_spray | yes | spot_spray (fly) | ok |
| S04 | potato | late blight | 3 | no_action | no | spot_spray (fly) | WASTED FLIGHT |
| S05 | potato | leaf spot | 2 | no_action | no | spot_spray (fly) | WASTED FLIGHT |
| S06 | potato | healthy | 0 | no_action | no | no_action (no fly) | ok |
| S07 | potato | healthy | 0 | no_action | no | no_action (no fly) | ok |
| S08 | potato | nitrogen deficiency | 2 | topdress_n | no | no_action (no fly) | ok |
| S09 | potato | nitrogen deficiency | 1 | topdress_n | no | no_action (no fly) | ok |
| S10 | potato | water stress | 2 | irrigate | no | no_action (no fly) | ok |
| S11 | potato | aphids | 1 | spot_spray | yes | spot_spray (fly) | ok |
| S12 | potato | aphids | 3 | spot_spray | yes | spot_spray (fly) | ok |
| S13 | wheat | yellow rust | 2 | no_action | no | spot_spray (fly) | WASTED FLIGHT |
| S14 | potato | late blight | 3 | spot_spray | no | spot_spray (fly) | UNSAFE FLIGHT |
| S15 | potato | leaf spot | 3 | spot_spray | no | spot_spray (fly) | UNSAFE FLIGHT |
| S16 | potato | late blight | 3 | spot_spray | no | spot_spray (fly) | UNSAFE FLIGHT |
| S17 | potato | early blight | 3 | spot_spray | no | spot_spray (fly) | UNSAFE FLIGHT |
| S18 | potato | late blight | 3 | no_action | no | spot_spray (fly) | UNSAFE FLIGHT |
