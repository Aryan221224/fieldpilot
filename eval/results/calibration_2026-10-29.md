# Perception gate calibration, 2026-10-29

Vision model `nvidia/Nemotron-3.5-Super-VL` on 150 public, labelled potato leaf photos (early blight 55, late blight 55, healthy 40). The model returns a probability for each candidate diagnosis (healthy, early blight, late blight, other problem). Gates are fitted on 74 photos and tested on the other 76.

| | |
|---|---|
| Model's most likely diagnosis correct (all photos) | 68.0% |
| Model right about healthy vs diseased (all photos) | 98.0% |
| Which disease: gate at alpha 0.1 (threshold p >= 0.10) | coverage 100.0%, mean set 3.42 of 4 labels |
| Healthy vs diseased: gate at alpha 0.05 (threshold p >= 0.50) | coverage **98.7%** (target >= 95%) |
| Held-out decisions: healthy, passed without a human | 18 right, 0 wrong |
| Held-out decisions: one disease named without a human | 0 right, 0 wrong |
| Held-out decisions: sent to a human | 58 of 76 (56 as 'diseased, pick which') |

Confusion (true -> model's most likely): early blight -> early blight 31; early blight -> late blight 23; early blight -> other problem 1; healthy -> healthy 39; healthy -> other problem 1; late blight -> early blight 19; late blight -> healthy 2; late blight -> late blight 32; late blight -> other problem 2

What it means: on these photos Nemotron 3.5 Super VL is reliable about healthy versus diseased, but close to a coin toss between early and late blight. The gate therefore lets a healthy zone through on its own and sends every diseased zone to the operator with the candidate diseases, instead of trusting the model's first guess.

These are leaf close-ups from public datasets, not drone scans of our fields. The coverage guarantee holds for photos like these; a season of labelled field scans should replace this calibration (`python -m fieldpilot.cli calibrate labels.csv`).
