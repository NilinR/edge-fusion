#include <Servo.h>

const int TRIG_PIN = 9;
const int ECHO_PIN = 10;
const int SERVO_PIN = 6;

Servo gateServo;
unsigned long lastSendMs = 0;
const unsigned long SAMPLE_INTERVAL_MS = 250; // 4 readings per second

void setup() {
  Serial.begin(115200);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  gateServo.attach(SERVO_PIN);
  gateServo.write(0); // Default closed position
}

float readDistanceCm() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  // 30ms timeout (~5m max) prevents blocking incoming serial commands
  long duration = pulseIn(ECHO_PIN, HIGH, 30000);
  if (duration == 0) {
    return 100.0; // Default safe distance if no echo
  }
  return (duration * 0.0343) / 2.0;
}

void loop() {
  // 1. Check for actuation commands from Edge Fusion
  if (Serial.available() > 0) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd == "TRIGGER_SERVO") {
      gateServo.write(90);
    } else if (cmd == "NORMAL") {
      gateServo.write(0);
    }
  }

  // 2. Periodically stream ultrasonic distance as JSON
  unsigned long now = millis();
  if (now - lastSendMs >= SAMPLE_INTERVAL_MS) {
    lastSendMs = now;
    float dist = readDistanceCm();
    Serial.print("{\"value\":");
    Serial.print(dist, 2);
    Serial.println("}");
  }
}