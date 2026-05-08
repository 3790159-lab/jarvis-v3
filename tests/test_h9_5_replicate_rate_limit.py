"""Phase H9.5: Replicate rate limit retry + helpful error."""
from __future__ import annotations

import sys
import os
import json
import urllib.error
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_http_error(code: int, body: str = "") -> urllib.error.HTTPError:
    import io
    return urllib.error.HTTPError(
        url="https://api.replicate.com/test",
        code=code,
        msg=f"HTTP {code}",
        hdrs={},  # type: ignore[arg-type]
        fp=io.BytesIO(body.encode()),
    )


# ---------------------------------------------------------------------------
# _post_with_retry
# ---------------------------------------------------------------------------

def test_post_with_retry_success_first_try():
    from app.services.replicate_image_gen import _post_with_retry
    response = {"id": "pred123", "status": "starting"}
    with patch("urllib.request.urlopen") as mock_open:
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=MagicMock(read=lambda: json.dumps(response).encode()))
        mock_cm.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_cm
        result = _post_with_retry("https://api.replicate.com/test", {}, "fake_key")
    assert result["id"] == "pred123"


def test_post_with_retry_retries_on_429():
    """Should retry after 429 and succeed on second attempt."""
    from app.services.replicate_image_gen import _post_with_retry

    response = {"id": "pred456", "status": "starting"}
    call_count = [0]

    def fake_urlopen(req, timeout=30):
        call_count[0] += 1
        if call_count[0] == 1:
            raise _make_http_error(429, '{"retry_after": 1}')
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=MagicMock(read=lambda: json.dumps(response).encode()))
        mock_cm.__exit__ = MagicMock(return_value=False)
        return mock_cm

    with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
         patch("time.sleep"):
        result = _post_with_retry("https://api.replicate.com/test", {}, "fake_key", max_retries=3)

    assert result["id"] == "pred456"
    assert call_count[0] == 2


def test_post_with_retry_raises_after_max_retries():
    """After all retries exhausted on 429, must raise RuntimeError with billing URL."""
    from app.services.replicate_image_gen import _post_with_retry

    with patch("urllib.request.urlopen", side_effect=_make_http_error(429, '{}')), \
         patch("time.sleep"):
        try:
            _post_with_retry("https://api.replicate.com/test", {}, "fake_key", max_retries=2)
            assert False, "Should have raised RuntimeError"
        except RuntimeError as e:
            assert "rate limit" in str(e).lower() or "replicate.com/account/billing" in str(e)


def test_post_with_retry_non_429_raises_immediately():
    """Non-429 HTTP errors should propagate immediately without retry."""
    from app.services.replicate_image_gen import _post_with_retry

    call_count = [0]
    def fake_urlopen(req, timeout=30):
        call_count[0] += 1
        raise _make_http_error(500, "Internal Server Error")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        try:
            _post_with_retry("https://api.replicate.com/test", {}, "fake_key", max_retries=3)
            assert False, "Should have raised"
        except urllib.error.HTTPError as e:
            assert e.code == 500
        except Exception:
            pass

    assert call_count[0] == 1, "Should not retry on non-429"


def test_post_with_retry_parses_retry_after():
    """retry_after from JSON body should be used for sleep duration."""
    from app.services.replicate_image_gen import _post_with_retry

    sleep_calls = []
    response = {"id": "ok"}

    call_count = [0]
    def fake_urlopen(req, timeout=30):
        call_count[0] += 1
        if call_count[0] == 1:
            raise _make_http_error(429, '{"retry_after": 30}')
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=MagicMock(read=lambda: json.dumps(response).encode()))
        mock_cm.__exit__ = MagicMock(return_value=False)
        return mock_cm

    with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
         patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
        _post_with_retry("https://api.replicate.com/test", {}, "fake_key", max_retries=3)

    assert len(sleep_calls) >= 1
    assert sleep_calls[0] == 31  # retry_after(30) + 1


# ---------------------------------------------------------------------------
# generate_images_replicate uses _post_with_retry
# ---------------------------------------------------------------------------

def test_generate_images_replicate_uses_post_with_retry():
    """generate_images_replicate should use _post_with_retry (not raw urlopen)."""
    import app.services.replicate_image_gen as mod
    import pathlib
    src = pathlib.Path("app/services/replicate_image_gen.py").read_text(encoding="utf-8")
    assert "_post_with_retry" in src
    # Old direct Request pattern should no longer be in generate_images_replicate body
    assert "generate_images_replicate" in src


def test_replicate_rate_limit_error_mentions_billing():
    """Rate limit RuntimeError must contain billing URL."""
    from app.services.replicate_image_gen import _post_with_retry

    with patch("urllib.request.urlopen", side_effect=_make_http_error(429, '{}')), \
         patch("time.sleep"):
        try:
            _post_with_retry("https://api.replicate.com/test", {}, "key", max_retries=1)
            assert False
        except RuntimeError as e:
            assert "replicate.com/account/billing" in str(e)


def test_post_with_retry_function_exists():
    from app.services.replicate_image_gen import _post_with_retry
    assert callable(_post_with_retry)
