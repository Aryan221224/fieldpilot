// pump_bridge - drives a real spray pump from FieldPilot's bench bridge.
//
//   laptop (python -m fieldpilot.bench --sink serial:COM6)  --USB-->  this board  -->  pump
//
// Protocol, one line each, 115200 baud:
//   "?"        -> "PUMP_BRIDGE 1 mode=<flow|switch|servo> pin=<n> deadman_ms=<n>"
//   "P<pwm>"   -> "OK <pwm>"        (800..2200 us; anything else -> "ERR <line>")
//
// Three output modes, chosen at compile time (FLOW is the default):
//   FLOW    logic-level MOSFET on PIN_OUT, PWM duty = (pwm - 1100) / 800: 1100 off, 1900 full.
//           With `bench --flow-law t1_10l` the duty follows the simulated ground speed the
//           way the T1's controller does, and drops to 0 below 0.5 m/s. Plain on/off
//           commands (1900 / 1100) still mean full / off. Do not use with a relay.
//   SWITCH  MOSFET or relay on PIN_OUT: pump ON while pwm >= THRESH_US
//   SERVO   servo signal on PIN_OUT for a pump driver/ESC that takes one
//
// T1 bench (docs/BENCH_RIG.md, Option C): the drone's own 24 V pump, unplugged from the
// K3-A Pro, on an IRLZ44N low-side switch from a 24 V supply through a 5 A fuse, with a
// 1N5822 flyback diode across the pump. Drone flight battery disconnected, props off.
//
// Dead-man: no valid command for DEADMAN_MS -> pump OFF. Unplugging the USB cable,
// closing the laptop or killing the bridge all stop the pump within 1.5 s.
//
// Boards: Arduino Uno / Nano / Pro Micro (Servo.h), ESP32 (ESP32Servo library).

#define MODE_SWITCH 1
#define MODE_SERVO  2
#define MODE_FLOW   3
#ifndef MODE
#define MODE MODE_FLOW
#endif

#if MODE == MODE_SERVO
  #if defined(ESP32)
    #include <ESP32Servo.h>
  #else
    #include <Servo.h>
  #endif
  Servo out;
#endif

const uint8_t  PIN_OUT     = 5;      // MOSFET gate / relay IN / servo signal
const uint8_t  PIN_LED     = LED_BUILTIN;
const uint16_t THRESH_US   = 1500;
const uint16_t OFF_US      = 1100;
const uint16_t FULL_US     = 1900;
const unsigned long DEADMAN_MS = 1500;

char buf[24];
uint8_t n = 0;
unsigned long lastCmd = 0;
uint16_t current = OFF_US;
bool timedOut = true;

void apply(uint16_t us) {
  current = us;
#if MODE == MODE_FLOW
  long duty = us <= OFF_US ? 0 : us >= FULL_US ? 255 : (long)(us - OFF_US) * 255 / (FULL_US - OFF_US);
  bool on = duty > 0;
  analogWrite(PIN_OUT, (int)duty);          // D5 on an Uno/Nano: ~980 Hz
#elif MODE == MODE_SERVO
  bool on = us >= THRESH_US;
  out.writeMicroseconds(us);
#else
  bool on = us >= THRESH_US;
  digitalWrite(PIN_OUT, on ? HIGH : LOW);
#endif
  digitalWrite(PIN_LED, on ? HIGH : LOW);   // visible on camera
}

void handle(const char *line) {
  if (line[0] == '?' && line[1] == '\0') {
    Serial.print(F("PUMP_BRIDGE 1 mode="));
    Serial.print(MODE == MODE_SERVO ? F("servo") : MODE == MODE_FLOW ? F("flow") : F("switch"));
    Serial.print(F(" pin="));
    Serial.print(PIN_OUT);
    Serial.print(F(" deadman_ms="));
    Serial.println(DEADMAN_MS);
    return;
  }
  if (line[0] == 'P') {
    long v = atol(line + 1);
    if (v >= 800 && v <= 2200) {
      apply((uint16_t)v);
      lastCmd = millis();
      timedOut = false;
      Serial.print(F("OK "));
      Serial.println(v);
      return;
    }
  }
  Serial.print(F("ERR "));
  Serial.println(line);
}

void setup() {
  pinMode(PIN_LED, OUTPUT);
#if MODE == MODE_SERVO
  out.attach(PIN_OUT);
#else
  pinMode(PIN_OUT, OUTPUT);
#endif
  apply(OFF_US);
  Serial.begin(115200);
}

void loop() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      buf[n] = '\0';
      if (n) handle(buf);
      n = 0;
    } else if (n < sizeof(buf) - 1) {
      buf[n++] = c;
    } else {
      n = 0;                       // overlong line: drop it
    }
  }
  if (!timedOut && millis() - lastCmd > DEADMAN_MS) {
    apply(OFF_US);
    timedOut = true;
  }
}
