#!/usr/bin/env python3
"""Zero-dependency S3/Cloudflare R2 Off-site Backup Synchronizer for Buyerly.

Implements AWS Signature Version 4 for S3-compatible endpoints (Cloudflare R2, AWS S3, Backblaze B2, MinIO)
using only the Python standard library.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _get_signature_key(key: str, date_stamp: str, region_name: str, service_name: str) -> bytes:
    k_date = _sign(("AWS4" + key).encode("utf-8"), date_stamp)
    k_region = _sign(k_date, region_name)
    k_service = _sign(k_region, service_name)
    return _sign(k_service, "aws4_request")


class S3Client:
    """Minimalistic S3/R2 client implementing SigV4."""

    def __init__(
        self,
        endpoint_url: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "auto",
        timeout_seconds: int = 60,
    ) -> None:
        self.endpoint_url = endpoint_url.rstrip("/")
        self.bucket = bucket.strip("/")
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region or "auto"
        self.timeout_seconds = timeout_seconds

        # Derive host and base URL
        parsed = urllib.parse.urlparse(self.endpoint_url)
        self.scheme = parsed.scheme or "https"
        self.host = parsed.netloc

    def _build_url(self, key: str = "", query: dict[str, str] | None = None) -> str:
        base = f"{self.scheme}://{self.host}/{self.bucket}"
        if key:
            base = f"{base}/{urllib.parse.quote(key.lstrip('/'), safe='/')}"
        if query:
            base = f"{base}?{urllib.parse.urlencode(query)}"
        return base

    def _request(
        self,
        method: str,
        key: str = "",
        query: dict[str, str] | None = None,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes, dict[str, str]]:
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")

        payload_hash = hashlib.sha256(body).hexdigest()
        canonical_uri = f"/{self.bucket}"
        if key:
            canonical_uri = f"{canonical_uri}/{urllib.parse.quote(key.lstrip('/'), safe='/')}"

        canonical_querystring = ""
        if query:
            sorted_q = sorted(query.items())
            canonical_querystring = urllib.parse.urlencode(sorted_q)

        req_headers = {
            "host": self.host,
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash,
        }
        if headers:
            for k, v in headers.items():
                req_headers[k.lower()] = v

        signed_headers_list = sorted(req_headers.keys())
        signed_headers = ";".join(signed_headers_list)
        canonical_headers = "".join(f"{k}:{req_headers[k]}\n" for k in signed_headers_list)

        canonical_request = (
            f"{method}\n"
            f"{canonical_uri}\n"
            f"{canonical_querystring}\n"
            f"{canonical_headers}\n"
            f"{signed_headers}\n"
            f"{payload_hash}"
        )

        service = "s3"
        credential_scope = f"{date_stamp}/{self.region}/{service}/aws4_request"
        string_to_sign = (
            f"AWS4-HMAC-SHA256\n"
            f"{amz_date}\n"
            f"{credential_scope}\n"
            f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
        )

        signing_key = _get_signature_key(self.secret_key, date_stamp, self.region, service)
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        authorization_header = (
            f"AWS4-HMAC-SHA256 "
            f"Credential={self.access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        )

        final_headers = dict(req_headers)
        final_headers["Authorization"] = authorization_header
        final_headers["User-Agent"] = "Buyerly-Backup-Sync/1.0"

        url = self._build_url(key=key, query=query)
        req = urllib.request.Request(url, data=body if method in ("PUT", "POST") else None, headers=final_headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as response:
                resp_status = response.getcode()
                resp_body = response.read()
                resp_headers = dict(response.headers)
                return resp_status, resp_body, resp_headers
        except urllib.error.HTTPError as e:
            err_body = e.read()
            return e.code, err_body, dict(e.headers)

    def upload_file(self, local_path: Path, remote_key: str | None = None) -> bool:
        if not local_path.is_file():
            print(f"[ERROR] Local backup file not found: {local_path}", file=sys.stderr)
            return False

        key = remote_key or local_path.name
        content = local_path.read_bytes()
        file_size_mb = len(content) / (1024 * 1024)
        print(f"[INFO] Uploading {local_path.name} ({file_size_mb:.2f} MB) to s3://{self.bucket}/{key}...")

        headers = {"content-type": "application/octet-stream"}
        status, resp_body, _ = self._request("PUT", key=key, body=content, headers=headers)
        if status in (200, 201, 204):
            print(f"[SUCCESS] Successfully uploaded to s3://{self.bucket}/{key}")
            return True
        else:
            print(f"[ERROR] S3 upload failed with HTTP {status}: {resp_body.decode('utf-8', errors='replace')}", file=sys.stderr)
            return False

    def download_file(self, remote_key: str, destination_path: Path) -> bool:
        print(f"[INFO] Downloading s3://{self.bucket}/{remote_key} to {destination_path}...")
        status, resp_body, _ = self._request("GET", key=remote_key)
        if status == 200:
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            destination_path.write_bytes(resp_body)
            print(f"[SUCCESS] Downloaded s3://{self.bucket}/{remote_key} ({len(resp_body)/(1024*1024):.2f} MB)")
            return True
        else:
            print(f"[ERROR] S3 download failed with HTTP {status}: {resp_body.decode('utf-8', errors='replace')}", file=sys.stderr)
            return False

    def list_objects(self, prefix: str = "") -> list[dict[str, str]]:
        query = {"list-type": "2"}
        if prefix:
            query["prefix"] = prefix

        status, resp_body, _ = self._request("GET", query=query)
        if status != 200:
            print(f"[ERROR] S3 list failed with HTTP {status}: {resp_body.decode('utf-8', errors='replace')}", file=sys.stderr)
            return []

        objects: list[dict[str, str]] = []
        try:
            root = ET.fromstring(resp_body)
            # S3 XML namespace
            ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
            contents = root.findall("s3:Contents", ns) or root.findall("Contents")
            for item in contents:
                key_el = item.find("s3:Key", ns) if item.find("s3:Key", ns) is not None else item.find("Key")
                last_mod_el = item.find("s3:LastModified", ns) if item.find("s3:LastModified", ns) is not None else item.find("LastModified")
                size_el = item.find("s3:Size", ns) if item.find("s3:Size", ns) is not None else item.find("Size")
                if key_el is not None and key_el.text:
                    objects.append({
                        "key": key_el.text,
                        "last_modified": last_mod_el.text if last_mod_el is not None and last_mod_el.text else "",
                        "size": size_el.text if size_el is not None and size_el.text else "0",
                    })
        except Exception as exc:
            print(f"[ERROR] Failed to parse S3 XML response: {exc}", file=sys.stderr)

        # Sort newest first
        objects.sort(key=lambda x: x["last_modified"] or x["key"], reverse=True)
        return objects

    def delete_object(self, remote_key: str) -> bool:
        print(f"[INFO] Deleting remote object s3://{self.bucket}/{remote_key}...")
        status, resp_body, _ = self._request("DELETE", key=remote_key)
        if status in (200, 204):
            print(f"[SUCCESS] Deleted s3://{self.bucket}/{remote_key}")
            return True
        else:
            print(f"[ERROR] S3 delete failed with HTTP {status}: {resp_body.decode('utf-8', errors='replace')}", file=sys.stderr)
            return False

    def prune_old_backups(self, retention_days: int = 60, min_keep_count: int = 7) -> int:
        """Prune backups older than retention_days, preserving at least min_keep_count backups."""
        objects = self.list_objects(prefix="buyerly_postgres_")
        if not objects:
            print("[INFO] No remote backups found to prune.")
            return 0

        total_count = len(objects)
        if total_count <= min_keep_count:
            print(f"[INFO] Total remote backups ({total_count}) <= safety threshold ({min_keep_count}). Skipping prune.")
            return 0

        now = datetime.now(timezone.utc)
        deleted = 0

        # We keep the newest min_keep_count backups unconditionally
        candidates = objects[min_keep_count:]
        for item in candidates:
            last_mod_str = item.get("last_modified")
            if not last_mod_str:
                continue
            try:
                # Parse ISO-8601 UTC timestamp: 2026-08-29T05:55:00.000Z
                cleaned_ts = last_mod_str.replace("Z", "+00:00")
                mod_time = datetime.fromisoformat(cleaned_ts)
                age_days = (now - mod_time).total_seconds() / 86400
                if age_days > retention_days:
                    print(f"[INFO] Backup {item['key']} is {age_days:.1f} days old (retention: {retention_days}d). Pruning...")
                    if self.delete_object(item["key"]):
                        deleted += 1
            except Exception as e:
                print(f"[WARNING] Could not parse date for {item['key']}: {e}", file=sys.stderr)

        print(f"[INFO] Pruning complete: deleted {deleted} expired backup(s).")
        return deleted


def main() -> int:
    parser = argparse.ArgumentParser(description="Buyerly Off-site S3 Backup Synchronizer")
    parser.add_argument("--upload", type=Path, help="Path to local backup file to upload")
    parser.add_argument("--download", type=str, help="Remote S3 key to download")
    parser.add_argument("--download-latest", action="store_true", help="Download the newest remote backup")
    parser.add_argument("--dest", type=Path, help="Destination local path for download")
    parser.add_argument("--list", action="store_true", help="List remote backups")
    parser.add_argument("--prune", action="store_true", help="Prune old remote backups")
    parser.add_argument("--retention-days", type=int, default=60, help="Retention period in days (default: 60)")
    parser.add_argument("--min-keep", type=int, default=7, help="Minimum backups to preserve (default: 7)")
    args = parser.parse_args()

    endpoint = os.environ.get("S3_ENDPOINT_URL", "").strip()
    bucket = os.environ.get("S3_BUCKET", "").strip()
    access_key = os.environ.get("S3_ACCESS_KEY_ID", "").strip()
    secret_key = os.environ.get("S3_SECRET_ACCESS_KEY", "").strip()
    region = os.environ.get("S3_REGION", "auto").strip()

    if not (endpoint and bucket and access_key and secret_key):
        print(
            "[WARNING] S3 credentials not fully configured (S3_ENDPOINT_URL, S3_BUCKET, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY). "
            "Skipping off-site synchronization."
        )
        return 0

    client = S3Client(endpoint_url=endpoint, bucket=bucket, access_key=access_key, secret_key=secret_key, region=region)

    if args.upload:
        success = client.upload_file(args.upload)
        if success and args.prune:
            client.prune_old_backups(retention_days=args.retention_days, min_keep_count=args.min_keep)
        return 0 if success else 1

    if args.list:
        items = client.list_objects()
        print(f"=== Remote Backups in s3://{bucket} ({len(items)} items) ===")
        for item in items:
            size_mb = int(item["size"]) / (1024 * 1024)
            print(f"  {item['key']:<45} {size_mb:>8.2f} MB   {item['last_modified']}")
        return 0

    if args.download_latest:
        items = client.list_objects()
        if not items:
            print("[ERROR] No remote backups found to download.", file=sys.stderr)
            return 1
        latest_key = items[0]["key"]
        dest = args.dest or Path(latest_key)
        success = client.download_file(latest_key, dest)
        return 0 if success else 1

    if args.download:
        if not args.dest:
            print("[ERROR] --dest is required when using --download.", file=sys.stderr)
            return 1
        success = client.download_file(args.download, args.dest)
        return 0 if success else 1

    if args.prune:
        client.prune_old_backups(retention_days=args.retention_days, min_keep_count=args.min_keep)
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
