import os, shutil, subprocess, tempfile, logging
from datetime import datetime
from time import sleep
import RPi.GPIO as GPIO
from picamera2 import Picamera2, Preview

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

if not shutil.which("rclone"):
    raise EnvironmentError("rclone not found in PATH")

# --- Config ---
PI_NUMBER      = 4                   # Change this to 1–12 on each Pi
RCLONE_REMOTE  = f"guzmanpi{PI_NUMBER}_gdrive"
DIR, STEP, ENABLE, LED = 20, 21, 16, 17
CW, CCW        = 1, 0
SPR            = 1000
delay          = 0.003
steps_per_move = SPR

# --- Exposure settings ---
# Step 1: Set LOCK_EXPOSURE = False and run — it will print the auto values
# Step 2: Copy those printed values into EXPOSURE_TIME and ANALOGUE_GAIN
# Step 3: Set LOCK_EXPOSURE = True and run normally
LOCK_EXPOSURE  = True
EXPOSURE_TIME  = 66654               # Microseconds — paste auto value here
ANALOGUE_GAIN  = 16.0                 # paste auto value here

# --- Folder structure ---
date_folder   = datetime.now().strftime("%Y-%m-%d")
camera_folder = f"camera{PI_NUMBER}"
remote_folder = f"manual_photos/{camera_folder}/{date_folder}"
UPLOAD_DIR    = os.path.join(tempfile.gettempdir(), "rpicam_upload")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# --- GPIO Setup ---
GPIO.setmode(GPIO.BCM)
for pin in (DIR, STEP, ENABLE, LED):
    GPIO.setup(pin, GPIO.OUT)
GPIO.output(ENABLE, GPIO.HIGH)
GPIO.output(LED, GPIO.HIGH)
print("Status LED ON")

# --- Camera Setup ---
picam2 = Picamera2()
camera_config = picam2.create_preview_configuration(main={"size": (2560, 1440)})
picam2.configure(camera_config)
print("Starting HQ Camera live preview...")
picam2.start_preview(Preview.QTGL)
picam2.start()
sleep(2)

# Read what auto exposure settled on
metadata = picam2.capture_metadata()
print(f"Auto exposure time: {metadata['ExposureTime']}us")
print(f"Auto gain: {metadata['AnalogueGain']}")

# Lock exposure after warmup if enabled
if LOCK_EXPOSURE:
    picam2.set_controls({
        "AeEnable": False,
        "AwbEnable": False,
        "ExposureTime": EXPOSURE_TIME,
        "AnalogueGain": ANALOGUE_GAIN
    })
    sleep(0.5)
    print(f"Exposure locked — ExposureTime: {EXPOSURE_TIME}us | Gain: {ANALOGUE_GAIN}")
else:
    print("Auto exposure enabled — copy the values above into EXPOSURE_TIME and ANALOGUE_GAIN then set LOCK_EXPOSURE = True")

photo_count = 1

def step_motor(direction, steps):
    GPIO.output(ENABLE, GPIO.LOW)
    sleep(0.001)
    GPIO.output(DIR, direction)
    for _ in range(steps):
        GPIO.output(STEP, GPIO.HIGH)
        sleep(delay)
        GPIO.output(STEP, GPIO.LOW)
        sleep(delay)
    GPIO.output(ENABLE, GPIO.HIGH)

def take_and_upload():
    global photo_count
    ts       = datetime.now().strftime(f"pi{PI_NUMBER}___%Y-%m-%d___%H-%M-%S")
    filepath = os.path.join(UPLOAD_DIR, f"photo_{ts}_{photo_count}.jpg")
    sleep(0.1)
    picam2.capture_file(filepath)
    logging.info(f"Captured: {filepath}")
    photo_count += 1

    print("Uploading to Google Drive...")
    result = subprocess.run(
        ["rclone", "copy", filepath, f"{RCLONE_REMOTE}:{remote_folder}", "--progress"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"✓ Upload successful: photo_{ts}_{photo_count - 1}.jpg")
        os.remove(filepath)
        logging.info(f"Deleted local: {filepath}")
    else:
        print(f"✗ Upload failed — photo kept locally at {filepath}")
        logging.warning(f"Upload failed: {result.stderr}")

print("\nControls:")
print("  1 = Clockwise (down)")
print("  2 = Counter-clockwise (up)")
print("  3 = Take photo")
print("  4 = Move clockwise then take photo")
print("  5 = Move counter-clockwise then take photo")
print("  q = Quit")

try:
    while True:
        key = input("\nEnter command: ").strip()

        if key == "1":
            print("Rotating clockwise")
            step_motor(CW, steps_per_move)

        elif key == "2":
            print("Rotating counter-clockwise")
            step_motor(CCW, steps_per_move)

        elif key == "3":
            print("Taking photo...")
            take_and_upload()

        elif key == "4":
            print("Rotating clockwise then taking photo...")
            step_motor(CW, steps_per_move)
            take_and_upload()

        elif key == "5":
            print("Rotating counter-clockwise then taking photo...")
            step_motor(CCW, steps_per_move)
            take_and_upload()

        elif key.lower() == "q":
            print("Exiting...")
            break

        else:
            print("Invalid input. Use 1, 2, 3, 4, 5, or q.")

finally:
    print("Shutting down...")
    GPIO.output(ENABLE, GPIO.HIGH)
    picam2.stop_preview()
    picam2.stop()
    GPIO.output(LED, GPIO.LOW)
    GPIO.cleanup()
    print("Camera stopped, LED OFF, GPIO cleaned up")
    try:
        shutil.rmtree(UPLOAD_DIR)
    except OSError as e:
        logging.warning(f"Could not remove upload dir: {e}")
