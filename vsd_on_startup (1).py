# SleepDoc - Vital Signs Detection
# Swipe LEFT to start monitoring, RIGHT to pause
# CTRL+C to stop

import time
import serial
import datetime
import threading
import json
import csv
import os
import math
import socket
import subprocess
import traceback
import numpy as np
from threading import Thread
from flask import Flask, request, jsonify, send_from_directory, redirect, send_file
from flask_cors import CORS
from collections import deque
import joblib
from urllib.parse import quote

try:
    import tflite_runtime.interpreter as tflite
    tf_available = True
except Exception as e:
    print(f"[TFLite] Not available: {e}")
    tf_available = False

try:
    from scipy.signal import butter, filtfilt
    from scipy.fft import fft, fftfreq
    scipy_available = True
except Exception as e:
    print(f"[SciPy Import] Not available: {e}")
    scipy_available = False

# ── Optional sensor imports ───────────────────────────────────────────────────
try:
    import board
    import busio
    board_available = True
except Exception as e:
    print(f"[Board] Not available: {e}")
    board_available = False

try:
    import smbus2
    import bme280
    bme280_available = True
except Exception as e:
    print(f"[BME280 Import] Not available: {e}")
    bme280_available = False

try:
    from mmWave import vitalsign
    mmwave_available = True
except Exception as e:
    print(f"[mmWave Import] Not available: {e}")
    mmwave_available = False

try:
    from gpiozero import RGBLED
    gpiozero_available = True
except Exception as e:
    print(f"[GPIOZero Import] Not available: {e}")
    gpiozero_available = False

FLASK_PORT  = 5000
WEB_APP_DIR = "/home/raspberry/Desktop/SLEEPDOC/web_app"
def get_local_ip():
    """Get the Pi's WiFi IP.

    Reads wlan0's assigned address directly first — this works whether or not
    there's a default gateway, which matters once the Pi is its own access
    point (no upstream internet). Falls back to the old routing-table trick,
    then to 127.0.0.1 if nothing is up at all.
    """
    try:
        out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show", "wlan0"],
            stderr=subprocess.DEVNULL
        ).decode()
        return out.split("inet ")[1].split("/")[0]
    except Exception:
        pass
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()
        
def find_radar_port():
    """Automatically finds the BM502 radar data port"""
    import serial.tools.list_ports
    
    ports = serial.tools.list_ports.comports()
    
    enhanced_port = None
    standard_port = None
    
    for port in ports:
        desc = str(port.description)
        # CP2105 has two ports:
        # Enhanced = DATA port (what we need!!)
        # Standard = CONFIG port (ignore!!)
        if "Enhanced" in desc:
            enhanced_port = port.device
            print(f"[Radar] Found data port at {port.device}!!")
        elif "Standard" in desc:
            standard_port = port.device
            print(f"[Radar] Found config port at {port.device}!!")
    
    # Always use Enhanced port for data!!
    if enhanced_port:
        return enhanced_port
    
    # Fallback — try ttyUSB0 then ttyUSB1
    for candidate in ["/dev/ttyUSB0", "/dev/ttyUSB1"]:
        try:
            test = serial.Serial(candidate, 921600, timeout=1)
            test.close()
            print(f"[Radar] Fallback port at {candidate}!!")
            return candidate
        except:
            continue
    
    print("[Radar] No radar port found!!")
    return None   

QR_PATH = "/home/raspberry/Desktop/SLEEPDOC/qr_code.png"

def generate_qr():
    """Generate a QR code pointing at this Pi's dashboard, for the touchscreen
    GUI to display. Retries briefly in case wlan0 hasn't been assigned an IP
    yet right at boot (e.g. AP mode coming up)."""
    try:
        import qrcode
    except ImportError:
        print("[QR] 'qrcode' package not installed — run: pip install qrcode[pil]")
        return

    ip = get_local_ip()
    for _ in range(10):
        if ip != "127.0.0.1":
            break
        time.sleep(1)
        ip = get_local_ip()

    url = f"http://{ip}:{FLASK_PORT}"
    try:
        os.makedirs(os.path.dirname(QR_PATH), exist_ok=True)
        qrcode.make(url).save(QR_PATH)
        print(f"[QR] Saved QR for {url} -> {QR_PATH}")
    except Exception as e:
        print(f"[QR] Could not generate QR code: {e}")
# LED1 = Common Cathode (common → GND)
# LED2 = Common Anode   (common → 3.3V)

led1 = None
led2 = None
if gpiozero_available:
    try:
        led1 = RGBLED(red=17, green=27, blue=22, active_high=True)
        led2 = RGBLED(red=23, green=24, blue=25, active_high=False)
        print("[LED] RGBLED ready!")
    except Exception as e:
        print(f"[LED] Not available: {e}")
        led1 = led2 = None

# Mood colours — exact hex converted to 0.0–1.0 fractions
LED_COLORS = {
    "sky_blue" : (0.655, 0.780, 0.906),   # #A7C7E7 DEFAULT
    "amber"    : (1.0,   0.549, 0.0  ),   # #FF8C00
    "rose"     : (1.0,   0.753, 0.796),   # #FFC0CB
    "lavender" : (0.902, 0.902, 0.980),   # #E6E6FA
    "sage"     : (0.561, 0.737, 0.561),   # #8FBC8F
}

def set_both_leds(color_tuple):
    if led1: led1.color = color_tuple
    if led2: led2.color = color_tuple

def leds_off():
    if led1: led1.off()
    if led2: led2.off()

def scale_color(color_tuple, brightness):
    """Scale color by brightness (0.0–1.0)"""
    return tuple(c * brightness for c in color_tuple)

def breathing_step(color_tuple, brightness, angle):
    """
    Smooth breathing effect using sine wave.
    Pulses between 15% and 100% of chosen brightness.
    Full cycle = ~4 seconds at 0.05 step / 0.05s sleep.
    """
    pulse = 0.15 + 0.85 * ((math.sin(angle) + 1) / 2)
    return tuple(c * brightness * pulse for c in color_tuple)


