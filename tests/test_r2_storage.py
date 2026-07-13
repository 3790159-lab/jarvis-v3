# -*- coding: utf-8 -*-
"""Tests for the Cloudflare R2 media-storage uploader.

Money-safe: no real S3/R2 call is ever made — the boto3 client is a MagicMock
and every config is injected. R2 upload is free in our limits, but tests must
still never touch the network.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from app.services.r2_storage import (
    R2Config,
    R2ConfigError,
    R2Error,
    R2TransientError,
    ensure_tmp_lifecycle_rule,
    upload_file,
    upload_file_async,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _config() -> R2Config:
    return R2Config(
        account_id="acct",
        access_key_id="ak",
        secret_access_key="sk",
        bucket="jarvis-media",
        endpoint="https://acct.r2.cloudflarestorage.com",
        public_base_url="https://pub-abc.r2.dev",
    )


def _mp4(tmp_path: Path, name: str = "clip.mp4") -> Path:
    p = tmp_path / name
    p.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"x" * 512)
    return p


def _client() -> MagicMock:
    client = MagicMock()
    client.put_object = MagicMock(return_value={})
    return client


def _client_error(status: int, code: str = "Error") -> ClientError:
    return ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "PutObject",
    )


# ── happy path ────────────────────────────────────────────────────────────────


def test_upload_returns_public_url(tmp_path):
    file = _mp4(tmp_path)
    client = _client()

    url = upload_file(file, client=client, config=_config())

    assert url.startswith("https://pub-abc.r2.dev/media/")
    client.put_object.assert_called_once()


def test_auto_key_shape(tmp_path):
    file = _mp4(tmp_path)
    client = _client()

    upload_file(file, client=client, config=_config())

    key = client.put_object.call_args.kwargs["Key"]
    assert re.fullmatch(r"media/\d{4}-\d{2}/[0-9a-f]{32}\.mp4", key), key


def test_auto_key_preserves_extension(tmp_path):
    file = _mp4(tmp_path, name="photo.PNG")
    client = _client()

    upload_file(file, client=client, config=_config())

    key = client.put_object.call_args.kwargs["Key"]
    assert key.endswith(".png")


def test_explicit_key_is_used(tmp_path):
    file = _mp4(tmp_path)
    client = _client()

    url = upload_file(file, key="media/custom/x.mp4", client=client, config=_config())

    assert url == "https://pub-abc.r2.dev/media/custom/x.mp4"
    assert client.put_object.call_args.kwargs["Key"] == "media/custom/x.mp4"


def test_put_object_sets_bucket_and_content_type(tmp_path):
    file = _mp4(tmp_path)
    client = _client()

    upload_file(file, client=client, config=_config())

    kwargs = client.put_object.call_args.kwargs
    assert kwargs["Bucket"] == "jarvis-media"
    assert kwargs["ContentType"] == "video/mp4"
    assert "Body" in kwargs


def test_prefix_param_used_for_auto_key(tmp_path):
    file = _mp4(tmp_path)
    client = _client()

    upload_file(file, client=client, config=_config(), prefix="tmp")

    key = client.put_object.call_args.kwargs["Key"]
    assert re.fullmatch(r"tmp/\d{4}-\d{2}/[0-9a-f]{32}\.mp4", key), key


def test_default_prefix_is_media(tmp_path):
    file = _mp4(tmp_path)
    client = _client()

    upload_file(file, client=client, config=_config())

    key = client.put_object.call_args.kwargs["Key"]
    assert key.startswith("media/")


def test_public_base_trailing_slash_not_doubled(tmp_path):
    file = _mp4(tmp_path)
    cfg = _config()
    cfg.public_base_url = "https://pub-abc.r2.dev/"
    client = _client()

    url = upload_file(file, key="media/x.mp4", client=client, config=cfg)

    assert url == "https://pub-abc.r2.dev/media/x.mp4"


# ── errors ────────────────────────────────────────────────────────────────────


def test_missing_file_raises(tmp_path):
    with pytest.raises(R2Error, match="not found"):
        upload_file(tmp_path / "nope.mp4", client=_client(), config=_config())


def test_missing_config_raises(monkeypatch):
    for var in (
        "R2_ACCOUNT_ID",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET",
        "R2_ENDPOINT",
        "R2_PUBLIC_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(R2ConfigError):
        upload_file(Path("whatever.mp4"))


# ── retry ─────────────────────────────────────────────────────────────────────


def test_retries_transient_then_succeeds(tmp_path):
    file = _mp4(tmp_path)
    client = _client()
    client.put_object.side_effect = [
        EndpointConnectionError(endpoint_url="https://acct.r2.cloudflarestorage.com"),
        _client_error(503, "SlowDown"),
        {},
    ]
    sleeps: list[float] = []

    url = upload_file(
        file, client=client, config=_config(), max_attempts=3, sleep=sleeps.append
    )

    assert url.startswith("https://pub-abc.r2.dev/media/")
    assert client.put_object.call_count == 3
    assert len(sleeps) == 2  # slept before each retry


def test_gives_up_after_max_attempts(tmp_path):
    file = _mp4(tmp_path)
    client = _client()
    client.put_object.side_effect = EndpointConnectionError(
        endpoint_url="https://acct.r2.cloudflarestorage.com"
    )

    with pytest.raises(R2TransientError):
        upload_file(
            file, client=client, config=_config(), max_attempts=3, sleep=lambda _s: None
        )

    assert client.put_object.call_count == 3


def test_non_transient_error_raises_immediately(tmp_path):
    file = _mp4(tmp_path)
    client = _client()
    client.put_object.side_effect = _client_error(403, "AccessDenied")

    with pytest.raises(R2Error) as exc:
        upload_file(
            file, client=client, config=_config(), max_attempts=3, sleep=lambda _s: None
        )

    assert not isinstance(exc.value, R2TransientError)
    assert client.put_object.call_count == 1  # no retry on 4xx


# ── async wrapper ─────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_upload_file_async_returns_url(tmp_path):
    file = _mp4(tmp_path)
    client = _client()

    url = await upload_file_async(file, client=client, config=_config())

    assert url.startswith("https://pub-abc.r2.dev/media/")


# ── tmp/ lifecycle rule ───────────────────────────────────────────────────────


def test_ensure_tmp_lifecycle_rule_applies_expiration(tmp_path):
    client = _client()
    client.get_bucket_lifecycle_configuration = MagicMock(
        side_effect=ClientError(
            {"Error": {"Code": "NoSuchLifecycleConfiguration"}}, "GetBucketLifecycleConfiguration"
        )
    )
    client.put_bucket_lifecycle_configuration = MagicMock(return_value={})

    ok = ensure_tmp_lifecycle_rule(client=client, config=_config())

    assert ok is True
    kwargs = client.put_bucket_lifecycle_configuration.call_args.kwargs
    assert kwargs["Bucket"] == "jarvis-media"
    rules = kwargs["LifecycleConfiguration"]["Rules"]
    assert len(rules) == 1
    assert rules[0]["Filter"] == {"Prefix": "tmp/"}
    assert rules[0]["Expiration"] == {"Days": 7}
    assert rules[0]["Status"] == "Enabled"


def test_ensure_tmp_lifecycle_rule_is_idempotent(tmp_path):
    client = _client()
    client.get_bucket_lifecycle_configuration = MagicMock(
        return_value={
            "Rules": [
                {
                    "ID": "tmp-expire-7d",
                    "Status": "Enabled",
                    "Filter": {"Prefix": "tmp/"},
                    "Expiration": {"Days": 7},
                },
            ]
        }
    )
    client.put_bucket_lifecycle_configuration = MagicMock(return_value={})

    ensure_tmp_lifecycle_rule(client=client, config=_config())

    rules = client.put_bucket_lifecycle_configuration.call_args.kwargs[
        "LifecycleConfiguration"
    ]["Rules"]
    assert len(rules) == 1, "re-applying the rule must not create duplicates"


def test_ensure_tmp_lifecycle_rule_preserves_other_rules(tmp_path):
    client = _client()
    client.get_bucket_lifecycle_configuration = MagicMock(
        return_value={
            "Rules": [
                {"ID": "other-rule", "Status": "Enabled", "Filter": {"Prefix": "keep/"}},
            ]
        }
    )
    client.put_bucket_lifecycle_configuration = MagicMock(return_value={})

    ensure_tmp_lifecycle_rule(client=client, config=_config())

    rules = client.put_bucket_lifecycle_configuration.call_args.kwargs[
        "LifecycleConfiguration"
    ]["Rules"]
    ids = {r["ID"] for r in rules}
    assert "other-rule" in ids
    assert any(r["Filter"] == {"Prefix": "tmp/"} for r in rules)


def test_ensure_tmp_lifecycle_rule_fails_open_on_error(tmp_path):
    client = _client()
    client.get_bucket_lifecycle_configuration = MagicMock(return_value={"Rules": []})
    client.put_bucket_lifecycle_configuration = MagicMock(
        side_effect=_client_error(403, "AccessDenied")
    )

    ok = ensure_tmp_lifecycle_rule(client=client, config=_config())

    assert ok is False
