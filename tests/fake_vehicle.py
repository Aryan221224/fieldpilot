"""A minimal MAVLink vehicle for protocol tests: heartbeats, telemetry, the
mission-upload handshake, arming and mode changes. Not a flight simulator."""
from __future__ import annotations

import threading
import time

from pymavlink import mavutil

from fieldpilot import winsock

winsock.apply()

M = mavutil.mavlink


class FakeVehicle:
    def __init__(self, port: int, autopilot: str = "px4", battery: int = 90, fix: int = 3,
                 reject_mission: bool = False, refuse_arm: bool = False):
        self.port = port
        self.ap = M.MAV_AUTOPILOT_PX4 if autopilot == "px4" else M.MAV_AUTOPILOT_ARDUPILOTMEGA
        self.battery, self.fix = battery, fix
        self.reject_mission, self.refuse_arm = reject_mission, refuse_arm
        self.items: dict[int, object] = {}
        self.expected = 0
        self.armed = False
        self.mission_started = False
        self.custom_mode = 0
        self.commands: list[int] = []
        self._stop = threading.Event()
        self.conn = winsock.vehicle_connection(mavutil, port, source_system=1, source_component=1)

    def start(self):
        threading.Thread(target=self._tx, daemon=True).start()
        threading.Thread(target=self._rx, daemon=True).start()
        return self

    def stop(self):
        self._stop.set()

    def _tx(self):
        while not self._stop.is_set():
            base = M.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED | (M.MAV_MODE_FLAG_SAFETY_ARMED if self.armed else 0)
            self.conn.mav.heartbeat_send(M.MAV_TYPE_QUADROTOR, self.ap, base, self.custom_mode,
                                         M.MAV_STATE_STANDBY)
            self.conn.mav.global_position_int_send(0, int(28.4498 * 1e7), int(77.5837 * 1e7),
                                                   200000, 3000 if self.armed else 0, 0, 0, 0, 0)
            self.conn.mav.sys_status_send(0, 0, 0, 500, 16000, 1000, self.battery, 0, 0, 0, 0, 0, 0)
            self.conn.mav.gps_raw_int_send(0, self.fix, int(28.4498 * 1e7), int(77.5837 * 1e7),
                                           200000, 100, 100, 0, 0, 12)
            time.sleep(0.1)

    def _rx(self):
        while not self._stop.is_set():
            msg = self.conn.recv_match(blocking=True, timeout=0.5)
            if msg is None:
                continue
            t = msg.get_type()
            if t == "MISSION_COUNT":
                self.expected, self.items = msg.count, {}
                self.conn.mav.mission_request_int_send(255, 0, 0, M.MAV_MISSION_TYPE_MISSION)
            elif t == "MISSION_ITEM_INT":
                self.items[msg.seq] = msg
                if msg.seq + 1 < self.expected:
                    self.conn.mav.mission_request_int_send(255, 0, msg.seq + 1, M.MAV_MISSION_TYPE_MISSION)
                else:
                    res = M.MAV_MISSION_DENIED if self.reject_mission else M.MAV_MISSION_ACCEPTED
                    self.conn.mav.mission_ack_send(255, 0, res, M.MAV_MISSION_TYPE_MISSION)
            elif t == "COMMAND_LONG":
                self.commands.append(msg.command)
                ok = M.MAV_RESULT_ACCEPTED
                if msg.command == M.MAV_CMD_COMPONENT_ARM_DISARM:
                    if self.refuse_arm:
                        ok = M.MAV_RESULT_DENIED
                    else:
                        self.armed = msg.param1 == 1
                elif msg.command == M.MAV_CMD_MISSION_START:
                    self.mission_started = True
                elif msg.command == M.MAV_CMD_DO_SET_MODE:
                    self.custom_mode = int(msg.param2)
                self.conn.mav.command_ack_send(msg.command, ok)
            elif t == "SET_MODE":
                self.custom_mode = msg.custom_mode