def get_led_settings():
    """Read led_mode, led_effect, led_brightness, light_on from /tmp/vsd_selection.json"""
    try:
        with open("/tmp/vsd_selection.json", "r") as f:
            data       = json.load(f)
        mode       = data.get("led_mode",       None)
        effect     = data.get("led_effect",     None)
        brightness = float(data.get("led_brightness", 1.0))
        brightness = max(0.1, min(1.0, brightness))
        light_on   = data.get("light_on",       True)  # default True!!
        return mode, effect, brightness, light_on
    except Exception:
        return None, None, 1.0, True  # default light ON!!

# ── Global State ──────────────────────────────────────────────────────────────
class GlobalV:
    hr               = None
    br               = None
    temp_c           = None
    pressure         = None
    humidity         = None
    status           = "pause"
    # Radar CSV
    radar_csvfile    = None
    radar_csvwriter  = None
    # Environment CSV
    env_csvfile      = None
    env_csvwriter    = None
    # Combined CSV
    combined_csvfile   = None
    combined_csvwriter = None
    # Session info
    session_start    = None
    session_folder   = None
    session_day      = None
    session_start_ts = None
    sleep_stage      = "Analysing..."
    sleep_score      = 0

gv   = GlobalV()
lock = threading.Lock()

# ── AI Buffer + Model ─────────────────────────────────────────────────────────
ai_hr_buffer  = deque(maxlen=57600)   # raw HR values, before any smoothing
stage_history = []   # all stage predictions for sleep score calculation

ai_interpreter    = None
ai_input_details  = None
ai_output_details = None
ai_label_encoder  = None

if tf_available:
    try:
        ai_interpreter = tflite.Interpreter(
            model_path="/home/raspberry/Desktop/SLEEPDOC/sleep_model.tflite"
        )
        ai_interpreter.allocate_tensors()
        ai_input_details  = ai_interpreter.get_input_details()
        ai_output_details = ai_interpreter.get_output_details()
        ai_label_encoder  = joblib.load("/home/raspberry/Desktop/SLEEPDOC/sleep_label_encoder.pkl")
        print("[AI] TFLite model and encoder loaded!!")
        print(f"[AI] Input shape : {ai_input_details[0]['shape']}")
        print(f"[AI] Output shape: {ai_output_details[0]['shape']}")

        expected_shape = [1, 640, 7]
        actual_shape   = list(ai_input_details[0]['shape'])
        if actual_shape != expected_shape:
            print("=" * 60)
            print(f"[AI] !!! SHAPE MISMATCH !!! Model expects {actual_shape} "
                  f"but generate_sleep_report builds {expected_shape}.")
            print("[AI] Every inference will fail until this is fixed — "
                  "check which sleep_model.tflite is actually on disk.")
            print("=" * 60)
    except Exception as e:
        print(f"[AI] Model not available: {e}")

# ── CSV — RADAR ───────────────────────────────────────────────────────────────
def start_radar_csv(folder):
    csv_path          = os.path.join(folder, "radar_vitals.csv")
    gv.radar_csvfile  = open(csv_path, "w", newline="")
    gv.radar_csvwriter = csv.writer(gv.radar_csvfile)
    gv.radar_csvwriter.writerow(["time", "heart_rate", "breath_rate"])
    print(f"[CSV] Radar → {csv_path}")

def stop_radar_csv():
    if gv.radar_csvfile:
        gv.radar_csvfile.close()
        gv.radar_csvfile   = None
        gv.radar_csvwriter = None
        print("[CSV] Radar session saved!")

def write_radar_csv():
    if gv.radar_csvwriter and gv.session_start:
        elapsed = round(time.time() - gv.session_start, 3)
        with lock:
            gv.radar_csvwriter.writerow([
                elapsed,
                round(gv.hr, 2) if gv.hr is not None else "",
                round(gv.br, 2) if gv.br is not None else "",
            ])
        # Also update combined CSV
        write_combined_csv()

# ── CSV — ENVIRONMENT ─────────────────────────────────────────────────────────
def start_env_csv(folder):
    csv_path         = os.path.join(folder, "environment.csv")
    gv.env_csvfile   = open(csv_path, "w", newline="")
    gv.env_csvwriter = csv.writer(gv.env_csvfile)
    gv.env_csvwriter.writerow(["time", "temperature", "humidity", "pressure"])
    print(f"[CSV] Environment → {csv_path}")

def stop_env_csv():
    if gv.env_csvfile:
        gv.env_csvfile.close()
        gv.env_csvfile   = None
        gv.env_csvwriter = None
        print("[CSV] Environment session saved!")

def write_env_csv(temp, humidity, pressure, elapsed):
    if gv.env_csvwriter:
        with lock:
            gv.env_csvwriter.writerow([
                elapsed,
                round(temp, 2),
                round(humidity, 2),
                round(pressure, 2),
            ])
        # Also update combined CSV with latest env values
        write_combined_csv()

# ── CSV — COMBINED ────────────────────────────────────────────────────────────
def start_combined_csv(folder):
    csv_path               = os.path.join(folder, "combined_session.csv")
    gv.combined_csvfile    = open(csv_path, "w", newline="")
    gv.combined_csvwriter  = csv.writer(gv.combined_csvfile)
    gv.combined_csvwriter.writerow([
        "time", "heart_rate", "breath_rate",
        "temperature", "humidity", "pressure"])
    print(f"[CSV] Combined → {csv_path}")

def stop_combined_csv():
    if gv.combined_csvfile:
        gv.combined_csvfile.close()
        gv.combined_csvfile   = None
        gv.combined_csvwriter = None
        print("[CSV] Combined session saved!")

def write_combined_csv():
    if gv.combined_csvwriter and gv.session_start:
        elapsed = round(time.time() - gv.session_start, 3)
        with lock:
            gv.combined_csvwriter.writerow([
                elapsed,
                round(gv.hr,       2) if gv.hr       is not None else "",
                round(gv.br,       2) if gv.br       is not None else "",
                round(gv.temp_c,   2) if gv.temp_c   is not None else "",
                round(gv.humidity, 2) if gv.humidity  is not None else "",
                round(gv.pressure, 2) if gv.pressure  is not None else "",
            ])
            gv.combined_csvfile.flush()   # write immediately, don't buffer

