# -*- coding: utf-8 -*-
"""Video face-swap pipeline using the owner's own Me-Persona (Fun Mode)."""
from __future__ import annotations

from app.services.block_m22_fun.me_persona import MePersonaData
from app.services.block_m_common.logging_setup import get_logger
from app.services.block_m_common.replicate_video_client import ReplicateVideoClient

logger = get_logger("video_face_swap")

_FACESWAP_MODEL = "lucataco/faceswap-a-video"
_COST_FACESWAP_PER_VIDEO = 0.05


class VideoFaceSwapPipeline:
    """Swap the owner's face into a video using their own trained Me-Persona.

    Security is hard-coded in the constructor call pattern: callers must pass
    the chat_id of the requester and the MePersonaData. swap_video() enforces
    that the persona belongs to the same chat_id, preventing cross-user swaps.

    Args:
        client: ReplicateVideoClient for API calls.
    """

    def __init__(self, client: ReplicateVideoClient) -> None:
        self._client = client

    async def swap_video(
        self,
        chat_id: int,
        video_url: str,
        me_persona: MePersonaData,
    ) -> dict:
        """Swap the requester's face into the given video.

        Args:
            chat_id: ID of the requesting chat — must match me_persona.chat_id.
            video_url: URL of the source video (or YouTube link, ytdl-resolved upstream).
            me_persona: Me-Persona data for the requester. Must be trained.

        Returns:
            {"output_url": str, "cost_usd": float}

        Raises:
            PermissionError: If chat_id does not match me_persona.chat_id.
            ValueError: If me_persona has no trained LoRA (no face photo available).
        """
        if me_persona.chat_id != chat_id:
            raise PermissionError(
                f"chat_id {chat_id} does not match me_persona owner {me_persona.chat_id}"
            )

        if not me_persona.seed_photos:
            raise ValueError(
                f"Me-Persona for chat {chat_id} has no seed photos. "
                "Run /me_seed to collect face photos first."
            )

        face_image_url = me_persona.seed_photos[0]
        logger.info(
            "VideoFaceSwap: chat=%s video=%.60s face=%.60s",
            chat_id, video_url, face_image_url,
        )

        payload = {
            "input": {
                "video": video_url,
                "face_image": face_image_url,
            }
        }
        output = await self._client._run_prediction(_FACESWAP_MODEL, payload)

        if isinstance(output, list) and output:
            output_url = output[0]
        elif isinstance(output, str):
            output_url = output
        else:
            output_url = str(output) if output else ""

        logger.info(
            "VideoFaceSwap: done chat=%s output=%.60s cost=$%.4f",
            chat_id, output_url, _COST_FACESWAP_PER_VIDEO,
        )
        return {"output_url": output_url, "cost_usd": _COST_FACESWAP_PER_VIDEO}
