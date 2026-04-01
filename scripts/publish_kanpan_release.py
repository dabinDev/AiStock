#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
from pathlib import Path
from urllib import error, parse, request


API_VERSION = "2022-11-28"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish a build artifact to GitHub Releases.")
    parser.add_argument("--asset", required=True, help="Path to the artifact file to upload.")
    parser.add_argument(
        "--asset-name",
        required=True,
        help="Name to use for the uploaded release asset.",
    )
    return parser.parse_args()


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def github_token() -> str:
    return (
        os.environ.get("GH_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
        or require_env("GH_TOKEN")
    )


def api_request(
    method: str,
    url: str,
    token: str,
    *,
    payload: dict[str, object] | None = None,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, object | None]:
    request_headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "AstockKanpanReleasePublisher/1.0",
    }
    if headers:
        request_headers.update(headers)

    body = data
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"

    req = request.Request(url, data=body, headers=request_headers, method=method)
    try:
        with request.urlopen(req) as response:
            raw = response.read()
            if not raw:
                return response.status, None
            return response.status, json.loads(raw.decode("utf-8"))
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API request failed: {method} {url} -> {exc.code} {raw}") from exc


def sanitize_ref_name(ref_name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", ref_name.strip())
    safe = safe.strip("-")
    return safe or "unknown"


def release_metadata() -> dict[str, object]:
    repository = require_env("GITHUB_REPOSITORY")
    ref_name = require_env("GITHUB_REF_NAME")
    ref_type = require_env("GITHUB_REF_TYPE")
    sha = require_env("GITHUB_SHA")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com")

    safe_ref = sanitize_ref_name(ref_name)
    short_sha = sha[:7]
    run_url = f"{server_url}/{repository}/actions/runs/{run_id}" if run_id else ""

    if ref_type == "tag":
        tag_name = ref_name
        release_name = f"Astock Kanpan {ref_name}"
        prerelease = False
        target_commitish = None
        channel = "tag"
    else:
        tag_name = f"kanpan-{safe_ref}-{short_sha}"
        release_name = f"Astock Kanpan {safe_ref} {short_sha}"
        prerelease = True
        target_commitish = sha
        channel = "snapshot"

    notes_lines = [
        "Automatic build output for Astock Kanpan.",
        "",
        f"- Channel: {channel}",
        f"- Ref: `{ref_type}:{ref_name}`",
        f"- Commit: `{sha}`",
        f"- Run attempt: `{run_attempt}`",
    ]
    if run_url:
        notes_lines.append(f"- Workflow run: {run_url}")

    return {
        "repository": repository,
        "tag_name": tag_name,
        "release_name": release_name,
        "prerelease": prerelease,
        "target_commitish": target_commitish,
        "notes": "\n".join(notes_lines),
    }


def fetch_release_by_tag(token: str, repository: str, tag_name: str) -> dict[str, object] | None:
    url = f"https://api.github.com/repos/{repository}/releases/tags/{parse.quote(tag_name, safe='')}"
    req = request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "AstockKanpanReleasePublisher/1.0",
        },
        method="GET",
    )
    try:
        with request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        if exc.code == 404:
            return None
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API request failed: GET {url} -> {exc.code} {raw}") from exc


def create_or_update_release(token: str, metadata: dict[str, object]) -> dict[str, object]:
    repository = metadata["repository"]
    tag_name = metadata["tag_name"]
    existing = fetch_release_by_tag(token, str(repository), str(tag_name))
    payload = {
        "name": metadata["release_name"],
        "body": metadata["notes"],
        "prerelease": metadata["prerelease"],
    }

    if existing:
        url = f"https://api.github.com/repos/{repository}/releases/{existing['id']}"
        _, updated = api_request("PATCH", url, token, payload=payload)
        assert isinstance(updated, dict)
        return updated

    payload["tag_name"] = tag_name
    if metadata["target_commitish"]:
        payload["target_commitish"] = metadata["target_commitish"]

    url = f"https://api.github.com/repos/{repository}/releases"
    try:
        _, created = api_request("POST", url, token, payload=payload)
        assert isinstance(created, dict)
        return created
    except RuntimeError:
        existing = fetch_release_by_tag(token, str(repository), str(tag_name))
        if not existing:
            raise
        url = f"https://api.github.com/repos/{repository}/releases/{existing['id']}"
        _, updated = api_request("PATCH", url, token, payload=payload)
        assert isinstance(updated, dict)
        return updated


def delete_existing_assets(token: str, release: dict[str, object], asset_name: str) -> None:
    for asset in release.get("assets", []):
        if not isinstance(asset, dict):
            continue
        if asset.get("name") != asset_name:
            continue
        asset_url = asset.get("url")
        if not asset_url:
            continue
        api_request("DELETE", str(asset_url), token)


def upload_asset(token: str, release: dict[str, object], asset_path: Path, asset_name: str) -> None:
    upload_url = str(release["upload_url"]).split("{", 1)[0]
    query = parse.urlencode({"name": asset_name})
    content_type = mimetypes.guess_type(asset_name)[0] or "application/octet-stream"
    data = asset_path.read_bytes()
    api_request(
        "POST",
        f"{upload_url}?{query}",
        token,
        data=data,
        headers={"Content-Type": content_type, "Accept": "application/vnd.github+json"},
    )


def main() -> int:
    args = parse_args()
    asset_path = Path(args.asset).resolve()
    if not asset_path.is_file():
        raise RuntimeError(f"Asset file not found: {asset_path}")

    token = github_token()
    metadata = release_metadata()
    release = create_or_update_release(token, metadata)
    delete_existing_assets(token, release, args.asset_name)
    upload_asset(token, release, asset_path, args.asset_name)
    print(
        f"Published {args.asset_name} to release {metadata['tag_name']} in {metadata['repository']}",
        file=sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