# ── Session Management ────────────────────────────────────────────────────────
def start_session():
    ai_hr_buffer.clear()
    stage_history.clear()

    now      = datetime.datetime.now()
    date_str = now.strftime("%d %B %Y")
    time_str = now.strftime("%I-%M%p")          # e.g. 11-00PM

    base       = "/home/raspberry/Desktop/SLEEPDOC/Session Reports"
    day_folder = os.path.join(base, date_str)
    os.makedirs(day_folder, exist_ok=True)

    # Temporary folder name until session ends
    temp_folder = os.path.join(day_folder, f"session_{time_str}_to_ongoing")
    os.makedirs(temp_folder, exist_ok=True)
    gv.session_folder   = temp_folder
    gv.session_day      = date_str
    gv.session_start_ts = time_str              # save start time string
    gv.session_start    = time.time()

    info = {
        "session_id":          now.strftime("%Y-%m-%d_%H-%M-%S"),
        "start_time":          now.strftime("%Y-%m-%d %H:%M:%S"),
        "start_date":          date_str,
        "start_time_readable": time_str,
        "folder":              temp_folder,
    }
    with open(os.path.join(temp_folder, "session_info.json"), "w") as f:
        json.dump(info, f, indent=2)

    start_radar_csv(temp_folder)
    start_env_csv(temp_folder)
    start_combined_csv(temp_folder)
    set_both_leds(LED_COLORS["sky_blue"])

    print(f"[Session] Started → {temp_folder}")

def stop_session():
    # Snapshot before anything else — these must never be read live from
    # gv/ai_hr_buffer inside the background report thread, since a new
    # session could start (and reset both) while the report is still rendering.
    hr_snapshot          = list(ai_hr_buffer)
    session_start_epoch  = gv.session_start

    stop_radar_csv()
    stop_env_csv()
    stop_combined_csv()

    if gv.session_folder and gv.session_start:
        now      = datetime.datetime.now()
        duration = round(time.time() - gv.session_start, 1)
        end_time = now.strftime("%I:%M %p")
        end_date = now.strftime("%d %B %Y")

        info_path = os.path.join(gv.session_folder, "session_info.json")
        try:
            with open(info_path, "r") as f:
                info = json.load(f)
            info.update({
                "end_time":          now.strftime("%Y-%m-%d %H:%M:%S"),
                "end_date":          end_date,
                "end_time_readable": end_time,
                "duration_seconds":  duration,
                "duration_minutes":  round(duration / 60, 2),
            })
            with open(info_path, "w") as f:
                json.dump(info, f, indent=2)

            base       = "/home/raspberry/Desktop/SLEEPDOC/Session Reports"
            day_folder = os.path.join(base, gv.session_day)
            end_str    = now.strftime("%I-%M%p")        # e.g. 08-00AM
            new_name   = f"session_{gv.session_start_ts}_to_{end_str}"
            new_folder = os.path.join(day_folder, new_name)
            os.rename(gv.session_folder, new_folder)
            gv.session_folder = new_folder
            print(f"[Session] Saved → {new_folder}")

            # Classify the whole session and render the PDF in the background,
            # so pausing still responds instantly — this can take a little
            # while for a long session (hundreds of epochs).
            Thread(target=generate_sleep_report,
                   args=(hr_snapshot, new_folder, session_start_epoch), daemon=True).start()
        except Exception as e:
            print(f"[Session] Summary error: {e}")

    gv.session_folder   = None
    gv.session_day      = None
    gv.session_start_ts = None

