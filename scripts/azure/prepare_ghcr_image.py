"""Resolve or publish the approved immutable GHCR image without leaking credentials."""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA = re.compile(r"^[0-9a-f]{40}$")
IMAGE_REPOSITORY = re.compile(r"^ghcr\.io/[a-z0-9_.-]+/[a-z0-9_.-]+$")
ACCEPT = (
    "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.v2+json"
)


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "")
    if not value:
        raise RuntimeError(f"{name} is required; image was not written")
    return value


def _run(
    runner: Callable[..., subprocess.CompletedProcess[str]],
    command: list[str],
    failure: str,
    **kwargs,
) -> subprocess.CompletedProcess[str]:
    result = runner(command, text=True, capture_output=True, **kwargs)
    if result.returncode != 0:
        raise RuntimeError(failure)
    return result


def prepare(
    environment: Mapping[str, str] = os.environ,
    open_url: Callable[..., object] = urlopen,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    actor = _required(environment, "GITHUB_ACTOR")
    github_token = _required(environment, "GH_TOKEN")
    source_sha = _required(environment, "SOURCE_SHA")
    image_repository = _required(environment, "IMAGE_REPOSITORY").lower()
    output_path = Path(_required(environment, "GITHUB_OUTPUT"))
    if not SHA.fullmatch(source_sha):
        raise RuntimeError("SOURCE_SHA is invalid; image was not written")
    if not IMAGE_REPOSITORY.fullmatch(image_repository):
        raise RuntimeError("IMAGE_REPOSITORY is invalid; image was not written")

    repository = image_repository.removeprefix("ghcr.io/")
    query = urlencode({"service": "ghcr.io", "scope": f"repository:{repository}:pull,push"})
    basic = base64.b64encode(f"{actor}:{github_token}".encode()).decode()
    token_request = Request(
        f"https://ghcr.io/token?{query}", headers={"Authorization": f"Basic {basic}"}
    )
    try:
        with open_url(token_request, timeout=30) as response:
            token_document = json.loads(response.read())
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "GHCR authenticated token request failed; image was not written"
        ) from error
    registry_token = token_document.get("token") or token_document.get("access_token")
    if not isinstance(registry_token, str) or not registry_token:
        raise RuntimeError("GHCR token response was invalid; image was not written")

    manifest_request = Request(
        f"https://ghcr.io/v2/{repository}/manifests/{source_sha}",
        headers={"Authorization": f"Bearer {registry_token}", "Accept": ACCEPT},
        method="HEAD",
    )
    digest = ""
    manifest_absent = False
    try:
        with open_url(manifest_request, timeout=30) as response:
            if getattr(response, "status", 200) != 200:
                raise RuntimeError("GHCR manifest lookup was inconclusive; image was not written")
            digest = response.headers.get("Docker-Content-Digest", "")
    except HTTPError as error:
        if error.code != 404:
            raise RuntimeError(
                "GHCR manifest authorization failed; image was not written"
            ) from error
        manifest_absent = True
    except (URLError, TimeoutError) as error:
        raise RuntimeError("GHCR manifest lookup failed; image was not written") from error

    if not manifest_absent:
        if not DIGEST.fullmatch(digest):
            raise RuntimeError("Existing GHCR manifest digest was invalid; image was not written")
        print("Existing source image found; build and push skipped.")
    else:
        tag = f"{image_repository}:{source_sha}"
        logged_in = False
        try:
            _run(
                runner,
                ["docker", "login", "ghcr.io", "--username", actor, "--password-stdin"],
                "GHCR login failed; image was not written",
                input=github_token + "\n",
            )
            logged_in = True
            _run(
                runner,
                ["docker", "build", "--pull", "--tag", tag, "."],
                "Container build failed; image was not written",
            )
            _run(
                runner,
                ["docker", "push", tag],
                "GHCR push failed or is unknown; do not retry automatically",
            )
            digest = _run(
                runner,
                [
                    "docker",
                    "buildx",
                    "imagetools",
                    "inspect",
                    tag,
                    "--format",
                    "{{.Manifest.Digest}}",
                ],
                "GHCR push may have succeeded but digest lookup failed; do not retry automatically",
            ).stdout.strip()
        finally:
            if logged_in:
                runner(["docker", "logout", "ghcr.io"], text=True, capture_output=True)
        if not DIGEST.fullmatch(digest):
            raise RuntimeError("Published GHCR manifest digest was invalid; publication is unknown")

    reference = f"{image_repository}@{digest}"
    with output_path.open("a", encoding="utf-8", newline="\n") as output:
        output.write(f"reference={reference}\n")
    return reference


if __name__ == "__main__":
    try:
        prepare()
    except RuntimeError as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from error
