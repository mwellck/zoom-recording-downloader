import os
import boto3
from datetime import datetime
from botocore.exceptions import ClientError, NoCredentialsError, PartialCredentialsError


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


class S3Client:
    def __init__(self, config):
        self.config = config
        self.s3_client = None
        self.bucket_name = None
        self.root_prefix = None

    def authenticate(self):
        """Initialize S3 client and validate credentials."""
        service_name = "DigitalOcean Spaces" if self.config.get('endpoint_url') else "AWS S3"
        print(f"{Color.DARK_CYAN}Initializing {service_name} connection...{Color.END}")

        try:
            # Get credentials from config
            aws_access_key = self.config.get('aws_access_key_id', '')
            aws_secret_key = self.config.get('aws_secret_access_key', '')
            region_name = self.config.get('region_name', 'us-east-1')
            endpoint_url = self.config.get('endpoint_url', None)  # For DigitalOcean Spaces
            self.bucket_name = self.config.get('bucket_name', '')

            if not self.bucket_name:
                print(f"{Color.RED}Error: Bucket name not configured.{Color.END}")
                return False

            # Initialize S3 client
            # If credentials are provided in config, use them
            # Otherwise, boto3 will look for credentials in environment variables or AWS config files
            client_config = {
                'region_name': region_name
            }

            if endpoint_url:
                client_config['endpoint_url'] = endpoint_url

            if aws_access_key and aws_secret_key:
                client_config['aws_access_key_id'] = aws_access_key
                client_config['aws_secret_access_key'] = aws_secret_key
                self.s3_client = boto3.client('s3', **client_config)
            else:
                # Use default credential chain (environment variables, AWS config, IAM role, etc.)
                self.s3_client = boto3.client('s3', **client_config)

            # Verify bucket exists and we have access
            try:
                self.s3_client.head_bucket(Bucket=self.bucket_name)
                service_display = "DigitalOcean Spaces" if endpoint_url else "S3"
                print(f"{Color.GREEN}Successfully connected to {service_display} bucket: {self.bucket_name}{Color.END}")
                return True
            except ClientError as e:
                error_code = e.response['Error']['Code']
                if error_code == '404':
                    print(f"{Color.RED}Error: Bucket '{self.bucket_name}' does not exist.{Color.END}")
                elif error_code == '403':
                    print(f"{Color.RED}Error: Access denied to bucket '{self.bucket_name}'.{Color.END}")
                else:
                    print(f"{Color.RED}Error accessing bucket: {e}{Color.END}")
                return False

        except NoCredentialsError:
            print(f"{Color.RED}Error: AWS credentials not found. Please configure credentials.{Color.END}")
            return False
        except PartialCredentialsError:
            print(f"{Color.RED}Error: Incomplete AWS credentials provided.{Color.END}")
            return False
        except Exception as e:
            print(f"{Color.RED}Failed to initialize S3 client: {e}{Color.END}")
            return False

    def initialize_root_folder(self):
        """Create root folder prefix with timestamp."""
        timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        root_folder_name = f"{self.config.get('root_folder_name', 'zoom-recording-downloader')}-{timestamp}"
        self.root_prefix = root_folder_name
        print(f"{Color.GREEN}S3 root prefix set to: {self.root_prefix}{Color.END}")
        return True

    def _build_s3_key(self, folder_name, filename):
        """Build the full S3 key (path) for a file."""
        # Normalize folder separators to forward slashes for S3
        folder_name = folder_name.replace(os.sep, '/')

        if self.root_prefix:
            return f"{self.root_prefix}/{folder_name}/{filename}"
        else:
            return f"{folder_name}/{filename}"

    def upload_file(self, local_path, folder_name, filename):
        """Upload file to S3 with retry logic."""
        try:
            s3_key = self._build_s3_key(folder_name, filename)

            max_retries = int(self.config.get('max_retries', 3))
            retry_delay = int(self.config.get('retry_delay', 5))
            failed_log = self.config.get('failed_log', 'failed-uploads.log')

            for attempt in range(max_retries):
                try:
                    print(f"    Attempt {attempt + 1} of {max_retries}...")

                    # Get file size for progress indication
                    file_size = os.path.getsize(local_path)

                    # Upload file
                    self.s3_client.upload_file(
                        local_path,
                        self.bucket_name,
                        s3_key,
                        ExtraArgs={'StorageClass': self.config.get('storage_class', 'STANDARD')}
                    )

                    print(f"    {Color.GREEN}Success! Uploaded to s3://{self.bucket_name}/{s3_key}{Color.END}")
                    return True

                except ClientError as e:
                    error_code = e.response['Error']['Code']
                    if attempt < max_retries - 1:
                        print(
                            f"    {Color.YELLOW}Upload failed ({error_code}). Retry after {retry_delay} seconds...{Color.END}")
                        import time
                        time.sleep(retry_delay)
                    else:
                        print(f"{Color.RED}Upload failed: {str(e)}{Color.END}")
                        with open(failed_log, 'a') as log:
                            log.write(f"{datetime.now()}: Failed to upload {filename} to S3 - {str(e)}\n")
                        return False

                except Exception as e:
                    if attempt < max_retries - 1:
                        print(f"    {Color.YELLOW}Retry after {retry_delay} seconds...{Color.END}")
                        import time
                        time.sleep(retry_delay)
                    else:
                        print(f"{Color.RED}Upload failed: {str(e)}{Color.END}")
                        with open(failed_log, 'a') as log:
                            log.write(f"{datetime.now()}: Failed to upload {filename} to S3 - {str(e)}\n")
                        return False

        except Exception as e:
            print(f"{Color.RED}Upload preparation failed: {str(e)}{Color.END}")
            return False

    def list_files(self, prefix=''):
        """List files in S3 bucket with given prefix."""
        try:
            full_prefix = f"{self.root_prefix}/{prefix}" if self.root_prefix else prefix
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=full_prefix
            )

            if 'Contents' in response:
                return [obj['Key'] for obj in response['Contents']]
            return []

        except Exception as e:
            print(f"{Color.RED}Failed to list files: {str(e)}{Color.END}")
            return []