# ── Write live_vitals.txt ─────────────────────────────────────────────────────
def write_vitals_thread():
    while True:
        try:
            with lock:
                vitals = {
                    "heart_rate":     round(gv.hr, 2)       if gv.hr       is not None else None,
                    "breathing_rate": round(gv.br, 2)       if gv.br       is not None else None,
                    "temperature":    round(gv.temp_c, 2)   if gv.temp_c   is not None else None,
                    "humidity":       round(gv.humidity, 2) if gv.humidity is not None else None,
                    "pressure":       round(gv.pressure, 2) if gv.pressure is not None else None,
                    "sleep_stage":    gv.sleep_stage,
                    "sleep_score":    gv.sleep_score,
                    "status":         gv.status,
                    "timestamp":      datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
            with open("/tmp/live_vitals.txt", "w") as f:
                json.dump(vitals, f)
        except Exception as e:
            print(f"[Vitals Write] {e}")
        time.sleep(1)

# ── Gesture Sensor (APDS9960) ─────────────────────────────────────────────────
apds = None
if board_available:
    try:
        i2c  = busio.I2C(board.SCL, board.SDA)
        from adafruit_apds9960.apds9960 import APDS9960
        apds = APDS9960(i2c)
        apds.enable_proximity = True
        time.sleep(0.1)   # let proximity engine settle before enabling gesture
        apds.enable_gesture   = True
        time.sleep(0.1)   # let gesture engine settle
        print("[Gesture] APDS9960 ready!")
    except Exception as e:
        print(f"[Gesture] Not available: {e}")
        apds = None

def detect_gesture():
    if not apds:
        return None
    try:
        g = apds.gesture()
        if g == 3: return "LEFT"
        if g == 4: return "RIGHT"
    except OSError:
        # I2C glitch — skip this reading, don't crash
        return None
    except Exception:
        pass
    return None

def gesture_thread():
    print("[Gesture] Swipe LEFT = Start | RIGHT = Pause")
    while True:
        try:
            g = detect_gesture()
            if g == "LEFT":
                with lock:
                    should_start = (gv.status != "start")
                    if should_start:
                        gv.status = "start"
                if should_start:
                    # start_session() does real I/O (file opens) — kept outside
                    # the lock so it never blocks other threads waiting on it,
                    # but the decision to call it was made atomically above.
                    start_session()
                    print("[Gesture] LEFT → Monitoring Started!")
            elif g == "RIGHT":
                with lock:
                    should_stop = (gv.status != "pause")
                    if should_stop:
                        gv.status = "pause"
                if should_stop:
                    stop_session()
                    print("[Gesture] RIGHT → Monitoring Paused!")
        except Exception as e:
            print(f"[Gesture Error] {e} — gesture detection continues, retrying")
            traceback.print_exc()
        time.sleep(0.1)

# ── Radar Thread (Full Manual Pipeline: Butterworth → FFT → Median → EMA) ────
SAMPLING_RATE  = 20        # radar samples per second
BUFFER_SIZE    = 300       # 300 / 20 = 15 seconds before first reading
HR_LOW         = 0.8       # Hz (48 BPM)
HR_HIGH        = 2.0       # Hz (120 BPM)
BR_LOW         = 0.1       # Hz (6 breaths/min)
BR_HIGH        = 0.5       # Hz (30 breaths/min)
FILTER_ORDER   = 4         # Butterworth filter order
HR_CALIBRATION = 2       # correction coefficient
BR_CALIBRATION = 0
EMA_ALPHA      = 0.3
MEDIAN_WINDOW  = 5

# Rolling buffer for raw phase data
phase_buffer = deque(maxlen=BUFFER_SIZE)

def butterworth_bandpass(data, lowcut, highcut, fs, order=FILTER_ORDER):
    nyq  = 0.5 * fs
    low  = lowcut  / nyq
    high = highcut / nyq
    b, a = butter(order, [low, high], btype='band')
    return filtfilt(b, a, np.array(data))

def fft_peak_frequency(filtered_signal, fs, lowcut, highcut):
    n        = len(filtered_signal)
    fft_vals = np.abs(fft(filtered_signal))
    freqs    = fftfreq(n, 1.0 / fs)
    pos_mask = freqs > 0
    freqs    = freqs[pos_mask]
    fft_vals = fft_vals[pos_mask]
    band_mask = (freqs >= lowcut) & (freqs <= highcut)
    if not np.any(band_mask):
        return 0.0
    peak_freq = freqs[band_mask][np.argmax(fft_vals[band_mask])]
    return float(peak_freq)

def apply_ema(new_value, previous_ema, alpha=EMA_ALPHA):
    return alpha * new_value + (1 - alpha) * previous_ema

hr_median_buffer = []
br_median_buffer = []

def apply_median(value, buffer):
    buffer.append(value)
    if len(buffer) > MEDIAN_WINDOW:
        buffer.pop(0)
    return float(np.median(buffer))

# ── Sleep Score Calculator ────────────────────────────────────────────────────
def calculate_sleep_score(stage_list=None, hr_list=None):
    if stage_list is None:
        stage_list = stage_history
    if hr_list is None:
        hr_list = ai_hr_buffer

    if not stage_list or not gv.session_start:
        return 0

    total_windows = len(stage_list)
    if total_windows == 0:
        return 0

    # Count each stage
    stage_counts = {}
    for s in stage_list:
        stage_counts[s] = stage_counts.get(s, 0) + 1

    # Each window = 32 seconds
    duration_minutes = (total_windows * 32) / 60

    # S1 — Sleep Duration Score (35%)
    ideal_minutes = 480   # 8 hours
    s1 = min(100, (duration_minutes / ideal_minutes) * 100)

    # S2 — Sleep Efficiency Score (20%)
    # % of time NOT in Wake
    wake_windows = stage_counts.get("W", 0)
    efficiency   = ((total_windows - wake_windows) / total_windows) * 100
    s2           = efficiency

    # S3 — Deep Sleep Score (15%)
    # Ideal = 20% of total sleep in N3
    n3_pct = (stage_counts.get("N3", 0) / total_windows) * 100
    s3     = min(100, (n3_pct / 20) * 100)

    # S4 — REM Sleep Score (15%)
    # Ideal = 25% of total sleep in REM
    rem_pct = (stage_counts.get("R", 0) / total_windows) * 100
    s4      = min(100, (rem_pct / 25) * 100)

    # S5 — HRV Score (15%)
    # Based on HR variability in hr_list
    if len(hr_list) > 10:
        hrv = np.std(hr_list)
        s5  = min(100, (hrv / 15) * 100)
    else:
        s5 = 50   # default if not enough data

    # Weighted final score
    score = (0.35 * s1) + (0.20 * s2) + (0.15 * s3) + (0.15 * s4) + (0.15 * s5)
    return round(score, 1)

# ── AI Report Generation (runs once, at session end) ──────────────────────────
# Raw model labels collapsed into categories a non-clinician can read at a glance.
# N1+N2 (light/stable sleep) merge into one "Light Sleep" bucket since most
# people can't meaningfully distinguish them; "P" (brief movement) folds into
# "Awake" since a movement arousal reads the same to a layperson; "Missing"
# stays separate since sensor dropout isn't a real sleep stage and shouldn't
# be silently hidden.
SIMPLE_STAGE = {
    "W"      : "Awake",
    "P"      : "Awake",
    "N1"     : "Light Sleep",
    "N2"     : "Light Sleep",
    "N3"     : "Deep Sleep",
    "R"      : "Dreaming",
    "Missing": "No Signal",
}

# Ordered for the hypnogram y-axis
SIMPLE_STAGE_ORDER = ["Awake", "Dreaming", "Light Sleep", "Deep Sleep", "No Signal"]

AI_WINDOW_SIZE = 640   # 32 seconds × 20Hz — fixed by the trained model's input shape, do not change

def generate_sleep_report(hr_snapshot, session_folder, session_start_epoch):
    """Runs once, at the end of a session (called from stop_session()).

    Classifies the WHOLE session in one pass — every consecutive, non-
    overlapping 32-second epoch — instead of repeatedly inferring a recent
    sliding window during the session. Builds a local stage list from every
    epoch, computes the overall score the same way calculate_sleep_score()
    already does, and saves a two-page PDF (score + hypnogram + breakdown,
    then a detailed clock-time timeline) into the session's folder.
    """
    if not tf_available or ai_interpreter is None or ai_label_encoder is None:
        print("[Report] Model not loaded — skipping report generation.")
        return

    n_epochs = len(hr_snapshot) // AI_WINDOW_SIZE
    if n_epochs == 0:
        print(f"[Report] Only {len(hr_snapshot)} HR samples collected — "
              f"need at least {AI_WINDOW_SIZE} (32s) for one epoch. Skipping report.")
        return

    print(f"[Report] Classifying {n_epochs} epochs ({n_epochs * 32}s of data)...")
    epoch_results  = []   # (start_seconds, stage_code, confidence)
    local_stages   = []   # this session's own stage list — never touches the shared global

    for i in range(n_epochs):
        try:
            window = hr_snapshot[i * AI_WINDOW_SIZE : (i + 1) * AI_WINDOW_SIZE]

            # Same per-epoch z-score scaling as the model was already using live
            mean = np.mean(window)
            std  = np.std(window)
            if std == 0:
                std = 1
            scaled = [(x - mean) / std for x in window]

            X_full = np.zeros((AI_WINDOW_SIZE, 7), dtype=np.float32)
            X_full[:, 0] = scaled
            X = X_full.reshape(1, AI_WINDOW_SIZE, 7)

            ai_interpreter.set_tensor(ai_input_details[0]['index'], X)
            ai_interpreter.invoke()
            probs      = ai_interpreter.get_tensor(ai_output_details[0]['index'])[0]
            pred_index = int(np.argmax(probs))
            confidence = float(probs[pred_index]) * 100

            stage_code = ai_label_encoder.inverse_transform([pred_index])[0]
            local_stages.append(stage_code)
            epoch_results.append((i * 32, stage_code, confidence))

        except Exception as e:
            print(f"[Report] Epoch {i} failed: {e}")
            traceback.print_exc()

    if not epoch_results:
        print("[Report] Every epoch failed — no report generated.")
        return

    score = calculate_sleep_score(stage_list=local_stages, hr_list=hr_snapshot)
    print(f"[Report] Done — {len(epoch_results)} epochs classified, score: {score}/100")

    try:
        _render_report_pdf(epoch_results, score, session_folder, session_start_epoch)
    except Exception as e:
        print(f"[Report] PDF generation failed: {e}")
        traceback.print_exc()

def _render_report_pdf(epoch_results, score, session_folder, session_start_epoch):
    """Builds the two-page report: page 1 is the score + hypnogram + stage
    breakdown (using simplified, layperson-friendly stage names); page 2 is a
    table of every contiguous stage block with real clock start/end times."""
    import matplotlib
    matplotlib.use("Agg")   # no display on the Pi when run headless
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    session_start_dt = datetime.datetime.fromtimestamp(session_start_epoch)

    starts      = [r[0] / 60.0 for r in epoch_results]          # minutes, for the x-axis
    raw_codes   = [r[1] for r in epoch_results]
    confidences = [r[2] for r in epoch_results]
    simple      = [SIMPLE_STAGE.get(c, c) for c in raw_codes]   # simplified names, same length
    y_vals      = [SIMPLE_STAGE_ORDER.index(s) if s in SIMPLE_STAGE_ORDER else len(SIMPLE_STAGE_ORDER)
                   for s in simple]

    total = len(simple)

    # Stage percentages — aggregated correctly: several raw codes (N1+N2) can
    # map to the same simplified name, so counts must be summed, not overwritten.
    stage_counts = {}
    for s in simple:
        stage_counts[s] = stage_counts.get(s, 0) + 1
    pct = {s: round(100 * c / total, 1) for s, c in stage_counts.items()}

    # Merge consecutive same-stage epochs into contiguous blocks with real clock times
    blocks = []   # (stage_name, start_dt, end_dt, duration_minutes)
    block_start_idx = 0
    for i in range(1, total + 1):
        if i == total or simple[i] != simple[block_start_idx]:
            start_dt = session_start_dt + datetime.timedelta(seconds=epoch_results[block_start_idx][0])
            end_dt   = session_start_dt + datetime.timedelta(seconds=epoch_results[i - 1][0] + 32)
            duration = round((end_dt - start_dt).total_seconds() / 60, 1)
            blocks.append((simple[block_start_idx], start_dt, end_dt, duration))
            block_start_idx = i

    out_path = os.path.join(session_folder, "sleep_report.pdf")
    with PdfPages(out_path) as pdf:

        # ── Page 1: score + hypnogram + breakdown ──────────────────────────
        fig = plt.figure(figsize=(8.5, 11), facecolor="white")

        fig.text(0.5, 0.96, "SleepDoc+ Session Report", ha="center", fontsize=20, weight="bold")
        fig.text(0.5, 0.93, session_start_dt.strftime("%d %B %Y, %I:%M %p"),
                  ha="center", fontsize=10, color="gray")

        score_color = "#2ecc71" if score >= 75 else "#f39c12" if score >= 50 else "#e74c3c"
        fig.text(0.5, 0.86, f"{score:.0f}/100", ha="center", fontsize=42, weight="bold", color=score_color)
        fig.text(0.5, 0.82, "Overall Sleep Score", ha="center", fontsize=11, color="gray")

        ax1 = fig.add_axes([0.1, 0.50, 0.85, 0.25])
        ax1.step(starts, y_vals, where="post", color="#5b6ee8", linewidth=1.5)
        ax1.set_yticks(range(len(SIMPLE_STAGE_ORDER)))
        ax1.set_yticklabels(SIMPLE_STAGE_ORDER, fontsize=8)
        ax1.set_xlabel("Time (minutes)", fontsize=9)
        ax1.set_title("Sleep Stage Timeline", fontsize=12, weight="bold")
        ax1.grid(True, axis="x", alpha=0.3)
        ax1.invert_yaxis()

        ax2 = fig.add_axes([0.1, 0.28, 0.85, 0.16])
        stage_color = {"Awake": "#e74c3c", "Dreaming": "#9b59b6",
                       "Light Sleep": "#3498db", "Deep Sleep": "#2ecc71", "No Signal": "#95a5a6"}
        labels = [s for s in SIMPLE_STAGE_ORDER if s in pct]
        values = [pct[s] for s in labels]
        ax2.barh(labels, values, color=[stage_color.get(s, "#95a5a6") for s in labels])
        ax2.set_xlabel("% of session", fontsize=9)
        ax2.set_title("Stage Breakdown", fontsize=12, weight="bold")
        for i, v in enumerate(values):
            ax2.text(v + 1, i, f"{v}%", va="center", fontsize=8)

        duration_min = round(total * 32 / 60, 1)
        avg_conf     = round(sum(confidences) / len(confidences), 1)
        summary = (f"Session duration: {duration_min} min   |   "
                   f"Epochs classified: {total}   |   "
                   f"Avg. confidence: {avg_conf}%")
        fig.text(0.5, 0.10, summary, ha="center", fontsize=9, color="gray")

        pdf.savefig(fig)
        plt.close(fig)

        # ── Page 2: detailed timeline — when each stage happened ───────────
        fig2 = plt.figure(figsize=(8.5, 11), facecolor="white")
        fig2.text(0.5, 0.96, "Detailed Stage Timeline", ha="center", fontsize=16, weight="bold")
        fig2.text(0.5, 0.94, "When each stage occurred, by clock time",
                  ha="center", fontsize=10, color="gray")

        table_data = [[name,
                       start_dt.strftime("%I:%M %p"),
                       end_dt.strftime("%I:%M %p"),
                       f"{dur:.0f} min"]
                      for name, start_dt, end_dt, dur in blocks]

        ax3 = fig2.add_axes([0.07, 0.04, 0.86, 0.86])
        ax3.axis("off")
        tbl = ax3.table(cellText=table_data,
                         colLabels=["Stage", "From", "To", "Duration"],
                         loc="upper center", cellLoc="center", colLoc="center")
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(8 if len(blocks) <= 35 else 6)
        tbl.scale(1, 1.4 if len(blocks) <= 35 else 0.9)
        for (row, col), cell in tbl.get_celld().items():
            if row == 0:
                cell.set_facecolor("#5b6ee8")
                cell.set_text_props(color="white", weight="bold")
            else:
                stage_name = table_data[row - 1][0]
                cell.set_facecolor(stage_color.get(stage_name, "#ffffff"))
                cell.set_alpha(0.3)

        pdf.savefig(fig2)
        plt.close(fig2)

    print(f"[Report] Saved → {out_path}  ({len(blocks)} stage blocks)")

data_port       = None
vts             = None
radar_available = False

if mmwave_available:
    try:
        radar_port = find_radar_port()      # ← auto detect!!
        if radar_port:
            data_port = serial.Serial(radar_port, 921600)
            vts       = vitalsign.VitalSign(data_port)
            radar_available = True
            print(f"[Radar] Connected at {radar_port}!!")
        else:
            print("[Radar] No port found!!")
            radar_available = False
    except Exception as e:
        print(f"[Radar] Not available: {e}")
        radar_available = False

def radar_thread():
    if radar_available:
        data_port.flushInput()

    first_reading    = True
    new_sample_count     = 0
    FFT_RECALC_INTERVAL  = 20
    consecutive_errs = 0

    while True:
        if gv.status == "start":
            try:
                if not radar_available:
                    time.sleep(1)
                    continue

                (dck, vd, _) = vts.tlvRead(False)
                if dck:
                    consecutive_errs = 0   # reset error counter on good frame

                    # Use full pipeline if scipy available, else fallback
                    if scipy_available:
                        # Step 1 — Get raw phase value
                        raw_phase = vd.unwrapPhasePeak_mm

                        # Reject corrupt values before adding to buffer!!
                        if raw_phase == 0.0:
                            continue
                        if abs(raw_phase) > 500:
                            continue

                        phase_buffer.append(raw_phase)
                        new_sample_count += 1

                        # Feed the AI buffer once per tick (~20Hz), not once per FFT
                        # recompute (~once/sec) — holds the last known HR steady in
                        # between, so 640 samples span a real ~32s, matching how the
                        # model was trained. Appending only on recompute would instead
                        # stretch 640 samples across ~11 real minutes.
                        if gv.hr is not None:
                            ai_hr_buffer.append(gv.hr)

                        if len(phase_buffer) < BUFFER_SIZE:
                            print(f"[Buffering] {len(phase_buffer)}/{BUFFER_SIZE}")
                            continue

                        # Only recalculate FFT every 20 new valid samples!!
                        if new_sample_count % FFT_RECALC_INTERVAL != 0:
                            continue

                        signal = np.array(phase_buffer)
                        signal = signal - np.mean(signal)

                        br_filtered = butterworth_bandpass(signal, BR_LOW, BR_HIGH, SAMPLING_RATE)
                        hr_filtered = butterworth_bandpass(signal, HR_LOW, HR_HIGH, SAMPLING_RATE)

                        br_freq = fft_peak_frequency(br_filtered, SAMPLING_RATE, BR_LOW, BR_HIGH)
                        hr_freq = fft_peak_frequency(hr_filtered, SAMPLING_RATE, HR_LOW, HR_HIGH)

                        raw_br = br_freq * 60.0
                        raw_hr = hr_freq * 60.0

                        median_hr = apply_median(raw_hr, hr_median_buffer)
                        median_br = apply_median(raw_br, br_median_buffer)

                        if first_reading:
                            smoothed_hr   = median_hr
                            smoothed_br   = median_br
                            first_reading = False
                        else:
                            smoothed_hr = apply_ema(median_hr, gv.hr if gv.hr is not None else median_hr)
                            smoothed_br = apply_ema(median_br, gv.br if gv.br is not None else median_br)

                        calibrated_hr = smoothed_hr + HR_CALIBRATION
                        calibrated_br = smoothed_br + BR_CALIBRATION

                        with lock:
                            gv.hr = calibrated_hr if calibrated_hr > 0 else gv.hr
                            gv.br = calibrated_br if calibrated_br > 0 else gv.br

                    else:
                        raw_hr = min(vd.heartRateEst_FFT, 500)
                        raw_br = min(vd.breathingRateEst_FFT, 500)
                        ai_hr_buffer.append(raw_hr)
                        median_hr = apply_median(raw_hr, hr_median_buffer)
                        median_br = apply_median(raw_br, br_median_buffer)
                        if first_reading:
                            smoothed_hr   = median_hr
                            smoothed_br   = median_br
                            first_reading = False
                        else:
                            smoothed_hr = apply_ema(median_hr, gv.hr if gv.hr is not None else median_hr)
                            smoothed_br = apply_ema(median_br, gv.br if gv.br is not None else median_br)
                        with lock:
                            gv.hr = smoothed_hr + HR_CALIBRATION
                            gv.br = smoothed_br + BR_CALIBRATION

                    write_radar_csv()
                    print(f"[Radar] HR: {gv.hr:.2f} BPM | BR: {gv.br:.2f}")

            except Exception as e:
                consecutive_errs += 1
                print(f"[Radar Error #{consecutive_errs}] {e}")
                # Clear stale buffer so we don't keep re-computing the same frozen values
                phase_buffer.clear()
                hr_median_buffer.clear()
                br_median_buffer.clear()
                first_reading    = True
                new_sample_count = 0
                if consecutive_errs >= 5:
                    # Back off longer if repeated failures
                    print("[Radar] Too many errors — waiting 3s before retry")
                    time.sleep(3)
                    consecutive_errs = 0
                    if data_port:
                        try:
                            data_port.flushInput()
                        except Exception:
                            pass
                else:
                    time.sleep(0.5)
        else:
            # Reset everything when paused — NOTE: ai_hr_buffer and stage_history
            # are NOT cleared here anymore. They hold the whole session's data,
            # needed intact by generate_sleep_report() in stop_session(). They're
            # reset once, deterministically, at the top of start_session() instead.
            phase_buffer.clear()
            hr_median_buffer.clear()
            br_median_buffer.clear()
            first_reading       = True
            new_sample_count    = 0
            time.sleep(0.1)

# ── BME280 Thread ─────────────────────────────────────────────────────────────
bme280_bus    = None
bme280_params = None

if bme280_available:
    try:
        bme280_bus    = smbus2.SMBus(1)
        bme280_params = bme280.load_calibration_params(bme280_bus, 0x77)
        print("[BME280] Ready!")
    except Exception as e:
        print(f"[BME280] Not available: {e}")
        bme280_bus    = None
        bme280_params = None

def bme280_thread():
    while True:
        if gv.status == "start":
            if bme280_bus and bme280_params:
                try:
                    data = bme280.sample(bme280_bus, 0x77, bme280_params)
                    with lock:
                        gv.temp_c   = data.temperature
                        gv.pressure = data.pressure
                        gv.humidity = data.humidity
                        temp_snap     = gv.temp_c
                        humidity_snap = gv.humidity
                        pressure_snap = gv.pressure

                    elapsed = round(time.time() - gv.session_start, 3) if gv.session_start else 0
                    write_env_csv(temp_snap, humidity_snap, pressure_snap, elapsed)
                    print(f"[BME280] Temp: {temp_snap:.1f}°C | Hum: {humidity_snap:.1f}% | Press: {pressure_snap:.1f}hPa")
                except Exception as e:
                    print(f"[BME280 Error] {e}")
            else:
                print("[BME280] Sensor not connected — skipping")
            time.sleep(5)
        else:
            time.sleep(0.1)

# ── LED Thread ────────────────────────────────────────────────────────────────
def led_thread():
    breath_angle = 0.0
    last_mode    = None
    last_effect  = None
    while True:
        try:
            if gv.status == "start":
                mode, effect, brightness, light_on = get_led_settings()

                # If user turned off lights → turn off and skip!!
                if not light_on:
                    leds_off()
                    breath_angle = 0.0
                    time.sleep(0.1)
                    continue

                # Reset angle when mode or effect changes!!
                if mode != last_mode or effect != last_effect:
                    breath_angle = 0.0
                    last_mode    = mode
                    last_effect  = effect

                # Default — no mode selected → sky_blue breathing
                if not mode or mode not in LED_COLORS:
                    color  = LED_COLORS["sky_blue"]
                    scaled = breathing_step(color, brightness, breath_angle)
                    set_both_leds(scaled)
                    breath_angle += 0.05
                    if breath_angle > 2 * math.pi:
                        breath_angle = 0.0
                    time.sleep(0.05)
                else:
                    color = LED_COLORS[mode]
                    if effect == "static":
                        set_both_leds(scale_color(color, brightness))
                        breath_angle = 0.0
                        time.sleep(0.05)
                    else:
                        scaled = breathing_step(color, brightness, breath_angle)
                        set_both_leds(scaled)
                        breath_angle += 0.05
                        if breath_angle > 2 * math.pi:
                            breath_angle = 0.0
                        time.sleep(0.05)
            else:
                leds_off()
                breath_angle = 0.0
                last_mode    = None
                last_effect  = None
                time.sleep(0.1)
        except Exception as e:
            print(f"[LED Error] {e}")
            time.sleep(0.1)

# ── Flask API ─────────────────────────────────────────────────────────────────
flask_app = Flask(__name__)
CORS(flask_app)

@flask_app.route("/")
def serve_app():
    if os.path.isdir(WEB_APP_DIR):
        return send_from_directory(WEB_APP_DIR, "index.html")
    return (
        "<html><body style='background:#09091e;color:#a78bfa;font-family:sans-serif;"
        "display:flex;align-items:center;justify-content:center;height:100vh;margin:0;'>"
        "<div style='text-align:center'>"
        "<h1>🌙 Sleep Doc</h1>"
        "<p>Flutter web app not found.</p>"
        f"<p>API is ready at <a href='/vitals' style='color:#38bdf8'>/vitals</a></p>"
        "</div></body></html>"
    )

# Captive portal detection — Android/iOS/Windows probe these URLs after
# joining WiFi to check for internet. Redirecting instead of giving the
# expected response makes the OS show "Sign in to network" and open a
# special browser here, bypassing the normal browser's offline page.
@flask_app.route("/generate_204")
@flask_app.route("/gen_204")
def captive_portal_android():
    return redirect("/", code=302)

@flask_app.route("/hotspot-detect.html")
def captive_portal_apple():
    return redirect("/", code=302)

@flask_app.route("/connecttest.txt")
def captive_portal_windows():
    return redirect("/", code=302)

REPORTS_BASE = "/home/raspberry/Desktop/SLEEPDOC/Session Reports"

@flask_app.route("/reports_list", methods=["GET"])
def list_reports():
    """JSON list of every session that has a sleep_report.pdf, newest first."""
    reports = []
    try:
        if os.path.isdir(REPORTS_BASE):
            date_folders = sorted(
                (d for d in os.listdir(REPORTS_BASE)
                 if os.path.isdir(os.path.join(REPORTS_BASE, d))),
                key=lambda d: os.path.getmtime(os.path.join(REPORTS_BASE, d)),
                reverse=True)
            for date_folder in date_folders:
                date_path = os.path.join(REPORTS_BASE, date_folder)
                session_folders = sorted(
                    (s for s in os.listdir(date_path)
                     if os.path.isdir(os.path.join(date_path, s))),
                    key=lambda s: os.path.getmtime(os.path.join(date_path, s)),
                    reverse=True)
                for session_folder in session_folders:
                    pdf_path = os.path.join(date_path, session_folder, "sleep_report.pdf")
                    if os.path.isfile(pdf_path):
                        reports.append({
                            "date":    date_folder,
                            "session": session_folder,
                            "url":     f"/reports/{quote(date_folder)}/{quote(session_folder)}/sleep_report.pdf",
                        })
    except Exception as e:
        print(f"[Flask /reports_list] {e}")
    return jsonify({"reports": reports})

@flask_app.route("/reports/<path:filepath>")
def serve_report(filepath):
    """Serves a session's PDF (or any file in its folder) directly by path."""
    return send_from_directory(REPORTS_BASE, filepath)

@flask_app.route("/qr")
def make_qr_image():
    """Generates a QR PNG on demand for any text passed in ?data=... — used by
    the touchscreen's Data Storage page to show a per-report QR without needing
    the qrcode library installed in the GUI's own environment."""
    try:
        import qrcode
        import io
        data = request.args.get("data", "")
        if not data:
            return "Missing data parameter", 400
        img = qrcode.make(data)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return send_file(buf, mimetype="image/png")
    except Exception as e:
        print(f"[Flask /qr] {e}")
        return "QR generation failed", 500

@flask_app.route("/<path:path>")
def serve_static(path):
    if os.path.isdir(WEB_APP_DIR):
        return send_from_directory(WEB_APP_DIR, path)
    return "Not found", 404

@flask_app.route("/vitals", methods=["GET"])
def get_vitals():
    try:
        if os.path.exists("/tmp/live_vitals.txt"):
            with open("/tmp/live_vitals.txt", "r") as f:
                data = json.load(f)
            return jsonify({
                "heart_rate":     data.get("heart_rate"),
                "breathing_rate": data.get("breathing_rate"),
                "temperature":    data.get("temperature"),
                "humidity":       data.get("humidity"),
                "pressure":       data.get("pressure"),
                "sleep_stage":    data.get("sleep_stage"),
                "sleep_score":    data.get("sleep_score"),
            })
    except Exception as e:
        print(f"[Flask /vitals] {e}")
    return jsonify({
        "heart_rate":     None,
        "breathing_rate": None,
        "temperature":    None,
        "humidity":       None,
        "pressure":       None,
        "sleep_stage":    None,
        "sleep_score":    None,
    })

@flask_app.route("/control", methods=["POST"])
def control():
    try:
        data = request.get_json()
        with open("/tmp/vsd_selection.json", "w") as f:
            json.dump(data, f)

        action_taken = None   # "started" | "stopped" | "no_change" | None (no monitoring key sent)

        # Step 1 — Handle monitoring state FIRST. Check-and-act is atomic
        # under the lock so a concurrent gesture can't see a stale gv.status
        # and start/stop the session a second time underneath us.
        #
        # start_session() / stop_session() do real file I/O (CSV opens/closes,
        # JSON writes, os.rename) that can stall for several seconds on the
        # Pi's SD card — long enough to time out the GUI's 3-second HTTP read.
        # We set gv.status immediately (so every other thread sees the new
        # state right away), then hand the actual session work off to a
        # background thread and return {"status": "ok"} instantly.
        if "monitoring" in data:
            if data["monitoring"]:
                with lock:
                    should_start = (gv.status != "start")
                    if should_start:
                        gv.status = "start"
                if should_start:
                    Thread(target=start_session, daemon=True).start()
                    action_taken = "started"
                else:
                    action_taken = "no_change"
            else:
                with lock:
                    should_stop = (gv.status != "pause")
                    if should_stop:
                        gv.status = "pause"   # set BEFORE handing off, so radar/bme280 stop immediately
                if should_stop:
                    def _stop_and_off():
                        stop_session()
                        leds_off()
                    Thread(target=_stop_and_off, daemon=True).start()
                    action_taken = "stopped"
                else:
                    action_taken = "no_change"

        # Step 2 — Handle LED settings (only if NOT turning off)
        elif data.get("light_on") and data.get("led_mode"):
            pass  # led_thread handles this via vsd_selection.json!!

        return jsonify({"status": "ok", "action": action_taken, "monitoring": gv.status})
    except Exception as e:
        print(f"[Flask /control] {e}")
        return jsonify({"error": str(e)})

@flask_app.route("/status", methods=["GET"])
def get_status():
    return jsonify({
        "radar":      radar_available,
        "bme280":     bme280_bus is not None,
        "gesture":    apds is not None,
        "leds":       led1 is not None,
        "monitoring": gv.status,
        "local_ip":   get_local_ip(),
    })

# ── Cleanup ───────────────────────────────────────────────────────────────────
def cleanup():
    print("[Cleanup] Shutting down...")
    gv.status = "off"
    leds_off()
    stop_session()

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("  Sleep Doc Starting...")
    print(f"  Local Dashboard: http://{get_local_ip()}:{FLASK_PORT}")
    print("  Waiting for LEFT gesture to start monitoring")
    print("  Swipe RIGHT to pause monitoring")
    print("=" * 50)

    generate_qr()

    Thread(target=radar_thread, daemon=True).start()
    Thread(target=gesture_thread, daemon=True).start()
    Thread(target=bme280_thread, daemon=True).start()
    Thread(target=write_vitals_thread, daemon=True).start()
    Thread(target=led_thread, daemon=True).start()

    Thread(
        target=lambda: flask_app.run(
            host="0.0.0.0",
            port=FLASK_PORT,
            debug=False,
            use_reloader=False,
            threaded=True
        ),
        daemon=True
    ).start()

    print("[System] All threads started!")
    print(f"[Flask] API running on port {FLASK_PORT}")

    try:
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n[System] Keyboard interrupt!")

    finally:
        cleanup()