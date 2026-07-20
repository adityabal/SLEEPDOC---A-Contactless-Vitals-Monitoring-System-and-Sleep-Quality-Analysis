# SleepDoc — Contactless Sleep & Vital-Sign Monitoring System

SleepDoc is a fully contactless, radar-based sleep and vital-sign monitoring device built on a Raspberry Pi 3 Model B. It uses a 60 GHz FMCW mmWave radar to detect sub-millimeter chest-wall displacement caused by breathing and heartbeat — no wearable, no skin contact, no clinical setup.

This repository documents the **hardware, signal processing, and embedded backend** work carried out as part of an internship at the **Department of Electrical Engineering, IIT Kharagpur**, under **Prof. Dr. Aurobinda Routray**, between May–June 2026.

---

## Why SleepDoc

Existing sleep monitoring falls into two camps: clinical polysomnography (accurate, but expensive and intrusive) or wearables (convenient, but uncomfortable overnight and prone to skin irritation/battery issues). SleepDoc aims to sit in between — a bedside device that monitors sleep without touching the user at all.

---

## Team & Contributions

SleepDoc was built by a two-person team. This repo is scoped to the author's (hardware lead's) contributions.

| Contributor | Responsibility |
|---|---|
| **Aditya Bal** (author) | Sensor hardware, radar signal processing pipeline, mechanical enclosure (CAD), embedded multithreaded backend, AI sleep-stage classification integration |
| **Pratyaksh Das** | Touchscreen GUI, Flask REST API, Flutter mobile application |
| **Kumar Shree** | Assisted with CAD model design for the enclosure |

Full credit for the GUI, API, and mobile app goes to **Pratyaksh Das** — `gui2.py` in this repo is his work, included here to show the complete working system alongside the hardware backend.

---

## System Architecture

SleepDoc is organized into five subsystems:

1. **Primary Sensing Layer** — BM502 mmWave radar for contactless heart rate & breathing rate detection
2. **Environmental Monitoring Layer** — BME280 sensor for temperature, humidity, and pressure
3. **Interaction Layer** — APDS9960 gesture sensor for touchless session control
4. **Ambient Feedback Layer** — Dual RGB LEDs for visual status feedback
5. **Processing & Control Layer** — Raspberry Pi 3 Model B running the multithreaded backend and AI inference

### Hardware Components

| Component | Interface | Notes |
|---|---|---|
| Raspberry Pi 3 Model B V1.2 | — | Central processing unit; 3.5" touchscreen attached |
| BM502 mmWave Radar (60 GHz FMCW) | USB | Auto-detected via CP2105 Enhanced serial port |
| BME280 Environmental Sensor | I2C | Temperature, humidity, pressure |
| APDS9960 Gesture Sensor | I2C | LEFT swipe starts session, RIGHT swipe stops it |
| RGB LED 1 (common-anode) | GPIO 17, 27, 22 | Ambient feedback |
| RGB LED 2 (common-cathode) | GPIO 23, 24, 25 | Ambient feedback (inverted logic vs. LED 1) |
| MAX98357A I2S DAC + Speaker | — | Discarded from final build (hardware damage) |

### Mechanical Design

The enclosure was designed in SolidWorks — a parent box for the Pi and touchscreen, dedicated holder boxes for the BME280 and APDS9960, a cover board for LED/sensor mounting, and a radar cover board that keeps the BM502's line of sight clear. A 3D-printed PLA prototype validated the fit before the hardware was migrated from a breadboard prototype to a soldered PCB for reliable overnight operation.

---

## Radar Signal Processing Pipeline

The BM502's raw `unwrapPhasePeak_mm` output goes through a 10-stage pipeline to extract usable heart rate and breathing rate values:

1. **Raw Phase Acquisition** — sampled at ~20 Hz
2. **Rolling Buffer** — last 300 samples (~15s) retained for FFT analysis
3. **DC Offset Removal** — subtract signal mean to remove baseline drift
4. **Bandpass Filter (Breathing)** — Butterworth, 0.1–0.5 Hz
5. **Bandpass Filter (Heart Rate)** — Butterworth, 0.8–2.0 Hz
6. **FFT Peak Detection** — dominant frequency per filtered signal
7. **Hz → BPM Conversion**
8. **Median Filter** (window = 5) — removes spike outliers
9. **Exponential Moving Average** (α = 0.3) — smooths output
10. **Calibration** — +3 BPM correction for observed heart rate overestimation

Corrupt/out-of-range raw readings (0.0 or >500) are rejected before entering the buffer, preventing single bad samples from distorting FFT-based estimates.

---

## Software Architecture

- `vsd_on_startup.py` auto-launches on boot and coordinates all sensor threads, the signal processing pipeline, and the GUI.
- Each subsystem (radar, environmental polling, gesture detection, LED control, GUI updates) runs on its own thread to avoid blocking on the resource-constrained Pi 3.
- Vitals and control state are shared via `/tmp/live_vitals.txt`, read by the GUI and, through the Flask API, the mobile app.
- The radar's serial port is auto-detected at runtime (via its CP2105 Enhanced port) rather than hard-coded, with a udev rule providing a stable symlink.
- Designed and validated for continuous 6–8 hour operation, matching a full night's sleep.

---

## Challenges & Fixes

| Challenge | Fix |
|---|---|
| Radar's USB port assignment changed across reboots | Dynamic port auto-detection via CP2105 signature + udev symlink |
| Corrupt/out-of-range radar phase values distorting FFT estimates | Outlier rejection before buffering (reject 0.0 or >500) |
| WS2812B LED ring damaged during development | Replaced with two discrete RGB LEDs; handled inverted common-anode/common-cathode drive logic separately |
| System crashed after ~15 minutes of runtime | Traced to an undrained `stdout`/`stderr` pipe buffer filling up (65,536-byte OS limit) when the GUI launched the backend via `subprocess.Popen`; fixed by not redirecting output to an unread pipe |
| Multithreaded stability on constrained hardware | Isolated each sensor/control pathway into its own thread, coordinated via shared state files |

---

## AI-Based Sleep Stage Classification (Ongoing)

A CNN-LSTM model was trained on the [DREAMT dataset](https://physionet.org/) (100 patients) to classify sleep stages from heart rate data into 7 categories: Wake, N1, N2, N3, REM, Movement, Missing.

- A non-blocking inference thread was built to collect a rolling 32-second buffer (640 samples at 20 Hz), apply per-patient standard scaling, and produce a prediction every ~32 seconds.
- **Current status:** the model was converted to TensorFlow Lite for on-device deployment on the Pi 3, but the TFLite model's predictions were found to be inaccurate. This is an active area of ongoing work beyond the internship period, and the AI model files are **not included in this repo** while under revision.

---

## Future Scope

- Develop a more accurate on-device sleep-stage model (current TFLite model needs replacement)
- Optimize inference via pruning, quantization, or a lighter architecture
- Migrate to Raspberry Pi 5 for more compute headroom
- Deliver session summaries as PDFs via WhatsApp/Telegram
- Validate heart rate/breathing rate accuracy against a clinical reference device
- Cloud-based storage and sync for long-term sleep trend analysis

---

## Repository Contents

- `vsd_on_startup.py` — Hardware backend: sensor integration, radar signal processing pipeline, multithreaded control (author's work)
- `gui2.py` — Touchscreen GUI (built by Pratyaksh Das)

AI model files are excluded while the sleep-stage classifier is being reworked.

---

## Acknowledgements

Thanks to Prof. Dr. Aurobinda Routray for supervising this project, to Pratyaksh Das for the GUI/API/mobile app work, and to Kumar Shree for help with the CAD enclosure design.

## References

- Joybien BM502 Radar Sensor Datasheet
- Bosch Sensortec BME280 Documentation
- Adafruit APDS9960 Guide
- DREAMT Dataset — PhysioNet
- TensorFlow Lite Documentation
