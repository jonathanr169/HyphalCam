import os, shutil, subprocess, tempfile, logging
from datetime import datetime
from time import sleep
import RPi.GPIO as GPIO
from picamera2 import Picamera2

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

if not shutil.which("rclone"):
    raise EnvironmentError("rclone not found in PATH")

# --- Config ---
PI_NUMBER        = 1                  # Change this to 1–12 on each Pi
RCLONE_REMOTE    = f"guzmanpi{PI_NUMBER}_gdrive"
DIR, STEP, ENABLE, LED = 20, 21, 16, 17
CW, CCW          = 1, 0
SPR              = 200                # Steps per revolution
delay            = 0.003              # Motor speed
TRAVEL_PER_TURN  = 1.6                # mm per revolution
SETTLE_DELAY     = 0.5                # Seconds to wait after moving before photo
PAUSE_TIME       = 3                  # Seconds to wait after photo before moving

IMAGE_FORMAT     = "jpeg"             # "jpeg" or "dng"
IMAGE_SIZE       = (4056, 3040)       # Resolution

# --- Exposure settings ---
# Run once with LOCK_EXPOSURE = False to let the camera auto-expose,
# check the first photo looks good, then set LOCK_EXPOSURE = True
# and tune EXPOSURE_TIME and ANALOGUE_GAIN to match
LOCK_EXPOSURE    = True
EXPOSURE_TIME    = 66654              # Microseconds — increase to brighten, decrease to darken
ANALOGUE_GAIN    = 10.0                # 1.0 = no gain, increase if too dark (e.g. 2.0, 4.0)

# --- Distance (set one, leave the other as None) ---
TUBE_LENGTH_IN   = None               # e.g. 6.7  — set this OR mm below
TUBE_LENGTH_MM   = 100.0              # e.g. 100.0 — set this OR inches above

# Convert inches to mm if inches is set
if TUBE_LENGTH_IN is not None:
    TUBE_LENGTH_MM = TUBE_LENGTH_IN * 25.4

# --- Real world measurements (don't change) ---
FRAME_WIDTH_MM   = 9.01               # Measured from ImageJ
OVERLAP_MM       = 5              # 35% overlap — adjust if needed

# --- Calculated (don't touch) ---
MM_PER_STEP      = TRAVEL_PER_TURN / SPR
STEP_DISTANCE_MM = FRAME_WIDTH_MM - OVERLAP_MM
steps_per_photo  = int(STEP_DISTANCE_MM / MM_PER_STEP)
NUM_PHOTOS       = int(TUBE_LENGTH_MM / STEP_DISTANCE_MM) + 1
steps_moved      = steps_per_photo * (NUM_PHOTOS - 1)

# --- Folder structure ---
date_folder   = datetime.now().strftime("%Y-%m-%d")
run_folder    = datetime.now().strftime("run_%H-%M")
camera_folder = f"camera{PI_NUMBER}"
remote_folder = f"rpicamfiles/panoramic_shots/{camera_folder}/{date_folder}/{run_folder}"
UPLOAD_DIR    = os.path.join(tempfile.gettempdir(), "rpicam_upload")
os.makedirs(UPLOAD_DIR, exist_ok=True)

logging.info(f"Tube length: {TUBE_LENGTH_MM}mm ({TUBE_LENGTH_MM / 25.4:.2f}in)")
logging.info(f"Frame width: {FRAME_WIDTH_MM}mm | Overlap: {OVERLAP_MM}mm")
logging.info(f"Step distance: {STEP_DISTANCE_MM}mm | Steps per move: {steps_per_photo}")
logging.info(f"Total photos: {NUM_PHOTOS}")
logging.info(f"Remote folder: {RCLONE_REMOTE}:{remote_folder}")
logging.info(f"Exposure locked: {LOCK_EXPOSURE} | ExposureTime: {EXPOSURE_TIME}us | Gain: {ANALOGUE_GAIN}")

# --- GPIO ---
GPIO.setmode(GPIO.BCM)
for pin in (DIR, STEP, ENABLE, LED):
    GPIO.setup(pin, GPIO.OUT)
GPIO.output(ENABLE, GPIO.HIGH)

# --- Camera ---
picam2 = Picamera2()
if IMAGE_FORMAT == "dng":
    config = picam2.create_still_configuration(
        main={"size": IMAGE_SIZE},
        raw={"size": picam2.sensor_resolution}
    )
else:
    config = picam2.create_still_configuration(
        main={"size": IMAGE_SIZE}
    )
picam2.configure(config)
picam2.start()
sleep(2)

# Lock exposure after warmup if enabled
if LOCK_EXPOSURE:
    picam2.set_controls({
        "AeEnable": False,
        "AwbEnable": False,
        "ExposureTime": EXPOSURE_TIME,
        "AnalogueGain": ANALOGUE_GAIN
    })
    sleep(0.5)
    logging.info("Exposure locked.")
else:
    logging.info("Auto exposure enabled.")

# --- Helpers ---
def step_motor(steps):
    GPIO.output(ENABLE, GPIO.LOW)
    sleep(0.01)
    for _ in range(steps):
        GPIO.output(STEP, GPIO.HIGH)
        sleep(delay)
        GPIO.output(STEP, GPIO.LOW)
        sleep(delay)
    GPIO.output(ENABLE, GPIO.HIGH)

def take_and_upload(position):
    ts       = datetime.now().strftime("%H-%M-%S")
    ext      = "dng" if IMAGE_FORMAT == "dng" else "jpg"
    filepath = os.path.join(UPLOAD_DIR, f"pos{position}_of{NUM_PHOTOS}__{ts}.{ext}")

    if IMAGE_FORMAT == "dng":
        picam2.capture_file(filepath, name="raw")
    else:
        picam2.capture_file(filepath)
    logging.info(f"Captured: {filepath}")

    print(f"Uploading photo {position}/{NUM_PHOTOS}...")
    result = subprocess.run(
        ["rclone", "copy", filepath, f"{RCLONE_REMOTE}:{remote_folder}", "--progress"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"✓ Photo {position}/{NUM_PHOTOS} uploaded successfully")
        os.remove(filepath)
        logging.info(f"Deleted local: {filepath}")
    else:
        print(f"✗ Upload failed for photo {position} — keeping local copy")
        logging.warning(f"Upload failed: {result.stderr}")

# --- Main sequence ---
GPIO.output(DIR, CW)
try:
    for i in range(NUM_PHOTOS):
        step_motor(steps_per_photo)
        GPIO.output(LED, GPIO.HIGH)
        sleep(SETTLE_DELAY)
        logging.info(f"Photo {i+1}/{NUM_PHOTOS}")
        take_and_upload(i + 1)
        sleep(PAUSE_TIME)
        GPIO.output(LED, GPIO.LOW)

    print("\nAll photos captured — returning to start...")
    GPIO.output(DIR, CCW)
    step_motor(steps_moved)
    print("Returned to start position.")

finally:
    GPIO.output(LED, GPIO.LOW)
    GPIO.cleanup()
    picam2.stop()
    try:
        shutil.rmtree(UPLOAD_DIR)
    except OSError as e:
        logging.warning(f"Could not remove upload dir: {e}")