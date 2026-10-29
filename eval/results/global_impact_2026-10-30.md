# Global impact estimate (ESTIMATE, from our test benchmarks)

World agricultural-drone activity, floor: **209.6 M hectare-treatments / year** (China 203 M + USA 6.64 M, 2025).

Gains measured per useful hectare in the benchmark (FieldPilot vs naive day): battery cycles -76 %, flight minutes -77 %, leftover mix -98 %.

Counting only **25 %** of those gains:

| saved per year | central (r = 0.25) | r = 0.10 | r = 0.50 |
|---|---|---|---|
| battery cycles | 35.0 M | 14.0 M | 70.1 M |
| battery packs (1,500-cycle) | 23.4 k | 9.3 k | 46.7 k |
| charging energy | 48.8 GWh | 19.5 GWh | 97.6 GWh |
| CO2 | 21.7 kt | 8.7 kt | 43.4 kt |
| battery wear | 65.4 M USD | 26.2 M USD | 130.8 M USD |
| pilot flight hours | 3.4 M h | 1.3 M h | 6.7 M h |
| wasted tank mix | 73.5 M L | 29.4 M L | 146.9 M L |

Cross-check: 184 M cycles a year today over 600,000 drones = 307 cycles per drone per year.

## Sources

- about 203 million hectare-treatments by agricultural drones in China in 2025 (178 million in 2024). CleanTechnica, 20 Sep 2026, https://cleantechnica.com/2026/09/20/china-agricultural-drones-30-times-us/
- about 16.4 million acres = 6.64 million hectare-treatments in the USA in 2025 (American Spray Drone Coalition 2025 Impact Survey). CleanTechnica, 20 Sep 2026 (same article)
- over 600,000 DJI agricultural drones in use globally at the end of 2025. DJI Agriculture annual report, 29 Apr 2026, https://www.aap.com.au/aapreleases/cision20260429ae46633/
- DJI DB1560 (Agras T40/T50): 30,000 mAh, 52.22 V, about 1,567 Wh, up to 1,500 cycles, USD 2,799. https://www.dronefly.com/products/dji-db1560-intelligent-flight-battery
- world-average CO2 intensity of electricity generation 445 g/kWh in 2024. IEA Electricity 2025, https://iea.org/reports/electricity-2025/emissions
- FieldPilot efficiency benchmark, 5 regions x 3 days, 105 bookings, live forecasts, SIMULATED flights. eval/results/efficiency_2026-10-30.json

Assumptions: 80 % depth of discharge per cycle, 90 % charger efficiency, DB1560 pack for the whole fleet, benchmark baseline per-hectare use as today's real-world use. Electricity price and chemical value are not counted, so the money figure is battery wear only.
