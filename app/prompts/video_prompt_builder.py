from __future__ import annotations


def build_i2v_prompt(
    subject: str,
    scene: str,
    action: str,
    duration_sec: int = 5,
    camera: str = "completely static camera, fixed medium close-up framing, no camera movement",
    style: str = "cinematic realism, realistic skin texture, natural proportions, ultra-detailed, 4K",
) -> str:
    return f"""adult person (20+), {subject}, {scene}, {style}, {camera}, smooth natural motion, {duration_sec} seconds duration;
0-1 sec: calm establishing moment, relaxed posture, natural breathing;
1-2 sec: subtle gaze shift, gentle blink, micro facial movement;
2-3 sec: {action}, movement starts naturally and slowly;
3-4 sec: action becomes more visible, stable identity, natural proportions;
4-5 sec: action settles smoothly, cinematic atmosphere remains stable;
negative prompt: low quality, distorted anatomy, stiff facial expression, unnatural blinking, jerky animation, camera movement, harsh lighting, oversaturated colors, explicit content, text, watermark, logo"""


def default_negative_prompt() -> str:
    return (
        "worst quality, low quality, normal quality, jpeg artifacts, signature, watermark, "
        "cartoon, 3d, doll, oil painting, anime, manga, comic book, cgi, render, stylized, "
        "illustration, plastic skin, shiny skin, "
        "asymmetric eyes, crooked smile, deformed fingers, extra fingers, too many fingers, "
        "mutated hands, bad proportions, unnatural pose, distorted background, dirty, blurry, "
        "motion artifacts, hair artifacts, lags, blurry movements, texture gap, text, font, logo"
    )