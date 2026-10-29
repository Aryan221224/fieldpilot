"""The project airframe: a T1-class 10 L, four-rotor, four-nozzle agricultural spray drone.

Every number FieldPilot uses for this aircraft comes from its specification sheet
(docs/AIRFRAME_T1.md, "Agricultural Spraying Drone - specifications, dimensions and BOM
review") or is derived from it here, with the assumption named. Nothing is a vendor
guarantee; calibrate against your own flight logs and a measuring jug.

    python -m fieldpilot.cli airframe          # the profile, the derivations, ArduPilot params
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# The specification sheet, as published (brochure pages 1, 3, 7, 10; BOM rows 8-38)
# ---------------------------------------------------------------------------
SHEET = {
    "tank_l": 10.0,
    "rotors": 4,
    "nozzles": 4,
    "wheelbase_mm": 1300,
    "unfolded_mm": (920, 920, 480),
    "folded_mm": (582, 582, 480),
    "dry_mass_kg": 10.0,                 # "weight without battery"
    "standard_takeoff_kg": 24.0,         # "standard / fully loaded weight"
    "mtow_kg": 28.0,
    "hover_min_at": {24.0: 12.0, 14.0: 28.0},
    "spray_width_m": 4.0,                # spec table (3-5 m in the narrative)
    "pump_max_l_min": 3.5,               # brochure, system; BOM pump alone: 5.5 L/min at 0.8 MPa
    "max_operating_speed_ms": 7.0,
    "max_flight_speed_ms": 10.0,
    "rc_range_m": 1000,                  # unobstructed
    "operating_temp_c": (-10.0, 40.0),
    "charge_min_advertised": 30,
    "work_rate_acres_h": 10.0,           # spec table (10-15 elsewhere)
    "pump_cutoff_ms": 0.5,               # "the pump stops below 0.5 m/s"
    "flow_follows_speed": True,
    "battery": "2 x 16000 mAh Li-Po (cell count not stated; the kit's charger is 12S)",
    "charger": "PC1080 12S, 1 unit",
    "pump": "24 V diaphragm, 0.8 MPa, 5.5 L/min, 285 g, outlet 10/6 mm",
    "motors": "4 x 120 KV with ESC, 34.7-inch folding propellers",
}

# ---------------------------------------------------------------------------
# Assumptions (each one is printed by `cli airframe`; change it here if you measure better)
# ---------------------------------------------------------------------------
CELLS = 12                    # from the 12S charger in the kit
CELL_V = 3.7
PACK_AH = 16.0
PACK_WH = CELLS * CELL_V * PACK_AH                      # 710.4 Wh
# One pack per flight: dry 10 kg + 10 L liquid + pack = 24 kg standard take-off weight.
PACK_KG = SHEET["standard_takeoff_kg"] - SHEET["dry_mass_kg"] - SHEET["tank_l"]   # 4.0 kg
HOVER_DEPTH = 0.85            # share of the pack a vendor hover-time figure uses
RESERVE = 0.30                # FieldPilot never plans into the last 30 %
CRUISE_FACTOR = 1.05          # forward flight with a tank: a little above hover, conservatively
SPRAY_SPEED_MS = 4.5          # India SOP-2023 band 4.5-5.0 m/s; airframe allows up to 7
TRANSIT_SPEED_MS = 6.0        # well under the 10 m/s maximum
CLIMB_MS = 2.0
SPRAY_RATE_L_HA = 20.0        # low-volume drone application
RANGE_M = 500.0               # half the 1000 m radio range; the pilot keeps line of sight
CHARGE_MIN_PLAN = 45          # 30 min advertised needs ~1.4 kW into a 710 Wh pack; plan 45
ACCEL_MS2 = 1.5             # assumption: braking / acceleration at each line end (K3 twin and planner)
TURN_SPEED_MS = 1.5         # assumption: headland side-step between lines
PUMP_STALL_DUTY = 0.20      # DC diaphragm pump: no flow below ~20 % duty (range 0.12-0.30;
                            # fit it on the bench with `cli deviation --fit`)
KIT_BATTERY_SETS = 2
KIT_CHARGERS = 1


def hover_power_w(mass_kg: float = SHEET["standard_takeoff_kg"]) -> float:
    """Hover power from the sheet's 12 min at 24 kg, scaled by mass^1.5."""
    p24 = HOVER_DEPTH * PACK_WH / (SHEET["hover_min_at"][24.0] / 60.0)
    return p24 * (mass_kg / 24.0) ** 1.5


