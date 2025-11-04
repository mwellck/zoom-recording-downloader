#!/usr/bin/env python3

# Program Name: zoom-recording-downloader.py
# Description:  Zoom Recording Downloader is a cross-platform Python script
#               that uses Zoom's API (v2) to download and organize all
#               cloud recordings from a Zoom account onto local storage.
#               This Python script uses the OAuth method of accessing the Zoom API
# Created:      2020-04-26
# Author:       Ricardo Rodrigues
# Website:      https://github.com/ricardorodrigues-ca/zoom-recording-downloader
# Forked from:  https://gist.github.com/danaspiegel/c33004e52ffacb60c24215abf8301680

# System modules
import argparse
import base64
import json
import os
import re as regex
import signal
import sys as system
import time
import urllib.parse
from datetime import datetime, date, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# Installed modules
import dateutil.parser as parser
import pathvalidate as path_validate
import requests
import tqdm as progress_bar
from zoneinfo import ZoneInfo
from google_drive_client import GoogleDriveClient
from s3_client import S3Client


class Color:
    PURPLE = "\033[95m"
    CYAN = "\033[96m"
    DARK_CYAN = "\033[36m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"
    END = "\033[0m"


CONF_PATH = "zoom-recording-downloader.conf"

# Load configuration file and check for proper JSON syntax
try:
    with open(CONF_PATH, encoding="utf-8-sig") as json_file:
        CONF = json.loads(json_file.read())
except json.JSONDecodeError as e:
    print(f"{Color.RED}### Error parsing JSON in {CONF_PATH}: {e}")
    system.exit(1)
except FileNotFoundError:
    print(f"{Color.RED}### Configuration file {CONF_PATH} not found")
    system.exit(1)
except Exception as e:
    print(f"{Color.RED}### Unexpected error: {e}")
    system.exit(1)


def config(section, key, default=''):
    try:
        return CONF[section][key]
    except KeyError:
        if default == LookupError:
            print(f"{Color.RED}### No value provided for {section}:{key} in {CONF_PATH}")
            system.exit(1)
        else:
            return default


ACCOUNT_ID = config("OAuth", "account_id", LookupError)
CLIENT_ID = config("OAuth", "client_id", LookupError)
CLIENT_SECRET = config("OAuth", "client_secret", LookupError)

APP_VERSION = "3.2 (Google Drive, S3 & DigitalOcean Spaces Edition)"

API_ENDPOINT_USER_LIST = "https://api.zoom.us/v2/users"

RECORDING_START_DATE = None
RECORDING_END_DATE = None

# Parse dates from config
start_date_str = config("Recordings", "start_date", "")
end_date_str = config("Recordings", "end_date", "")

if start_date_str:
    RECORDING_START_DATE = parser.parse(start_date_str).replace(tzinfo=timezone.utc).date()
else:
    # Default to 30 days ago if not specified
    RECORDING_START_DATE = (date.today() - timedelta(days=30))

if end_date_str:
    RECORDING_END_DATE = parser.parse(end_date_str).replace(tzinfo=timezone.utc).date()
else:
    # Default to today
    RECORDING_END_DATE = date.today()
DOWNLOAD_DIRECTORY = config("Storage", "download_dir", 'downloads')
COMPLETED_MEETING_IDS_LOG = config("Storage", "completed_log", 'completed-downloads.log')
COMPLETED_MEETING_IDS = set()
USE_COMPLETED_LOG = config("Storage", "use_completed_log", True)
LAST_RUN_LOG = config("Storage", "last_run_log", "last-run.log")

# Zoom deletion configuration
DELETE_FROM_ZOOM = config("Zoom", "delete_after_download", False)
INCLUDE_INACTIVE_USERS = config("Zoom", "include_inactive_users", False)

# Auto date range configuration
AUTO_DATE_RANGE = config("Recordings", "auto_date_range", False)

MEETING_TIMEZONE = ZoneInfo(config("Recordings", "timezone", 'UTC'))
MEETING_STRFTIME = config("Recordings", "strftime", '%Y.%m.%d - %I.%M %p UTC')
MEETING_FILENAME = config("Recordings", "filename",
                          '{meeting_time} - {topic} - {rec_type} - {recording_id}.{file_extension}')
MEETING_FOLDER = config("Recordings", "folder", '{topic} - {meeting_time}')

# Parallel processing configuration
MAX_WORKERS = int(config("Processing", "max_workers", 3))  # Number of parallel downloads/uploads

# Storage configuration
GDRIVE_ENABLED = False
S3_ENABLED = False


def setup_google_drive():
    """Initialize Google Drive client with OAuth authentication"""
    try:
        drive_client = GoogleDriveClient(CONF.get('GoogleDrive', {}))
        if not drive_client.authenticate():
            choice = input("Would you like to continue with local storage instead? (y/n): ")
            if choice.lower() != 'y':
                system.exit(1)
            return None

        if not drive_client.initialize_root_folder():
            print(f"{Color.RED}### Failed to create root folder in Google Drive{Color.END}")
            choice = input("Would you like to continue with local storage instead? (y/n): ")
            if choice.lower() != 'y':
                system.exit(1)
            return None

        return drive_client
    except Exception as e:
        print(f"{Color.RED}### Google Drive initialization failed: {str(e)}{Color.END}")
        choice = input("Would you like to continue with local storage instead? (y/n): ")
        if choice.lower() != 'y':
            system.exit(1)
        return None


def setup_s3():
    """Initialize S3 client"""
    try:
        s3_client = S3Client(CONF.get('S3', {}))
        if not s3_client.authenticate():
            choice = input("Would you like to continue with local storage instead? (y/n): ")
            if choice.lower() != 'y':
                system.exit(1)
            return None

        if not s3_client.initialize_root_folder():
            print(f"{Color.RED}### Failed to initialize S3 root folder{Color.END}")
            choice = input("Would you like to continue with local storage instead? (y/n): ")
            if choice.lower() != 'y':
                system.exit(1)
            return None

        return s3_client
    except Exception as e:
        print(f"{Color.RED}### S3 initialization failed: {str(e)}{Color.END}")
        choice = input("Would you like to continue with local storage instead? (y/n): ")
        if choice.lower() != 'y':
            system.exit(1)
        return None


