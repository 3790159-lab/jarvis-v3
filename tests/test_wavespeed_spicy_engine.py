from pathlib import Path

from app.services.block_m2_video.engines.engine_protocol import VideoRequest
from app.services.block_m2_video.engines.errors import (
    TransientVideoError, TerminalVideoError,
)


def test_video_request_has_resolution_and_negative_prompt_defaults():
    r = VideoRequest(
        persona_id="p", persona_name="n",
        input_image_path=Path("x.jpg"), prompt="move",
    )
    assert r.resolution == "720p"
    assert r.negative_prompt == ""
    assert r.seconds == 5


def test_error_bases_are_distinct_runtimeerrors():
    assert issubclass(TransientVideoError, RuntimeError)
    assert issubclass(TerminalVideoError, RuntimeError)
    assert not issubclass(TransientVideoError, TerminalVideoError)