def predicted_hover_min(mass_kg: float) -> float:
    return HOVER_DEPTH * PACK_WH / hover_power_w(mass_kg) * 60.0


def t1_spec():
    from .mission import DroneSpec
    p = hover_power_w()
    return DroneSpec(
        name="T1 10 L quad (project airframe)",
        transit_speed_ms=TRANSIT_SPEED_MS, spray_speed_ms=SPRAY_SPEED_MS, climb_speed_ms=CLIMB_MS,
        hover_power_w=round(p, 1), cruise_power_w=round(p * CRUISE_FACTOR, 1),
        battery_wh=round(PACK_WH, 1), reserve_frac=RESERVE,
        tank_ml=SHEET["tank_l"] * 1000.0, swath_m=SHEET["spray_width_m"],
        spray_rate_l_ha=SPRAY_RATE_L_HA, max_range_m=RANGE_M, max_flight_s=900.0,
        pump_max_l_min=SHEET["pump_max_l_min"], max_spray_speed_ms=SHEET["max_operating_speed_ms"],
        pump_cutoff_ms=SHEET["pump_cutoff_ms"], pump_stall_duty=PUMP_STALL_DUTY,
        dry_mass_kg=SHEET["dry_mass_kg"],
        battery_mass_kg=PACK_KG, ref_mass_kg=SHEET["standard_takeoff_kg"], mtow_kg=SHEET["mtow_kg"],
        min_temp_c=SHEET["operating_temp_c"][0], max_temp_c=SHEET["operating_temp_c"][1],
        nozzles=SHEET["nozzles"], rotors=SHEET["rotors"],
        accel_ms2=ACCEL_MS2, turn_speed_ms=TURN_SPEED_MS)


# ---------------------------------------------------------------------------
# India, Drone Rules 2021: categories by maximum all-up weight (drone + payload)
# ---------------------------------------------------------------------------
def india_category(all_up_kg: float) -> str:
    if all_up_kg <= 0.25:
        return "nano"
    if all_up_kg <= 2.0:
        return "micro"
    if all_up_kg <= 25.0:
        return "small"
    if all_up_kg <= 150.0:
        return "medium"
    return "large"