def load_access_token():
    """ OAuth function, thanks to https://github.com/freelimiter
    """
    url = f"https://zoom.us/oauth/token?grant_type=account_credentials&account_id={ACCOUNT_ID}"

    client_cred = f"{CLIENT_ID}:{CLIENT_SECRET}"
    client_cred_base64_string = base64.b64encode(client_cred.encode("utf-8")).decode("utf-8")

    headers = {
        "Authorization": f"Basic {client_cred_base64_string}",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    response = json.loads(requests.request("POST", url, headers=headers).text)

    global ACCESS_TOKEN
    global AUTHORIZATION_HEADER

    try:
        ACCESS_TOKEN = response["access_token"]
        AUTHORIZATION_HEADER = {
            "Authorization": f"Bearer {ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }

    except KeyError:
        print(f"{Color.RED}### The key 'access_token' wasn't found.{Color.END}")


def get_users():
    """ loop through pages and return all users (active and optionally inactive) """
    # Determine which user statuses to fetch
    if INCLUDE_INACTIVE_USERS:
        # Fetch both active and inactive users
        statuses = ['active', 'inactive']
        print(f"{Color.CYAN}Fetching both active and inactive users...{Color.END}")
    else:
        # Only fetch active users (default)
        statuses = ['active']
        print(f"{Color.CYAN}Fetching active users only...{Color.END}")

    all_users = []

    for status in statuses:
        response = requests.get(
            url=f"{API_ENDPOINT_USER_LIST}?status={status}",
            headers=AUTHORIZATION_HEADER
        )

        if not response.ok:
            print(response)
            print(
                f"{Color.RED}### Could not retrieve {status} users. Please make sure that your access "
                f"token is still valid{Color.END}"
            )
            continue  # Continue with other statuses instead of exiting

        page_data = response.json()
        total_pages = int(page_data["page_count"]) + 1

        for page in range(1, total_pages):
            url = f"{API_ENDPOINT_USER_LIST}?status={status}&page_number={str(page)}"
            user_data = requests.get(url=url, headers=AUTHORIZATION_HEADER).json()
            users = ([
                (
                    user["email"],
                    user["id"],
                    user.get("first_name", ""),
                    user.get("last_name", ""),
                    user.get("status", status)  # Include status
                )
                for user in user_data["users"]
            ])

            all_users.extend(users)

        print(f"{Color.GREEN}Found {len([u for u in all_users if u[4] == status])} {status} users{Color.END}")

    if not all_users:
        print(f"{Color.RED}### No users found!{Color.END}")
        system.exit(1)

    return all_users


def format_filename(params):
    file_extension = params["file_extension"].lower()
    recording = params["recording"]
    recording_id = params["recording_id"]
    recording_type = params["recording_type"]

    invalid_chars_pattern = r'[<>:"/\\|?*\x00-\x1F]'
    topic = regex.sub(invalid_chars_pattern, '', recording["topic"])
    rec_type = recording_type.replace("_", " ").title()
    meeting_time_utc = parser.parse(recording["start_time"]).replace(tzinfo=timezone.utc)
    meeting_time_local = meeting_time_utc.astimezone(MEETING_TIMEZONE)
    year = meeting_time_local.strftime("%Y")
    month = meeting_time_local.strftime("%m")
    day = meeting_time_local.strftime("%d")
    meeting_time = meeting_time_local.strftime(MEETING_STRFTIME)

    filename = MEETING_FILENAME.format(**locals())
    folder = MEETING_FOLDER.format(**locals())
    return (filename, folder)


def get_downloads(recording):
    if not recording.get("recording_files"):
        raise Exception

    downloads = []
    for download in recording["recording_files"]:
        file_type = download["file_type"]
        file_extension = download["file_extension"]
        recording_id = download["id"]
        file_size = download.get("file_size", 0)  # Get file size in bytes (0 for CC/TIMELINE files)

        if file_type == "":
            recording_type = "incomplete"
        elif file_type != "TIMELINE":
            recording_type = download["recording_type"]
        else:
            recording_type = download["file_type"]

        # must append access token to download_url
        download_url = f"{download['download_url']}?access_token={ACCESS_TOKEN}"
        downloads.append((file_type, file_extension, download_url, recording_type, recording_id, file_size))

    return downloads


def get_recordings(email, page_size, rec_start_date, rec_end_date):
    return {
        "userId": email,
        "page_size": page_size,
        "from": rec_start_date,
        "to": rec_end_date
    }


def per_delta(start, end, delta):
    """ Generator used to create deltas for recording start and end dates
    """
    curr = start
    while curr < end:
        yield curr, min(curr + delta, end)
        curr += delta


def list_recordings(email):
    """ Start date now split into YEAR, MONTH, and DAY variables (Within 6 month range)
        then get recordings within that range
    """

    recordings = []

    for start, end in per_delta(RECORDING_START_DATE, RECORDING_END_DATE, timedelta(days=30)):
        post_data = get_recordings(email, 300, start, end)
        response = requests.get(
            url=f"https://api.zoom.us/v2/users/{email}/recordings",
            headers=AUTHORIZATION_HEADER,
            params=post_data
        )
        recordings_data = response.json()
        if "meetings" in recordings_data:
            recordings.extend(recordings_data["meetings"])
        else:
            print(f"No 'meetings' key found in response for {email} from {start} to {end}")

    return recordings


def download_recording(download_url, email, filename, folder_name, worker_id=0):
    dl_dir = os.sep.join([DOWNLOAD_DIRECTORY, folder_name])
    sanitized_download_dir = path_validate.sanitize_filepath(dl_dir)
    sanitized_filename = path_validate.sanitize_filename(filename)
    full_filename = os.sep.join([sanitized_download_dir, sanitized_filename])

    os.makedirs(sanitized_download_dir, exist_ok=True)

    response = requests.get(download_url, stream=True)

    # total size in bytes.
    total_size = int(response.headers.get("content-length", 0))
    block_size = 32 * 1024  # 32 Kibibytes

    # create TQDM progress bar with position
    prog_bar = progress_bar.tqdm(
        dynamic_ncols=True,
        total=total_size,
        unit="iB",
        unit_scale=True,
        desc=f'    Download [{worker_id}]',
        position=worker_id,
        leave=False  # Remove progress bar after completion
    )
    try:
        with open(full_filename, "wb") as fd:
            for chunk in response.iter_content(block_size):
                prog_bar.update(len(chunk))
                fd.write(chunk)  # write video chunk to disk
        prog_bar.close()

        return True

    except Exception as e:
        print(
            f"{Color.RED}### The video recording with filename '{filename}' for user with email "
            f"'{email}' could not be downloaded because {Color.END}'{e}'"
        )

        return False


def load_completed_meeting_ids():
    if not USE_COMPLETED_LOG:
        print(f"{Color.DARK_CYAN}Completed log disabled. All recordings will be processed.{Color.END}\n")
        return

    try:
        with open(COMPLETED_MEETING_IDS_LOG, 'r') as fd:
            [COMPLETED_MEETING_IDS.add(line.strip()) for line in fd]

    except FileNotFoundError:
        print(
            f"{Color.DARK_CYAN}Log file not found. Creating new log file: {Color.END}"
            f"{COMPLETED_MEETING_IDS_LOG}\n"
        )


def get_last_run_time():
    """Get the timestamp of the last successful run"""
    try:
        with open(LAST_RUN_LOG, 'r') as fd:
            last_run_str = fd.read().strip()
            if last_run_str:
                # Parse ISO format timestamp
                last_run = datetime.fromisoformat(last_run_str)
                return last_run
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"{Color.YELLOW}⚠ Could not read last run time: {e}{Color.END}")
        return None
    return None


def save_last_run_time():
    """Save the current timestamp as the last run time"""
    try:
        current_time = datetime.now().isoformat()
        with open(LAST_RUN_LOG, 'w') as fd:
            fd.write(current_time)
        print(f"{Color.GREEN}✓ Saved run time: {current_time}{Color.END}")
    except Exception as e:
        print(f"{Color.YELLOW}⚠ Could not save last run time: {e}{Color.END}")


def calculate_auto_date_range():
    """Calculate start_date based on last run time, or use config values"""
    global RECORDING_START_DATE, RECORDING_END_DATE

    if not AUTO_DATE_RANGE:
        # Use configured dates
        return

    last_run = get_last_run_time()

    if last_run:
        # Use last run time as start date
        RECORDING_START_DATE = last_run.date()
        RECORDING_END_DATE = date.today()

        print(f"{Color.CYAN}📅 Auto date range enabled:{Color.END}")
        print(f"   Last run: {last_run.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"   Fetching recordings from {RECORDING_START_DATE} to {RECORDING_END_DATE}")
        print(f"   ({(RECORDING_END_DATE - RECORDING_START_DATE).days} days)\n")
    else:
        # First run - use configured start_date or default to 30 days ago
        if RECORDING_START_DATE:
            print(f"{Color.CYAN}📅 First run with auto date range:{Color.END}")
            print(f"   Using configured start_date: {RECORDING_START_DATE}")
            print(f"   Future runs will fetch from last run time\n")
        else:
            # Default to last 30 days for first run
            RECORDING_START_DATE = date.today() - timedelta(days=30)
            RECORDING_END_DATE = date.today()
            print(f"{Color.CYAN}📅 First run with auto date range:{Color.END}")
            print(f"   No start_date configured, using last 30 days")
            print(f"   Fetching recordings from {RECORDING_START_DATE} to {RECORDING_END_DATE}\n")


# Create a lock for thread-safe file writing
completed_log_lock = Lock()


def save_completed_meeting_id(recording_id):
    """Thread-safe function to save completed meeting ID"""
    if not USE_COMPLETED_LOG:
        return

    with completed_log_lock:
        with open(COMPLETED_MEETING_IDS_LOG, "a") as fd:
            fd.write(f"{recording_id}\n")
        COMPLETED_MEETING_IDS.add(recording_id)


def delete_recording_from_zoom(recording_id):
    """Delete a recording from Zoom cloud storage"""
    try:
        # URL encode the recording ID for the API call
        import urllib.parse
        encoded_id = urllib.parse.quote(recording_id, safe='')

        delete_url = f"https://api.zoom.us/v2/meetings/{encoded_id}/recordings"

        response = requests.delete(url=delete_url, headers=AUTHORIZATION_HEADER)

        if response.status_code == 204:
            # 204 No Content means successful deletion
            print(f"    {Color.GREEN}✓ Deleted from Zoom cloud{Color.END}")
            return True
        elif response.status_code == 404:
            print(f"    {Color.YELLOW}⚠ Recording not found in Zoom (may be already deleted){Color.END}")
            return True  # Consider this a success since the recording is gone
        else:
            print(f"    {Color.RED}✗ Failed to delete from Zoom (Status: {response.status_code}){Color.END}")
            return False

    except Exception as e:
        print(f"    {Color.RED}✗ Error deleting from Zoom: {str(e)}{Color.END}")
        return False


def verify_local_file_size(full_filename, expected_size):
    """Verify local file size matches expected size."""
    if not os.path.exists(full_filename):
        return {"status": "missing", "message": "File not found"}

    try:
        actual_size = os.path.getsize(full_filename)
        if actual_size == expected_size:
            return {
                "status": "verified",
                "expected": expected_size,
                "actual": actual_size
            }
        else:
            return {
                "status": "mismatch",
                "expected": expected_size,
                "actual": actual_size
            }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# Verification logging configuration
VERIFICATION_LOG = None
VERIFY_ON_DOWNLOAD = True
VERIFY_ON_UPLOAD = True
RETRY_ON_MISMATCH = True
MAX_VERIFICATION_RETRIES = 3

# Load verification config if available
if 'Verification' in CONF:
    VERIFICATION_LOG = config("Verification", "verification_log", "verification-log.json")
    VERIFY_ON_DOWNLOAD = config("Verification", "verify_on_download", True)
    VERIFY_ON_UPLOAD = config("Verification", "verify_on_upload", True)
    RETRY_ON_MISMATCH = config("Verification", "retry_on_mismatch", True)
    MAX_VERIFICATION_RETRIES = int(config("Verification", "max_verification_retries", 3))

verification_log_lock = Lock()


def log_verification_result(recording_uuid, file_id, filename, folder, expected_size, actual_size, status, storage_type):
    """Thread-safe function to log verification results to JSON file."""
    if not VERIFICATION_LOG:
        return

    try:
        with verification_log_lock:
            # Load existing log
            verification_data = {}
            if os.path.exists(VERIFICATION_LOG):
                try:
                    with open(VERIFICATION_LOG, 'r') as fd:
                        verification_data = json.load(fd)
                except (json.JSONDecodeError, FileNotFoundError):
                    verification_data = {}

            # Initialize recording entry if not exists
            if recording_uuid not in verification_data:
                verification_data[recording_uuid] = {"files": []}

            # Add file verification result
            verification_data[recording_uuid]["files"].append({
                "file_id": file_id,
                "filename": filename,
                "folder": folder,
                "expected_size": expected_size,
                "actual_size": actual_size,
                "status": status,
                "storage": storage_type,
                "timestamp": datetime.now().isoformat()
            })

            # Save log
            with open(VERIFICATION_LOG, 'w') as fd:
                json.dump(verification_data, fd, indent=2)

    except Exception as e:
        print(f"{Color.YELLOW}⚠ Failed to log verification result: {e}{Color.END}")


def process_recording(recording, index, total_count, email, storage_service, worker_id=0):
    """Process a single recording (download and optionally upload)"""
    try:
        recording_id = recording["uuid"]

        if USE_COMPLETED_LOG and recording_id in COMPLETED_MEETING_IDS:
            print(f"\n==> [{index + 1}/{total_count}] Skipping already downloaded recording")
            return True

        try:
            downloads = get_downloads(recording)
        except Exception as e:
            print(
                f"{Color.RED}### [{index + 1}/{total_count}] Failed to get download URLs: {str(e)}{Color.END}"
            )
            return False

        print(f"\n==> [{index + 1}/{total_count}] Processing recording")

        all_files_success = True
        for file_type, file_extension, download_url, recording_type, rec_id, expected_size in downloads:
            try:
                params = {
                    "file_extension": file_extension,
                    "recording": recording,
                    "recording_id": rec_id,
                    "recording_type": recording_type
                }
                filename, folder_name = format_filename(params)

                print(f"    > [{index + 1}/{total_count}] Downloading {filename}")
                sanitized_download_dir = path_validate.sanitize_filepath(
                    os.sep.join([DOWNLOAD_DIRECTORY, folder_name])
                )
                sanitized_filename = path_validate.sanitize_filename(filename)
                full_filename = os.sep.join([sanitized_download_dir, sanitized_filename])

                if download_recording(download_url, email, filename, folder_name, worker_id):
                    # Verify local file size if enabled
                    if VERIFY_ON_DOWNLOAD and expected_size > 0:
                        verify_result = verify_local_file_size(full_filename, expected_size)

                        if verify_result["status"] == "mismatch":
                            print(f"    {Color.RED}✗ Download size mismatch: expected {expected_size}, got {verify_result['actual']}{Color.END}")
                            log_verification_result(recording_id, rec_id, sanitized_filename, folder_name,
                                                  expected_size, verify_result.get('actual', 0), "mismatch", "local")
                            all_files_success = False
                            continue
                        elif verify_result["status"] == "verified":
                            print(f"    {Color.GREEN}✓ Download verified ({expected_size} bytes){Color.END}")
                            log_verification_result(recording_id, rec_id, sanitized_filename, folder_name,
                                                  expected_size, verify_result['actual'], "verified", "local")

                    # Upload to cloud storage if enabled
                    upload_success = False
                    storage_type = "local"

                    if GDRIVE_ENABLED and storage_service:
                        storage_type = "gdrive"
                        print(f"    > [{index + 1}/{total_count}] Uploading to Google Drive...")
                        upload_success = storage_service.upload_file(full_filename, folder_name, sanitized_filename,
                                                                     worker_id)

                        # Verify upload if enabled
                        if upload_success and VERIFY_ON_UPLOAD and expected_size > 0:
                            verify_result = storage_service.verify_file_size(folder_name, sanitized_filename, expected_size)

                            if verify_result["status"] == "mismatch":
                                print(f"    {Color.RED}✗ Upload size mismatch: expected {expected_size}, got {verify_result.get('actual', 0)}{Color.END}")
                                log_verification_result(recording_id, rec_id, sanitized_filename, folder_name,
                                                      expected_size, verify_result.get('actual', 0), "mismatch", storage_type)
                                upload_success = False
                            elif verify_result["status"] == "verified":
                                print(f"    {Color.GREEN}✓ Upload verified ({expected_size} bytes){Color.END}")
                                log_verification_result(recording_id, rec_id, sanitized_filename, folder_name,
                                                      expected_size, verify_result['actual'], "verified", storage_type)
                            elif verify_result["status"] == "error":
                                print(f"    {Color.YELLOW}⚠ Upload verification error: {verify_result.get('message')}{Color.END}")
                                log_verification_result(recording_id, rec_id, sanitized_filename, folder_name,
                                                      expected_size, 0, "error", storage_type)

                    elif S3_ENABLED and storage_service:
                        storage_type = "s3"
                        print(f"    > [{index + 1}/{total_count}] Uploading to S3/Spaces...")
                        upload_success = storage_service.upload_file(full_filename, folder_name, sanitized_filename,
                                                                     worker_id)

                        # Verify upload if enabled
                        if upload_success and VERIFY_ON_UPLOAD and expected_size > 0:
                            verify_result = storage_service.verify_file_size(folder_name, sanitized_filename, expected_size)

                            if verify_result["status"] == "mismatch":
                                print(f"    {Color.RED}✗ Upload size mismatch: expected {expected_size}, got {verify_result.get('actual', 0)}{Color.END}")
                                log_verification_result(recording_id, rec_id, sanitized_filename, folder_name,
                                                      expected_size, verify_result.get('actual', 0), "mismatch", storage_type)
                                upload_success = False
                            elif verify_result["status"] == "verified":
                                print(f"    {Color.GREEN}✓ Upload verified ({expected_size} bytes){Color.END}")
                                log_verification_result(recording_id, rec_id, sanitized_filename, folder_name,
                                                      expected_size, verify_result['actual'], "verified", storage_type)
                            elif verify_result["status"] == "error":
                                print(f"    {Color.YELLOW}⚠ Upload verification error: {verify_result.get('message')}{Color.END}")
                                log_verification_result(recording_id, rec_id, sanitized_filename, folder_name,
                                                      expected_size, 0, "error", storage_type)

                    # Clean up local file if upload was successful
                    if upload_success and os.path.exists(full_filename):
                        os.remove(full_filename)
                        if os.path.exists(sanitized_download_dir) and not os.listdir(sanitized_download_dir):
                            os.rmdir(sanitized_download_dir)
                else:
                    all_files_success = False

            except Exception as e:
                print(
                    f"{Color.RED}### [{index + 1}/{total_count}] Failed to process file {file_type}: "
                    f"{str(e)}{Color.END}"
                )
                all_files_success = False
                continue

        # Only mark as complete if all files were processed successfully
        if all_files_success:
            save_completed_meeting_id(recording_id)

            # Delete from Zoom if configured
            if DELETE_FROM_ZOOM:
                print(f"    > [{index + 1}/{total_count}] Deleting from Zoom cloud...")
                delete_recording_from_zoom(recording_id)

            return True
        return False

    except Exception as e:
        print(f"{Color.RED}### [{index + 1}/{total_count}] Unexpected error: {str(e)}{Color.END}")
        return False


def get_recording_by_uuid(recording_uuid):
    """Fetch a specific recording from Zoom API by UUID."""
    try:
        # The recording UUID might be URL-encoded
        encoded_uuid = urllib.parse.quote(recording_uuid, safe='')
        url = f"https://api.zoom.us/v2/meetings/{encoded_uuid}/recordings"

        response = requests.get(url=url, headers=AUTHORIZATION_HEADER)

        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            return None  # Recording not found (may have been deleted)
        else:
            print(f"{Color.YELLOW}⚠ Failed to fetch recording {recording_uuid}: Status {response.status_code}{Color.END}")
            return None

    except Exception as e:
        print(f"{Color.RED}Error fetching recording {recording_uuid}: {str(e)}{Color.END}")
        return None


def build_active_recordings_lookup(users, from_date, to_date):
    """Build lookup dictionary of all active recordings by UUID."""
    print(f"{Color.DARK_CYAN}Building lookup of active recordings from {from_date} to {to_date}...{Color.END}")

    recordings_by_uuid = {}

    # Temporarily set global date range for list_recordings()
    global RECORDING_START_DATE, RECORDING_END_DATE
    original_start = RECORDING_START_DATE
    original_end = RECORDING_END_DATE

    RECORDING_START_DATE = from_date
    RECORDING_END_DATE = to_date

    try:
        for email, user_id, first_name, last_name, status in users:
            user_info = f"{first_name} {last_name} - {email}" if first_name and last_name else f"{email}"
            print(f"  Fetching active recordings for {user_info}...")

            recordings = list_recordings(user_id)
            for rec in recordings:
                recordings_by_uuid[rec['uuid']] = rec

            if recordings:
                print(f"    Found {len(recordings)} active recording(s)")
    finally:
        # Restore original date range
        RECORDING_START_DATE = original_start
        RECORDING_END_DATE = original_end

    print(f"{Color.GREEN}Total active recordings found: {len(recordings_by_uuid)}{Color.END}\n")
    return recordings_by_uuid


def build_trash_recordings_lookup(users):
    """Build lookup dictionary of all trashed recordings by UUID."""
    print(f"{Color.DARK_CYAN}Building lookup of trashed recordings...{Color.END}")

    trash_by_uuid = {}

    for email, user_id, first_name, last_name, status in users:
        user_info = f"{first_name} {last_name} - {email}" if first_name and last_name else f"{email}"
        print(f"  Fetching trash for {user_info}...")

        trash_recordings = list_trash_recordings(user_id)
        for rec in trash_recordings:
            trash_by_uuid[rec['uuid']] = rec

        if trash_recordings:
            print(f"    Found {len(trash_recordings)} trashed recording(s)")

    print(f"{Color.YELLOW}Total trashed recordings found: {len(trash_by_uuid)}{Color.END}\n")
    return trash_by_uuid


def verify_completed_downloads(storage_service, storage_type):
    """Verify all completed downloads against Zoom API and storage backend."""
    print(f"\n{Color.BOLD}{'='*70}{Color.END}")
    print(f"{Color.BOLD}Starting verification of completed downloads...{Color.END}")
    print(f"{Color.BOLD}{'='*70}{Color.END}\n")

    print(f"{Color.DARK_CYAN}Note: Using date range from config ({RECORDING_START_DATE} to {RECORDING_END_DATE}){Color.END}")
    print(f"{Color.DARK_CYAN}Recordings outside this range will show as 'Not Accessible'{Color.END}\n")

    load_completed_meeting_ids()
    total = len(COMPLETED_MEETING_IDS)

    if total == 0:
        print(f"{Color.YELLOW}No completed downloads found in log.{Color.END}")
        return {"verified": [], "mismatches": [], "missing": [], "errors": [], "in_trash": [], "not_accessible": [], "fully_verified": []}

    print(f"Found {total} completed recordings to verify.\n")

    # Build lookups of all accessible recordings
    users = get_users()
    active_recordings = build_active_recordings_lookup(users, RECORDING_START_DATE, RECORDING_END_DATE)
    trash_recordings = build_trash_recordings_lookup(users)

    verified = []
    mismatches = []
    missing = []
    errors = []
    in_trash = []
    not_accessible = []

    # Track verified files by recording UUID to find fully verified recordings
    verified_by_recording = {}  # {uuid: [list of verified files]}
    failed_recordings = set()  # UUIDs with any failures

    for idx, recording_uuid in enumerate(COMPLETED_MEETING_IDS, 1):
        print(f"\n[{idx}/{total}] Verifying recording: {recording_uuid}")

        # Check if recording is in active recordings
        recording_data = active_recordings.get(recording_uuid)

        if recording_data:
            # Recording is active - proceed with verification
            print(f"  {Color.GREEN}✓ Found in active recordings{Color.END}")
        elif recording_uuid in trash_recordings:
            # Recording is in trash
            print(f"  {Color.YELLOW}⚠ Recording in Zoom trash (restore to verify files){Color.END}")
            in_trash.append(recording_uuid)
            continue
        else:
            # Recording not accessible (archived or permanently deleted)
            print(f"  {Color.YELLOW}⚠ Recording not accessible (may be archived, outside date range, or permanently deleted){Color.END}")
            not_accessible.append(recording_uuid)
            continue

        try:
            downloads = get_downloads(recording_data)

            for file_type, file_ext, url, rec_type, file_id, expected_size in downloads:
                if expected_size == 0:
                    continue  # Skip CC/TIMELINE files without size

                params = {
                    "file_extension": file_ext,
                    "recording": recording_data,
                    "recording_id": file_id,
                    "recording_type": rec_type
                }
                filename, folder_name = format_filename(params)
                sanitized_filename = path_validate.sanitize_filename(filename)

                print(f"  Checking: {sanitized_filename}")

                # Verify based on storage type
                if storage_type == "local":
                    sanitized_download_dir = path_validate.sanitize_filepath(
                        os.sep.join([DOWNLOAD_DIRECTORY, folder_name])
                    )
                    full_filename = os.sep.join([sanitized_download_dir, sanitized_filename])
                    result = verify_local_file_size(full_filename, expected_size)
                elif storage_type == "gdrive" and storage_service:
                    result = storage_service.verify_file_size(folder_name, sanitized_filename, expected_size)
                elif storage_type == "s3" and storage_service:
                    result = storage_service.verify_file_size(folder_name, sanitized_filename, expected_size)
                else:
                    print(f"    {Color.RED}✗ Unknown storage type{Color.END}")
                    continue

                # Process result
                if result["status"] == "verified":
                    verified.append((recording_uuid, sanitized_filename, expected_size))
                    print(f"    {Color.GREEN}✓ Verified ({expected_size} bytes){Color.END}")

                    # Track for fully verified recordings
                    if recording_uuid not in verified_by_recording:
                        verified_by_recording[recording_uuid] = []
                    verified_by_recording[recording_uuid].append(sanitized_filename)

                elif result["status"] == "mismatch":
                    mismatches.append((recording_uuid, sanitized_filename, result))
                    print(f"    {Color.RED}✗ Size mismatch: expected {expected_size}, got {result.get('actual', 'unknown')}{Color.END}")
                    failed_recordings.add(recording_uuid)
                elif result["status"] == "missing":
                    missing.append((recording_uuid, sanitized_filename, expected_size))
                    print(f"    {Color.RED}✗ File missing{Color.END}")
                    failed_recordings.add(recording_uuid)
                elif result["status"] == "error":
                    errors.append((recording_uuid, sanitized_filename, result.get('message', 'Unknown error')))
                    print(f"    {Color.YELLOW}⚠ Error: {result.get('message')}{Color.END}")
                    failed_recordings.add(recording_uuid)

        except Exception as e:
            print(f"  {Color.RED}Error processing recording: {str(e)}{Color.END}")
            errors.append((recording_uuid, "N/A", str(e)))
            failed_recordings.add(recording_uuid)

    # Identify fully verified recordings (all files passed verification)
    fully_verified_recordings = []
    for uuid in verified_by_recording:
        if uuid not in failed_recordings:
            recording_data = active_recordings.get(uuid)
            if recording_data:
                # Count files that have size > 0 (verifiable files)
                downloads = get_downloads(recording_data)
                total_verifiable_files = sum(1 for _, _, _, _, _, size in downloads if size > 0)

                if len(verified_by_recording[uuid]) == total_verifiable_files:
                    fully_verified_recordings.append((uuid, recording_data))

    # Print summary
    print(f"\n{Color.BOLD}{'='*70}{Color.END}")
    print(f"{Color.BOLD}Verification Summary:{Color.END}")
    print(f"{Color.BOLD}{'='*70}{Color.END}")
    print(f"  {Color.GREEN}✓ Verified files: {len(verified)}{Color.END}")
    print(f"  {Color.GREEN}✓ Fully verified recordings: {len(fully_verified_recordings)}{Color.END}")
    print(f"  {Color.RED}✗ Size mismatches: {len(mismatches)}{Color.END}")
    print(f"  {Color.RED}✗ Missing files: {len(missing)}{Color.END}")
    print(f"  {Color.YELLOW}⚠ Errors: {len(errors)}{Color.END}")
    print(f"  {Color.YELLOW}ℹ In Zoom trash: {len(in_trash)} (restore to verify){Color.END}")
    print(f"  {Color.DARK_CYAN}ℹ Not accessible: {len(not_accessible)} (archived, outside date range, or deleted){Color.END}")
    print(f"{Color.BOLD}{'='*70}{Color.END}\n")

    return {
        "verified": verified,
        "mismatches": mismatches,
        "missing": missing,
        "errors": errors,
        "in_trash": in_trash,
        "not_accessible": not_accessible,
        "fully_verified": fully_verified_recordings
    }


def auto_fix_corrupted_recordings(verification_results):
    """Remove corrupted/missing recordings from completed log for re-download."""
    mismatches = verification_results["mismatches"]
    missing = verification_results["missing"]

    if not mismatches and not missing:
        print(f"{Color.GREEN}No corrupted or missing files found. Nothing to fix.{Color.END}")
        return

    # Collect all affected recording UUIDs
    affected_uuids = set()
    for uuid, filename, _ in mismatches:
        affected_uuids.add(uuid)
    for uuid, filename, _ in missing:
        affected_uuids.add(uuid)

    print(f"\n{Color.BOLD}Auto-Fix Summary:{Color.END}")
    print(f"  Found {len(affected_uuids)} recordings with issues:")
    print(f"    - {len(mismatches)} file(s) with size mismatches")
    print(f"    - {len(missing)} missing file(s)")
    print(f"\n  These recordings will be removed from the completed log and re-downloaded on next run.\n")

    # Ask for confirmation
    response = input(f"{Color.BOLD}Proceed with auto-fix? (y/n): {Color.END}")

    if response.lower() != 'y':
        print(f"{Color.YELLOW}Auto-fix cancelled.{Color.END}")
        return

    # Remove affected UUIDs from the set
    for uuid in affected_uuids:
        if uuid in COMPLETED_MEETING_IDS:
            COMPLETED_MEETING_IDS.remove(uuid)

    # Rewrite the completed log
    try:
        with open(COMPLETED_MEETING_IDS_LOG, 'w') as fd:
            for uuid in COMPLETED_MEETING_IDS:
                fd.write(f"{uuid}\n")

        print(f"\n{Color.GREEN}✓ Auto-fix complete!{Color.END}")
        print(f"  Removed {len(affected_uuids)} recording(s) from completed log.")
        print(f"  Run the downloader again to re-download these recordings.\n")

        # Generate detailed report
        report_file = "verification-report.log"
        with open(report_file, 'w') as fd:
            fd.write("Zoom Recording Downloader - Verification Report\n")
            fd.write("=" * 70 + "\n")
            fd.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

            if mismatches:
                fd.write("SIZE MISMATCHES:\n")
                fd.write("-" * 70 + "\n")
                for uuid, filename, result in mismatches:
                    fd.write(f"Recording: {uuid}\n")
                    fd.write(f"  File: {filename}\n")
                    fd.write(f"  Expected: {result.get('expected', 'unknown')} bytes\n")
                    fd.write(f"  Actual: {result.get('actual', 'unknown')} bytes\n\n")

            if missing:
                fd.write("\nMISSING FILES:\n")
                fd.write("-" * 70 + "\n")
                for uuid, filename, expected_size in missing:
                    fd.write(f"Recording: {uuid}\n")
                    fd.write(f"  File: {filename}\n")
                    fd.write(f"  Expected size: {expected_size} bytes\n\n")

        print(f"  Detailed report saved to: {report_file}\n")

    except Exception as e:
        print(f"{Color.RED}Error during auto-fix: {str(e)}{Color.END}")


def delete_verified_recordings(fully_verified_recordings):
    """Delete fully verified recordings from Zoom to free up cloud storage."""
    if not fully_verified_recordings:
        print(f"{Color.YELLOW}No fully verified recordings to delete.{Color.END}")
        return

    print(f"\n{Color.BOLD}{'='*70}{Color.END}")
    print(f"{Color.BOLD}Delete Verified Recordings from Zoom{Color.END}")
    print(f"{Color.BOLD}{'='*70}{Color.END}\n")
    print(f"Found {len(fully_verified_recordings)} fully verified recording(s).")
    print(f"{Color.YELLOW}These recordings have been successfully verified against your storage.{Color.END}")
    print(f"{Color.YELLOW}Deleting them from Zoom will free up cloud storage space.{Color.END}\n")

    # Show some details about what will be deleted
    print(f"{Color.BOLD}Recordings to be deleted:{Color.END}")
    for idx, (uuid, recording_data) in enumerate(fully_verified_recordings[:5], 1):
        topic = recording_data.get('topic', 'N/A')
        start_time = recording_data.get('start_time', 'N/A')
        print(f"  {idx}. {topic} ({start_time})")

    if len(fully_verified_recordings) > 5:
        print(f"  ... and {len(fully_verified_recordings) - 5} more recording(s)")

    print()

    # Ask for confirmation
    response = input(f"{Color.BOLD}⚠ WARNING: This will permanently delete these recordings from Zoom!\n   Proceed with deletion? (y/n): {Color.END}")

    if response.lower() != 'y':
        print(f"{Color.YELLOW}Deletion cancelled.{Color.END}")
        return

    # Delete recordings
    print(f"\n{Color.BOLD}Deleting recordings from Zoom...{Color.END}\n")
    successful_deletions = []
    failed_deletions = []

    for idx, (uuid, recording_data) in enumerate(fully_verified_recordings, 1):
        topic = recording_data.get('topic', 'N/A')
        print(f"[{idx}/{len(fully_verified_recordings)}] {topic}")
        print(f"  UUID: {uuid}")

        if delete_recording_from_zoom(uuid):
            successful_deletions.append(uuid)
        else:
            failed_deletions.append(uuid)

    # Print summary
    print(f"\n{Color.BOLD}{'='*70}{Color.END}")
    print(f"{Color.BOLD}Deletion Summary:{Color.END}")
    print(f"{Color.BOLD}{'='*70}{Color.END}")
    print(f"  {Color.GREEN}✓ Successfully deleted: {len(successful_deletions)}{Color.END}")
    if failed_deletions:
        print(f"  {Color.RED}✗ Failed to delete: {len(failed_deletions)}{Color.END}")
    print(f"{Color.BOLD}{'='*70}{Color.END}\n")

    if successful_deletions:
        print(f"{Color.GREEN}Successfully freed up Zoom cloud storage!{Color.END}\n")


def list_trash_recordings(email):
    """Fetch all recordings from trash (deleted within last 30 days)."""
    recordings = []

    try:
        # Note: Trash API does NOT support date filtering, so we fetch all trash
        params = {
            "userId": email,
            "trash": "true",
            "page_size": 300
        }

        response = requests.get(
            url=f"https://api.zoom.us/v2/users/{email}/recordings",
            headers=AUTHORIZATION_HEADER,
            params=params
        )

        if not response.ok:
            print(f"{Color.YELLOW}⚠ Could not fetch trash recordings for {email}: Status {response.status_code}{Color.END}")
            return recordings

        recordings_data = response.json()

        if "meetings" in recordings_data:
            recordings.extend(recordings_data["meetings"])

        # Handle pagination if needed
        while "next_page_token" in recordings_data and recordings_data["next_page_token"]:
            params["next_page_token"] = recordings_data["next_page_token"]
            response = requests.get(
                url=f"https://api.zoom.us/v2/users/{email}/recordings",
                headers=AUTHORIZATION_HEADER,
                params=params
            )
            recordings_data = response.json()
            if "meetings" in recordings_data:
                recordings.extend(recordings_data["meetings"])

    except Exception as e:
        print(f"{Color.RED}Error fetching trash recordings for {email}: {str(e)}{Color.END}")

    return recordings


def restore_recording_from_zoom(recording_uuid):
    """Restore a recording from Zoom trash back to active storage."""
    try:
        # URL encode the recording UUID for the API call
        encoded_uuid = urllib.parse.quote(recording_uuid, safe='')

        restore_url = f"https://api.zoom.us/v2/meetings/{encoded_uuid}/recordings/status"

        response = requests.put(
            url=restore_url,
            headers=AUTHORIZATION_HEADER,
            json={"action": "recover"}
        )

        if response.status_code == 204:
            # 204 No Content means successful restore
            print(f"    {Color.GREEN}✓ Restored from trash{Color.END}")
            return True
        elif response.status_code == 404:
            print(f"    {Color.YELLOW}⚠ Recording not found in trash (may have expired){Color.END}")
            return False
        else:
            print(f"    {Color.RED}✗ Failed to restore (Status: {response.status_code}){Color.END}")
            return False

    except Exception as e:
        print(f"    {Color.RED}✗ Error restoring recording: {str(e)}{Color.END}")
        return False


def filter_recordings_by_date(recordings, start_date, end_date):
    """Filter recordings by meeting start_time within date range."""
    filtered = []

    for recording in recordings:
        try:
            # Parse meeting start time
            meeting_time = parser.parse(recording["start_time"]).replace(tzinfo=timezone.utc).date()

            # Check if within date range
            if start_date <= meeting_time <= end_date:
                filtered.append(recording)
        except Exception as e:
            print(f"{Color.YELLOW}⚠ Could not parse date for recording {recording.get('uuid', 'unknown')}: {e}{Color.END}")
            continue

    return filtered


def remove_from_completed_log(recording_uuids):
    """Remove list of UUIDs from completed-downloads.log (thread-safe)."""
    try:
        with completed_log_lock:
            # Remove from set
            for uuid in recording_uuids:
                if uuid in COMPLETED_MEETING_IDS:
                    COMPLETED_MEETING_IDS.remove(uuid)

            # Rewrite log file
            with open(COMPLETED_MEETING_IDS_LOG, 'w') as fd:
                for uuid in COMPLETED_MEETING_IDS:
                    fd.write(f"{uuid}\n")

        print(f"{Color.GREEN}✓ Removed {len(recording_uuids)} recording(s) from completed log{Color.END}")
        return True

    except Exception as e:
        print(f"{Color.RED}Error removing from completed log: {str(e)}{Color.END}")
        return False


def restore_deleted_workflow(from_date, to_date):
    """Main workflow for restoring deleted recordings from trash."""
    print(f"\n{Color.BOLD}{'='*70}{Color.END}")
    print(f"{Color.BOLD}Restore Deleted Recordings{Color.END}")
    print(f"{Color.BOLD}{'='*70}{Color.END}\n")

    print(f"{Color.DARK_CYAN}Date range: {from_date} to {to_date}{Color.END}")
    print(f"{Color.YELLOW}Note: Zoom trash holds recordings for max 30 days{Color.END}\n")

    load_access_token()

    print(f"{Color.BOLD}Getting user accounts...{Color.END}")
    users = get_users()

    all_trash_recordings = []

    # Fetch trash recordings for all users
    for email, user_id, first_name, last_name, status in users:
        user_info = f"{first_name} {last_name} - {email}" if first_name and last_name else f"{email}"
        print(f"\n{Color.BOLD}Fetching trash for {user_info}{Color.END}")

        trash_recordings = list_trash_recordings(user_id)

        if trash_recordings:
            # Add user info to each recording
            for recording in trash_recordings:
                recording['_user_email'] = email
                recording['_user_name'] = user_info

            # Filter by date for this user to show accurate count
            user_filtered = filter_recordings_by_date(trash_recordings, from_date, to_date)
            all_trash_recordings.extend(trash_recordings)

            if user_filtered:
                print(f"  Found {len(user_filtered)} recording(s) in date range (total in trash: {len(trash_recordings)})")
            else:
                print(f"  Found 0 recordings in date range (total in trash: {len(trash_recordings)})")

    if not all_trash_recordings:
        print(f"\n{Color.YELLOW}No deleted recordings found in trash.{Color.END}")
        return

    # Filter by date
    filtered_recordings = filter_recordings_by_date(all_trash_recordings, from_date, to_date)

    if not filtered_recordings:
        print(f"\n{Color.YELLOW}No deleted recordings found within date range {from_date} to {to_date}.{Color.END}")
        print(f"Total recordings in trash: {len(all_trash_recordings)}")
        return

    # Display recordings
    print(f"\n{Color.BOLD}{'='*70}{Color.END}")
    print(f"{Color.BOLD}Found {len(filtered_recordings)} deleted recording(s) (meeting dates: {from_date} to {to_date}):{Color.END}")
    print(f"{Color.BOLD}{'='*70}{Color.END}\n")

    for idx, recording in enumerate(filtered_recordings, 1):
        meeting_time = parser.parse(recording["start_time"]).replace(tzinfo=timezone.utc)
        meeting_time_str = meeting_time.strftime("%Y-%m-%d %H:%M UTC")
        topic = recording.get("topic", "No Topic")
        uuid = recording.get("uuid", "Unknown")
        user_name = recording.get("_user_name", "Unknown User")
        file_count = len(recording.get("recording_files", []))

        print(f"{idx}. [{meeting_time_str}] {topic}")
        print(f"   User: {user_name}")
        print(f"   UUID: {uuid}")
        print(f"   Files: {file_count}\n")

    # Ask for confirmation
    print(f"{Color.BOLD}{'='*70}{Color.END}")
    response = input(f"{Color.BOLD}Restore these {len(filtered_recordings)} recording(s) from trash? (y/n): {Color.END}")

    if response.lower() != 'y':
        print(f"{Color.YELLOW}Restore cancelled.{Color.END}")
        return

    # Restore recordings
    print(f"\n{Color.BOLD}Restoring recordings...{Color.END}\n")

    restored_uuids = []
    failed_uuids = []

    for idx, recording in enumerate(filtered_recordings, 1):
        uuid = recording.get("uuid")
        topic = recording.get("topic", "No Topic")

        print(f"[{idx}/{len(filtered_recordings)}] Restoring: {topic}")

        if restore_recording_from_zoom(uuid):
            restored_uuids.append(uuid)
        else:
            failed_uuids.append(uuid)

    # Summary
    print(f"\n{Color.BOLD}{'='*70}{Color.END}")
    print(f"{Color.BOLD}Restore Summary:{Color.END}")
    print(f"{Color.BOLD}{'='*70}{Color.END}")
    print(f"  {Color.GREEN}✓ Successfully restored: {len(restored_uuids)}{Color.END}")
    print(f"  {Color.RED}✗ Failed: {len(failed_uuids)}{Color.END}")
    print(f"{Color.BOLD}{'='*70}{Color.END}\n")

    # Ask about removing from completed log
    if restored_uuids:
        load_completed_meeting_ids()

        # Check which restored recordings are in completed log
        in_completed_log = [uuid for uuid in restored_uuids if uuid in COMPLETED_MEETING_IDS]

        if in_completed_log:
            print(f"{Color.YELLOW}Note: {len(in_completed_log)} of the restored recording(s) were previously downloaded.{Color.END}")
            response = input(f"{Color.BOLD}Remove from completed log so they can be downloaded again? (y/n): {Color.END}")

            if response.lower() == 'y':
                remove_from_completed_log(in_completed_log)
                print(f"\n{Color.GREEN}You can now run the downloader to download these restored recordings.{Color.END}\n")
            else:
                print(f"{Color.DARK_CYAN}Recordings remain marked as completed.{Color.END}\n")
        else:
            print(f"{Color.GREEN}Restored recordings can be downloaded on next run.{Color.END}\n")


def handle_graceful_shutdown(signal_received, frame):
    print(f"\n{Color.DARK_CYAN}SIGINT or CTRL-C detected. Exiting gracefully.{Color.END}")

    system.exit(0)


# ################################################################
# #                        MAIN                                  #
# ################################################################

def main():
    global GDRIVE_ENABLED, S3_ENABLED

    # Parse command-line arguments
    parser = argparse.ArgumentParser(description='Zoom Recording Downloader')
    parser.add_argument('--verify', action='store_true',
                       help='Verify previously downloaded recordings against Zoom API')
    parser.add_argument('--delete-verified', action='store_true',
                       help='Delete recordings from Zoom after successful verification (use with --verify)')
    parser.add_argument('--restore-deleted', action='store_true',
                       help='Restore deleted recordings from Zoom trash (within last 30 days)')
    parser.add_argument('--from', dest='from_date', type=str,
                       help='Start date for restore (format: YYYY-MM-DD). Defaults to 30 days ago')
    parser.add_argument('--to', dest='to_date', type=str,
                       help='End date for restore (format: YYYY-MM-DD). Defaults to today')
    parser.add_argument('--use-config-dates', action='store_true',
                       help='Use start_date/end_date from config Recordings section for restore')
    args = parser.parse_args()

    # clear the screen buffer
    os.system('cls' if os.name == 'nt' else 'clear')

    # show the logo
    print(f"""
        {Color.DARK_CYAN}


                             ,*****************.
                          *************************
                        *****************************
                      *********************************
                     ******               ******* ******
                    *******                .**    ******
                    *******                       ******/
                    *******                       /******
                    ///////                 //    //////
                    ///////*              ./////.//////
                     ////////////////////////////////*
                       /////////////////////////////
                          /////////////////////////
                             ,/////////////////

                        Zoom Recording Downloader

                        V{APP_VERSION}

        {Color.END}
    """)

    # Handle verification mode
    if args.verify:
        print(f"{Color.BOLD}Verification Mode{Color.END}\n")
        print("Select storage backend to verify:")
        print("1. Local Storage")
        print("2. Google Drive")
        print("3. Amazon S3 / DigitalOcean Spaces")
        choice = input("Enter choice (1-3): ")

        storage_service = None
        storage_type = "local"

        if choice == "2":
            GDRIVE_ENABLED = True
            storage_service = setup_google_drive()
            storage_type = "gdrive"
            if not storage_service:
                print(f"{Color.RED}Failed to initialize Google Drive. Exiting.{Color.END}")
                system.exit(1)
        elif choice == "3":
            S3_ENABLED = True
            storage_service = setup_s3()
            storage_type = "s3"
            if not storage_service:
                print(f"{Color.RED}Failed to initialize S3. Exiting.{Color.END}")
                system.exit(1)

        load_access_token()

        # Run verification
        results = verify_completed_downloads(storage_service, storage_type)

        # Offer auto-fix if issues found
        if results["mismatches"] or results["missing"]:
            auto_fix_corrupted_recordings(results)
        else:
            print(f"{Color.GREEN}All files verified successfully!{Color.END}")

        # Delete verified recordings from Zoom if requested
        if args.delete_verified and results["fully_verified"]:
            delete_verified_recordings(results["fully_verified"])

        return

    # Handle restore-deleted mode
    if args.restore_deleted:
        print(f"{Color.BOLD}Restore Deleted Recordings Mode{Color.END}\n")

        # Parse date range from command-line arguments
        # Priority: 1) --use-config-dates, 2) --from/--to, 3) default (last 30 days)
        if args.use_config_dates:
            # Use dates from config
            if not RECORDING_START_DATE or not RECORDING_END_DATE:
                print(f"{Color.RED}Error: No dates configured in Recordings section of config file{Color.END}")
                print(f"{Color.YELLOW}Please set start_date and end_date in zoom-recording-downloader.conf{Color.END}")
                system.exit(1)

            restore_from_date = RECORDING_START_DATE
            restore_to_date = RECORDING_END_DATE
            print(f"{Color.DARK_CYAN}Using dates from config: {restore_from_date} to {restore_to_date}{Color.END}\n")

        elif args.from_date or args.to_date:
            # Use command-line dates
            if args.from_date:
                try:
                    restore_from_date = parser.parse(args.from_date).replace(tzinfo=timezone.utc).date()
                except Exception as e:
                    print(f"{Color.RED}Invalid --from date format: {args.from_date}. Use YYYY-MM-DD{Color.END}")
                    system.exit(1)
            else:
                # Default from date if only --to is provided
                restore_from_date = date.today() - timedelta(days=30)

            if args.to_date:
                try:
                    restore_to_date = parser.parse(args.to_date).replace(tzinfo=timezone.utc).date()
                except Exception as e:
                    print(f"{Color.RED}Invalid --to date format: {args.to_date}. Use YYYY-MM-DD{Color.END}")
                    system.exit(1)
            else:
                # Default to date if only --from is provided
                restore_to_date = date.today()

            print(f"{Color.DARK_CYAN}Using date range: {restore_from_date} to {restore_to_date}{Color.END}\n")

        else:
            # Default to last 30 days
            restore_from_date = date.today() - timedelta(days=30)
            restore_to_date = date.today()
            print(f"{Color.DARK_CYAN}Using default date range: {restore_from_date} to {restore_to_date} (last 30 days){Color.END}\n")

        # Run restore workflow
        restore_deleted_workflow(restore_from_date, restore_to_date)

        return

    # Storage choice prompt
    print("\nChoose download method:")
    print("1. Local Storage")
    print("2. Google Drive")
    print("3. Amazon S3 / DigitalOcean Spaces")
    choice = input("Enter choice (1-3): ")

    storage_service = None
    if choice == "2":
        GDRIVE_ENABLED = True
        storage_service = setup_google_drive()
        if not storage_service:
            GDRIVE_ENABLED = False
    elif choice == "3":
        S3_ENABLED = True
        storage_service = setup_s3()
        if not storage_service:
            S3_ENABLED = False

    load_access_token()
    load_completed_meeting_ids()

    # Calculate automatic date range if enabled
    calculate_auto_date_range()

    print(f"{Color.BOLD}Getting user accounts...{Color.END}")
    users = get_users()

    for email, user_id, first_name, last_name, status in users:
        userInfo = (
            f"{first_name} {last_name} - {email}" if first_name and last_name else f"{email}"
        )
        status_indicator = f"[{status.upper()}]" if status != "active" else ""
        print(f"\n{Color.BOLD}Getting recording list for {userInfo} {status_indicator}{Color.END}")

        recordings = list_recordings(user_id)
        total_count = len(recordings)
        print(f"==> Found {total_count} recordings")

        if total_count == 0:
            continue

        # Process recordings in parallel
        print(f"\n{Color.BOLD}Processing up to {MAX_WORKERS} recordings in parallel...{Color.END}\n")

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Submit all recording processing tasks with worker IDs
            future_to_recording = {}
            for index, recording in enumerate(recordings):
                # Assign worker_id based on index modulo max_workers
                worker_id = index % MAX_WORKERS
                future = executor.submit(
                    process_recording,
                    recording,
                    index,
                    total_count,
                    email,
                    storage_service,
                    worker_id
                )
                future_to_recording[future] = (recording, index)

            # Track completion
            completed = 0
            failed = 0

            # Process completed tasks as they finish
            for future in as_completed(future_to_recording):
                recording, index = future_to_recording[future]
                try:
                    success = future.result()
                    if success:
                        completed += 1
                    else:
                        failed += 1
                except Exception as e:
                    print(f"{Color.RED}### Recording processing exception: {str(e)}{Color.END}")
                    failed += 1

        # Summary for this user
        print(f"\n{Color.BOLD}Summary for {userInfo}:{Color.END}")
        print(f"  ✓ Completed: {completed}")
        print(f"  ✗ Failed: {failed}")
        print(f"  Total: {total_count}")

    # Save the current run time for next run (if auto date range is enabled)
    if AUTO_DATE_RANGE:
        print()  # Empty line for spacing
        save_last_run_time()


if __name__ == "__main__":
    # tell Python to shutdown gracefully when SIGINT is received
    signal.signal(signal.SIGINT, handle_graceful_shutdown)

    main()