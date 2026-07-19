import json
import os
import sys

import boto3

JOB_ID = os.environ["JOB_ID"]
BUCKET = os.environ.get("DDP_S3_BUCKET", "ddp")
WORK = "/workspace"

s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["DDP_S3_ENDPOINT"],
    aws_access_key_id=os.environ["DDP_S3_ACCESS_KEY"],
    aws_secret_access_key=os.environ["DDP_S3_SECRET_KEY"],
)


def upload(local_path, key):
    s3.upload_file(str(local_path), BUCKET, key)
    print(f"[ddp] uploaded {key}", flush=True)


def main():
    os.chdir(WORK)
    prefix = f"jobs/{JOB_ID}/output"
    count = 0

    if os.path.isdir("output"):
        for root, _, files in os.walk("output"):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, "output")
                upload(full, f"{prefix}/{rel}")
                count += 1

    if os.path.exists("manifest.json"):
        try:
            for fpath in json.load(open("manifest.json")).get("outputs", []):
                if os.path.isfile(fpath):
                    upload(fpath, f"{prefix}/{fpath}")
                    count += 1
        except (json.JSONDecodeError, OSError):
            pass

    print(f"[ddp] collected {count} output(s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