# ---------------------------------------------------------------------------
# ArduPilot starting parameters, for when the flight controller runs ArduPilot
# ---------------------------------------------------------------------------
def ardupilot_params(spec=None, pump_output: int = 9) -> dict[str, float]:
    """Sprayer + failsafe starting values for this airframe. Verify each on the aircraft.

    ArduPilot's sprayer makes flow follow ground speed and stops the pump below a minimum
    speed: the same law the T1 sheet describes. SPRAY_PUMP_RATE is the pump output (% of
    full) per 1 m/s, so full output must equal the sheet's 3.5 L/min through 4 nozzles -
    calibrate SERVOn_MAX with a measuring jug.
    """
    s = spec or t1_spec()
    per_ms = s.spray_rate_l_ha * s.swath_m * 60.0 / 10_000.0     # L/min per 1 m/s
    rate_pct = 100.0 * per_ms / s.pump_max_l_min
    mah = PACK_AH * 1000
    return {
        f"SERVO{pump_output}_FUNCTION": 22,          # SprayerPump
        "SPRAY_ENABLE": 1,
        "SPRAY_PUMP_RATE": round(rate_pct, 1),       # % per m/s
        "SPRAY_PUMP_MIN": 0,
        "SPRAY_SPEED_MIN": round(s.pump_cutoff_ms * 100),   # cm/s
        "WPNAV_SPEED": round(s.spray_speed_ms * 100),        # cm/s
        "BATT_CAPACITY": mah,
        "BATT_LOW_MAH": round(mah * s.reserve_frac),
        "BATT_LOW_VOLT": round(CELLS * 3.55, 1),
        "BATT_CRT_VOLT": round(CELLS * 3.40, 1),
        "BATT_FS_LOW_ACT": 2,                        # RTL
        "BATT_FS_CRT_ACT": 1,                        # land
        "FENCE_ENABLE": 1,
        "FENCE_TYPE": 3,                             # altitude + circle
        "FENCE_RADIUS": round(s.max_range_m),
        "FENCE_ALT_MAX": 15,
        "FENCE_ACTION": 1,                           # RTL
        "RTL_ALT": 1000,                             # cm
        "FS_THR_ENABLE": 1,                          # RC loss -> RTL
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def work_rate(spec=None, field_ha: float = 4.0) -> dict:
    """Hectares per hour of flying + swap/refill time on a square field, from the sortie planner."""
    from .dayplan import sorties_for
    from .sorties import GROUND_TURNAROUND_S
    s = sorties_for(field_ha, "t1_10l")
    hours = (s["flight_min"] * 60 + GROUND_TURNAROUND_S * max(0, s["n"] - 1)) / 3600
    return {"field_ha": field_ha, "sorties": s["n"], "flight_min": round(s["flight_min"], 1),
            "ha_per_h": round(field_ha / hours, 2), "acres_per_h": round(field_ha / hours * 2.471, 1)}


def kit_rate(spec=None, hours: float = 9.0, battery_sets: int = KIT_BATTERY_SETS,
             chargers: int = KIT_CHARGERS, charge_min: float = CHARGE_MIN_PLAN) -> dict:
    """Full-tank sorties a day with the kit as shipped: which runs out first, packs or charger.

    ``hours`` of spray window (India SOP-2023: 06-11 and 14-18 = 9 h). Ground turnaround and
    one pack per sortie, as the day planner assumes. Drive time between farms ignored, so this
    is the ceiling for one large field.
    """
    from .sorties import GROUND_TURNAROUND_S
    s = spec or t1_spec()
    w = work_rate(s, 4.0)
    fly = w["flight_min"] / w["sorties"]
    ha_per = s.tank_ml / 1000.0 / s.spray_rate_l_ha
    ready = [0.0] * battery_sets
    free = [0.0] * chargers
    t, n = 0.0, 0
    while True:
        k = min(range(battery_sets), key=lambda i: ready[i])
        start = max(t, ready[k])
        land = start + fly
        if land > hours * 60:
            break
        n += 1
        c = min(range(chargers), key=lambda i: free[i])
        free[c] = max(land, free[c]) + charge_min
        ready[k] = free[c]
        t = land + GROUND_TURNAROUND_S / 60.0
    return {"window_h": hours, "battery_sets": battery_sets, "chargers": chargers,
            "charge_min": charge_min, "sorties": n, "ha": round(n * ha_per, 1),
            "ha_per_h": round(n * ha_per / hours, 2),
            "aircraft_only_ha_per_h": w["ha_per_h"],
            "aircraft_busy_pct": round(100 * n * fly / (hours * 60))}


def report() -> dict:
    s = t1_spec()
    full = s.mass_kg(s.tank_ml)
    return {
        "airframe": s.name,
        "sheet": SHEET,
        "derived": {
            "pack_wh": round(PACK_WH, 1), "pack_kg": PACK_KG,
            "hover_w_full_24kg": round(hover_power_w(24.0)),
            "hover_w_empty_14kg": round(hover_power_w(14.0)),
            "hover_min_predicted_14kg": round(predicted_hover_min(14.0), 1),
            "hover_min_sheet_14kg": SHEET["hover_min_at"][14.0],
            "usable_wh": round(s.usable_wh, 1),
            "flow_l_min_at_spray_speed": round(s.flow_l_min(), 2),
            "pump_limited_speed_ms": round(s.pump_limited_speed_ms(), 2),
            "takeoff_kg_full_tank": round(full, 1),
            "india_category_full_tank": india_category(full),
            "india_category_at_mtow": india_category(SHEET["mtow_kg"]),
            "work_rate": work_rate(s),
            "work_rate_sheet_acres_h": SHEET["work_rate_acres_h"],
            "kit_as_shipped": kit_rate(s),
            "kit_4_packs_2_chargers": kit_rate(s, battery_sets=4, chargers=2),
        },
        "assumptions": {
            "cells": CELLS, "hover_depth": HOVER_DEPTH, "reserve": RESERVE,
            "cruise_factor": CRUISE_FACTOR, "spray_speed_ms": SPRAY_SPEED_MS,
            "spray_rate_l_ha": SPRAY_RATE_L_HA, "range_m": RANGE_M,
            "charge_min_plan": CHARGE_MIN_PLAN, "kit": {"battery_sets": KIT_BATTERY_SETS,
                                                        "chargers": KIT_CHARGERS},
        },
        "ardupilot_params": ardupilot_params(s),
    }
