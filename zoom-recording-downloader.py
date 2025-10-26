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

        if file_type == "":
            recording_type = "incomplete"
        elif file_type != "TIMELINE":
            recording_type = download["recording_type"]
        else:
            recording_type = download["file_type"]

        # must append access token to download_url
        download_url = f"{download['download_url']}?access_token={ACCESS_TOKEN}"
        downloads.append((file_type, file_extension, download_url, recording_type, recording_id))

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

        delete_url = f"https://api.zoom.us/v2/recordings/{encoded_id}"

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
        for file_type, file_extension, download_url, recording_type, rec_id in downloads:
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
                    # Upload to cloud storage if enabled
                    upload_success = False

                    if GDRIVE_ENABLED and storage_service:
                        print(f"    > [{index + 1}/{total_count}] Uploading to Google Drive...")
                        upload_success = storage_service.upload_file(full_filename, folder_name, sanitized_filename,
                                                                     worker_id)
                    elif S3_ENABLED and storage_service:
                        print(f"    > [{index + 1}/{total_count}] Uploading to S3/Spaces...")
                        upload_success = storage_service.upload_file(full_filename, folder_name, sanitized_filename,
                                                                     worker_id)

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


def handle_graceful_shutdown(signal_received, frame):
    print(f"\n{Color.DARK_CYAN}SIGINT or CTRL-C detected. Exiting gracefully.{Color.END}")

    system.exit(0)


# ################################################################
# #                        MAIN                                  #
# ################################################################

def main():
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

    # Storage choice prompt
    print("\nChoose download method:")
    print("1. Local Storage")
    print("2. Google Drive")
    print("3. Amazon S3 / DigitalOcean Spaces")
    choice = input("Enter choice (1-3): ")

    global GDRIVE_ENABLED, S3_ENABLED

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