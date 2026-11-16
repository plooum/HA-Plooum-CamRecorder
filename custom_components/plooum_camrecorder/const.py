"""Constants of Plooum CamRecorder."""

from __future__ import annotations

DOMAIN = "plooum_camrecorder"
ENTRY_TITLE = "Plooum CamRecorder"

# Options
CONF_FOLDER = "folder"
CONF_KEEP_DAYS = "keep_days"
CONF_MAX_SIZE_GB = "max_size_gb"
CONF_CAMERAS = "cameras"  # the cameras that get entities

DEFAULT_SUBFOLDER = "plooum_camrecorder"  # in the local media folder
DEFAULT_KEEP_DAYS = 30
DEFAULT_MAX_SIZE_GB = 0

# Actions
SERVICE_SNAPSHOT = "snapshot"
SERVICE_RECORD = "record"
SERVICE_START_RECORDING = "start_recording"
SERVICE_STOP_RECORDING = "stop_recording"
ATTR_DURATION = "duration"
ATTR_LOOKBACK = "lookback"
ATTR_MAX_DURATION = "max_duration"

DEFAULT_DURATION = 30  # s, record
DEFAULT_MAX_DURATION = 600  # s, start_recording stops by itself after it
MAX_DURATION = 6 * 3600
MAX_LOOKBACK = 30

SNAPSHOT_TIMEOUT = 20  # s, all the tries of a photo together
RETRY_DELAY = 1  # s before trying a photo again (a camera that was idle)
STOP_TIMEOUT = 60  # s to wait for a stopped video to be written
PURGE_INTERVAL = 3600  # s

EVENT_SAVED = f"{DOMAIN}_saved"

KIND_PHOTO = "photo"
KIND_VIDEO = "video"

ATTR_CAMERA_ENTITY = "camera_entity"
