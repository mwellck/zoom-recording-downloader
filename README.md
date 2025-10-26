## Sponsorships

# Recall.ai - API for meeting recording
If you're looking for a meeting recording API, consider checking out [Recall.ai](https://www.recall.ai/?utm_source=github&utm_medium=sponsorship&utm_campaign=zoom-recording-downloader), an API that records Zoom, Google Meet, Microsoft Teams, In-person meetings, and more.

---

# ⚡️ zoom-recording-downloader ⚡️ 

## ☁️ Now with Google Drive, Amazon S3 & DigitalOcean Spaces support ☁️

[![Python 3.11](https://img.shields.io/badge/python-3.11%20%2B-blue.svg)](https://www.python.org/) [![License](https://img.shields.io/badge/license-MIT-brown.svg)](https://raw.githubusercontent.com/ricardorodrigues-ca/zoom-recording-downloader/master/LICENSE)

**Zoom Recording Downloader** is a cross-platform Python app that utilizes Zoom's API (v2) to download and organize all cloud recordings from a Zoom Business account to local storage, Google Drive, Amazon S3, or DigitalOcean Spaces.

## Screenshot ##
![screenshot](screenshot.png)

## Installation ##

_Attention: You will need [Python 3.11](https://www.python.org/downloads/) or greater_

```sh
$ git clone https://github.com/ricardorodrigues-ca/zoom-recording-downloader
$ cd zoom-recording-downloader
$ pip3 install -r requirements.txt
```

## Usage ##

_Attention: You will need a [Zoom Developer account](https://marketplace.zoom.us/) in order to create a [Server-to-Server OAuth app](https://developers.zoom.us/docs/internal-apps) with the required credentials_

1. Create a [server-to-server OAuth app](https://marketplace.zoom.us/user/build), set up your app and collect your credentials (`Account ID`, `Client ID`, `Client Secret`). For questions on this, [reference the docs](https://developers.zoom.us/docs/internal-apps/create/) on creating a server-to-server app. Make sure you activate the app. Follow Zoom's [set up documentation](https://marketplace.zoom.us/docs/guides/build/server-to-server-oauth-app/) or [this video](https://www.youtube.com/watch?v=OkBE7CHVzho) for a more complete walk through.

2. Add the necessary scopes to your app. In your app's _Scopes_ tab, add the following scopes: 
    > `cloud_recording:read:list_user_recordings:admin`, `user:read:user:admin`, `user:read:list_users:admin`.

3. Copy **zoom-recording-downloader.conf.template** to a new file named **zoom-recording-downloader.conf** and fill in your Server-to-Server OAuth app credentials:
```
      {
	      "OAuth": {
		      "account_id": "<ACCOUNT_ID>",
		      "client_id": "<CLIENT_ID>",
		      "client_secret": "<CLIENT_SECRET>"
	      }
      }
```

4. You can optionally add other options to the configuration file:

- Specify the base **download_dir** under which the recordings will be downloaded (default is 'downloads')
- Specify the **completed_log** log file that will store the ID's of downloaded recordings (default is 'completed-downloads.log')

```
      {
              "Storage": {
                      "download_dir": "downloads",
                      "completed_log": "completed-downloads.log"
              }
      }
```

- Specify the **start_date** from which to start downloading meetings (default is Jan 1 of this year)
- Specify the **end_date** at which to stop downloading meetings (default is today)
- Dates are specified as YYYY-MM-DD

```
      {
              "Recordings": {
                      "start_date": "2024-01-01",
                      "end_date": "2024-12-31"
              }
      }
```

- If you don't specify the **start_date** you can specify the year, month, and day seperately
- Specify the day of the month to start as **start_day** (default is 1)
- Specify the month to start as **start_month** (default is 1)
- Specify the year to start as **start_year** (default is this year)

```
      {
              "Recordings": {
                      "start_year": "2024",
                      "start_month": "1",
                      "start_day": "1"
              }
      }
```

- Specify the timezone for the saved meeting times saved in the filenames (default is 'UTC')
- You can use any timezone supported by [ZoneInfo](https://docs.python.org/3/library/zoneinfo.html)
- Specify the time format for the saved meeting times in the filenames (default is '%Y.%m.%d - %I.%M %p UTC')
- You can use any of the [strftime format codes](https://docs.python.org/3/library/datetime.html#strftime-and-strptime-format-codes) supported by datetime

```
      {
              "Recordings": {
                      "timezone": "America/New_York",
                      "strftime": "%Y.%m.%d-%H.%M%z"
              }
      }
```

- Specify the format for the filenames of saved meetings (default is '{meeting_time} - {topic} - {rec_type} - {recording_id}.{file_extension}')
- Specify the format for the folder name (under the download folder) for saved meetings (default is '{topic} - {meeting_time}')

```
      {
              "Recordings": {
                      "filename": "{meeting_time}-{topic}-{rec_type}-{recording_id}.{file_extension}",
                      "folder": "{year}/{month}/{meeting_time}-{topic}"
              }
      }
```

For the previous formats you can use the following values
  - **{file_extension}** is the lowercase version of the file extension
  - **{meeting_time}** is the time in the format of **strftime** and **timezone**
  - **{day}** is the day from **meeting_time**
  - **{month}** is the month from **meeting_time**
  - **{year}** is the year from **meeting_time**
  - **{recording_id}** is the recording id from zoom
  - **{rec_type}** is the type of the recording
  - **{topic}** is the title of the zoom meeting

## Parallel Processing (Optional) ##

To speed up processing of multiple recordings, you can enable parallel processing:

```json
{
    "Processing": {
        "max_workers": 3
    }
}
```

**max_workers** determines how many recordings are processed simultaneously:
- `1` = Sequential processing (original behavior)
- `2-3` = Recommended for most users (2-3x faster)
- `4-5` = For fast internet connections
- `6-10` = Advanced users only

**Benefits:**
- ✅ Process multiple recordings at once
- ✅ Better bandwidth utilization
- ✅ 2-3x faster for large batches
- ✅ Upload progress bars for all cloud storage options

**Requirements:**
- Adequate disk space: `max_workers × largest_recording_size`
- Sufficient bandwidth: Faster internet = more workers
- Available RAM: ~1-2 GB per worker

See [PARALLEL_PROCESSING.md](PARALLEL_PROCESSING.md) for detailed tuning guide.

## Advanced Configuration Options ##

### Root Folder Timestamps

Control whether cloud storage folders include timestamps:

```json
{
    "GoogleDrive": {
        "root_folder_name": "zoom-recordings",
        "use_timestamp": false
    },
    "S3": {
        "root_folder_name": "zoom-recordings",
        "use_timestamp": false
    }
}
```

- **false** (default): All recordings in single folder (e.g., `zoom-recordings/`)
- **true**: Each run creates timestamped folder (e.g., `zoom-recordings-2024-10-26-143022/`)

**Recommendation:** Use `false` to avoid duplicates and save costs.

### Completed Downloads Tracking

Control whether to track already-downloaded recordings:

```json
{
    "Storage": {
        "use_completed_log": true,
        "completed_log": "completed-downloads.log"
    }
}
```

- **true** (default): Skip already-downloaded recordings (faster, no duplicates)
- **false**: Process all recordings every time (useful for one-time re-downloads)

**Recommendation:** Use `true` for regular downloads, `false` only for disaster recovery.

See [CONFIGURATION_OPTIONS.md](CONFIGURATION_OPTIONS.md) for detailed guide with use cases and cost analysis.

## Automatic Zoom Deletion (Optional) ##

⚠️ **Advanced Feature:** Automatically delete recordings from Zoom after successful backup.

```json
{
    "Zoom": {
        "delete_after_download": false
    }
}
```

- **false** (default): Keep recordings in Zoom (safe)
- **true**: Delete from Zoom after successful download/upload (frees storage)

**Important:**
- Requires additional Zoom API scope: `cloud_recording:delete:meeting_recording:admin`
- Only deletes after successful backup
- Deletion is permanent (cannot be undone)
- Useful for freeing Zoom storage or compliance requirements

See [ZOOM_DELETION.md](ZOOM_DELETION.md) for complete guide including safety features, setup instructions, and best practices.

## Inactive/Deactivated Users (Optional) ##

Include recordings from deactivated users:

```json
{
    "Zoom": {
        "include_inactive_users": false
    }
}
```

- **false** (default): Only process active users
- **true**: Include inactive/deactivated users

**Use cases:**
- Archive recordings from former employees
- Compliance/legal requirements
- Historical backup before account deletion
- Free storage by archiving and deleting inactive user recordings

See [INACTIVE_USERS.md](INACTIVE_USERS.md) for complete guide.

## Automatic Date Range / Incremental Sync (Optional) ##

Automatically fetch only new recordings since the last run:

```json
{
    "Recordings": {
        "auto_date_range": false
    }
}
```

- **false** (default): Use manual start_date/end_date
- **true**: Automatically calculate date range from last run

**Perfect for:**
- Automated/scheduled downloads (cron jobs)
- No need to update dates manually
- Efficient - only fetches new recordings
- Automatic catch-up after downtime

**How it works:**
- First run: Uses start_date or defaults to last 30 days
- Saves timestamp in `last-run.log`
- Future runs: Automatically fetches from last run time to today

See [AUTO_DATE_RANGE.md](AUTO_DATE_RANGE.md) for complete guide with cron examples.

## Google Drive Setup (Optional) ##

To enable Google Drive upload support:

1. Create a Google Cloud Project:
   - Go to [Google Cloud Console](https://console.cloud.google.com)
   - Create a new project or select an existing one
   - Enable the Google Drive API for your project ([Click to enable ↗](https://console.cloud.google.com/flows/enableapi?apiid=drive.googleapis.com))

2. Create OAuth 2.0 credentials:
	- In Cloud Console, go to "APIs & Services" > "Credentials"
	- Click "Create Credentials" > "OAuth client ID"
	- Choose "Desktop application" as the application type
	- Give it a name (e.g., "Zoom Recording Downloader")
	- Download the JSON file and save as `client_secrets.json` in the script directory

3. Configure OAuth consent screen:
	- Go to "OAuth consent screen"
	- Select "External" user type
	- Set application name to "Zoom Recording Downloader"
	- Add required scopes:
		- https://www.googleapis.com/auth/drive.file
		- https://www.googleapis.com/auth/drive.metadata
		- https://www.googleapis.com/auth/drive.appdata

4. Update your config:
	```json
	{
		"GoogleDrive": {
			"client_secrets_file": "client_secrets.json",
			"token_file": "token.json",
			"root_folder_name": "zoom-recording-downloader",
			"retry_delay": 5,
			"max_retries": 3,
			"failed_log": "failed-uploads.log"
        }
	}
	```

**Important:** Keep your OAuth credentials file secure and never commit it to version control.
Consider adding `client_secrets.json` to your .gitignore file.

Note: When you first run the script with Google Drive enabled, it will open your default browser for authentication. After authorizing the application, the token will be saved locally and reused for future runs.

## Amazon S3 Setup (Optional) ##

To enable Amazon S3 upload support:

1. Create an S3 Bucket (if you don't have one):
   - Go to [AWS S3 Console](https://s3.console.aws.amazon.com/)
   - Click "Create bucket"
   - Choose a unique bucket name
   - Select your preferred region
   - Configure other settings as needed

2. Set up AWS credentials (choose one method):

   **Option A: Configuration File**
   ```json
   {
       "S3": {
           "aws_access_key_id": "YOUR_ACCESS_KEY",
           "aws_secret_access_key": "YOUR_SECRET_KEY",
           "region_name": "us-east-1",
           "bucket_name": "your-bucket-name"
       }
   }
   ```

   **Option B: AWS Credentials File** (~/.aws/credentials)
   ```
   [default]
   aws_access_key_id = YOUR_ACCESS_KEY
   aws_secret_access_key = YOUR_SECRET_KEY
   ```

   **Option C: Environment Variables**
   ```bash
   export AWS_ACCESS_KEY_ID=YOUR_ACCESS_KEY
   export AWS_SECRET_ACCESS_KEY=YOUR_SECRET_KEY
   export AWS_DEFAULT_REGION=us-east-1
   ```

   **Option D: IAM Role** (for EC2 instances)
   - Attach an IAM role with S3 permissions to your EC2 instance
   - No credentials needed in config

3. Required IAM Permissions:
   ```json
   {
       "Version": "2012-10-17",
       "Statement": [
           {
               "Effect": "Allow",
               "Action": [
                   "s3:PutObject",
                   "s3:GetObject",
                   "s3:ListBucket",
                   "s3:HeadBucket"
               ],
               "Resource": [
                   "arn:aws:s3:::your-bucket-name",
                   "arn:aws:s3:::your-bucket-name/*"
               ]
           }
       ]
   }
   ```

4. Update your config:
	```json
	{
		"S3": {
			"aws_access_key_id": "YOUR_ACCESS_KEY",
			"aws_secret_access_key": "YOUR_SECRET_KEY",
			"region_name": "us-east-1",
			"bucket_name": "your-bucket-name",
			"root_folder_name": "zoom-recording-downloader",
			"storage_class": "STANDARD",
			"retry_delay": 5,
			"max_retries": 3,
			"failed_log": "failed-uploads.log"
		}
	}
	```

**Storage Classes:** You can specify different S3 storage classes to optimize costs:
- `STANDARD` - General purpose (default)
- `STANDARD_IA` - Infrequent access, lower storage cost
- `INTELLIGENT_TIERING` - Automatic cost optimization
- `GLACIER` - Archive storage with retrieval delay
- `DEEP_ARCHIVE` - Lowest cost, longest retrieval time

## DigitalOcean Spaces Setup (Optional) ##

To enable DigitalOcean Spaces upload support:

1. Create a Space:
   - Go to [DigitalOcean Spaces](https://cloud.digitalocean.com/spaces)
   - Click "Create a Space"
   - Choose a datacenter region (e.g., NYC3, SFO3, AMS3)
   - Choose a unique name for your Space
   - Set file listing to "Private" (recommended)

2. Generate API Keys:
   - Go to API → Spaces access keys
   - Click "Generate New Key"
   - Give it a name (e.g., "Zoom Recording Downloader")
   - Save both the Access Key and Secret Key

3. Update your config:
	```json
	{
		"S3": {
			"aws_access_key_id": "YOUR_SPACES_ACCESS_KEY",
			"aws_secret_access_key": "YOUR_SPACES_SECRET_KEY",
			"region_name": "us-east-1",
			"bucket_name": "your-space-name",
			"endpoint_url": "https://nyc3.digitaloceanspaces.com",
			"root_folder_name": "zoom-recording-downloader",
			"retry_delay": 5,
			"max_retries": 3,
			"failed_log": "failed-uploads.log"
		}
	}
	```

**Important Settings for DigitalOcean Spaces:**
- **endpoint_url**: Must be set to your region's endpoint (e.g., `https://nyc3.digitaloceanspaces.com`, `https://sfo3.digitaloceanspaces.com`, `https://ams3.digitaloceanspaces.com`)
- **region_name**: Can be left as `us-east-1` (required by boto3 but not used by Spaces)
- **bucket_name**: Your Space name (without the region prefix)

**Available Regions:**
- NYC3: `https://nyc3.digitaloceanspaces.com`
- SFO3: `https://sfo3.digitaloceanspaces.com`
- AMS3: `https://ams3.digitaloceanspaces.com`
- SGP1: `https://sgp1.digitaloceanspaces.com`
- FRA1: `https://fra1.digitaloceanspaces.com`

Note: Leave `endpoint_url` empty or omit it entirely when using AWS S3. The endpoint_url is only needed for S3-compatible services like DigitalOcean Spaces.

## Running the Script ##

```sh
$ python zoom-recording-downloader.py
```

When prompted, choose your preferred storage method:
1. **Local Storage** - Saves recordings to your local machine
2. **Google Drive** - Uploads recordings to your Google Drive account
3. **Amazon S3 / DigitalOcean Spaces** - Uploads recordings to S3 or Spaces

**Note:** For cloud storage options (Google Drive, S3, or Spaces), files are temporarily downloaded to local storage before being uploaded, then automatically deleted after successful upload.

## Cloud Storage Features ##

All cloud storage options include:
- ✅ Automatic retry logic for failed uploads
- ✅ **Real-time upload progress bars** with speed and time estimates
- ✅ Configurable retry attempts and delays
- ✅ Failed upload logging for troubleshooting
- ✅ Timestamped root folders to organize different download runs
- ✅ Automatic local file cleanup after successful upload
- ✅ Organized folder structure matching your configuration
- ✅ **Parallel processing support** for faster batch operations

## Troubleshooting ##

### Google Drive Issues
- If authentication fails, delete `token.json` and re-authenticate
- Ensure all required scopes are added to your OAuth consent screen
- Check that the Google Drive API is enabled in your project

### S3/Spaces Issues
- Verify your credentials are correct
- Ensure your bucket/space name is correct (without region prefix for Spaces)
- Check that your IAM permissions include all required actions
- For Spaces, verify the endpoint_url matches your region
- Review `failed-uploads.log` for detailed error messages

### General Issues
- Check that Python 3.11+ is installed
- Verify all dependencies are installed: `pip3 install -r requirements.txt`
- Ensure your Zoom OAuth app is activated and has the required scopes
- Check the `completed-downloads.log` to see which recordings were processed

## Security Best Practices ##

- Never commit credentials to version control
- Add `client_secrets.json`, `token.json`, and your config file to `.gitignore`
- Use IAM roles when running on AWS infrastructure
- Rotate API keys and access tokens regularly
- Use separate credentials with minimal required permissions
- Enable encryption on your S3 buckets or Spaces
- Review access logs periodically

## License ##

This project is licensed under the MIT License. See the LICENSE file for details.