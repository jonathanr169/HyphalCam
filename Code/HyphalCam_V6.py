import os, shutil, subprocess, tempfile, logging
from datetime import datetime
from time import sleep
import RPi.GPIO as GPIO
from picamera2 import Picamera2

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

if not shutil.which("rclone"):
    raise EnvironmentError("rclone not found in PATH")

# --- Config ---
PI_NUMBER          = 1
RCLONE_REMOTE      = f"guzmanpi{PI_NUMBER}_gdrive"
BASE_FOLDER        = "rpicamfiles"
NUM_PHOTOS         = 10
TUBE_LENGTH_INCHES = 17
TRAVEL_PER_TURN    = 0.063
SPR                = 200
delay              = 0.001
SETTLE_DELAY       = 0.5
PAUSE_TIME         = 2
DIR, STEP, ENABLE, LED = 20, 21, 16, 17
CW, CCW = 1, 0

IMAGE_FORMAT       = "jpeg"              # "jpeg" or "dng"
IMAGE_SIZE         = (4056, 3040)        # Change resolution here

# --- Calculated (don't touch) ---
MAX_TURNS       = TUBE_LENGTH_INCHES / TRAVEL_PER_TURN
steps_per_photo = int(SPR * MAX_TURNS / NUM_PHOTOS)
steps_moved     = steps_per_photo * NUM_PHOTOS

# --- Folder structure ---
date_folder   = datetime.now().strftime("%Y-%m-%d")
run_folder    = datetime.now().strftime("run_%H-%M")
camera_folder = f"camera{PI_NUMBER}"
remote_folder = f"{BASE_FOLDER}/{date_folder}/{camera_folder}/{run_folder}"
UPLOAD_DIR    = os.path.join(tempfile.gettempdir(), "rpicam_upload")
os.makedirs(UPLOAD_DIR, exist_ok=True)

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

photo_paths = []

def take_photo(position):
    ts  = datetime.now().strftime("%H-%M-%S")
    ext = "dng" if IMAGE_FORMAT == "dng" else "jpg"
    filepath = os.path.join(UPLOAD_DIR, f"pos{position}_of{NUM_PHOTOS}__{ts}.{ext}")

    if IMAGE_FORMAT == "dng":
        picam2.capture_file(filepath, name="raw")
    else:
        picam2.capture_file(filepath)

    photo_paths.append(filepath)
    logging.info(f"Captured: {filepath}")

# --- Main sequence ---
GPIO.output(DIR, CW)
try:
    for i in range(NUM_PHOTOS):
        step_motor(steps_per_photo)
        GPIO.output(LED, GPIO.HIGH)
        sleep(SETTLE_DELAY)
        logging.info(f"Photo {i+1}/{NUM_PHOTOS}")
        take_photo(i + 1)
        sleep(PAUSE_TIME)
        GPIO.output(LED, GPIO.LOW)

    GPIO.output(DIR, CCW)
    step_motor(steps_moved)

finally:
    GPIO.output(LED, GPIO.LOW)
    GPIO.cleanup()
    picam2.stop()

# --- Upload ---
if photo_paths:
    result = subprocess.run(
        ["rclone", "copy", UPLOAD_DIR, f"{RCLONE_REMOTE}:{remote_folder}", "--progress"],
        capture_output=True, text=True
    )
    logging.info("Upload successful!" if result.returncode == 0
                 else f"Upload error (code {result.returncode}): {result.stderr}")

    for p in photo_paths:
        if os.path.exists(p):
            os.remove(p)
            logging.info(f"Deleted: {p}")
    try:
        shutil.rmtree(UPLOAD_DIR)
    except OSError as e:
        logging.warning(f"Could not remove upload dir: {e}")