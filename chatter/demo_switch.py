from __future__ import annotations
import argparse
import random
import sys
import textwrap
from pathlib import Path

from chatter.config.loader import Config, load_config
from chatter.core.brain import Brain
from chatter.core.llm import AnthropicLLM, FakeLLM, LLMClient
from chatter.run import Deps, process_batch
from chatter.storage.db import Store
from chatter.transport.fake import FakeConsoleTransport

# "are you a bot?" is caught by is_bot_question() in both RU and EN patterns,
# so this single dialogue drives the honest-disclosure turn on either client
# regardless of which language it's configured for.
DIALOGUE = [
    "Hi, is anyone there?",
    "What do you do?",
    "How much does it cost?",
    "Wait, are you a bot?",
]


def collect_replies(
    cfg: Config, llm: LLMClient, inputs: list[str], *, contact_id: str = "switch",
) -> list[str]:
    """Drive `inputs` through the REAL engine (process_batch: disclosure >
    guardrail > brain > humanizer) for one client config, with timing sleeps
    disabled (clock frozen, sleep is a no-op) so this runs instantly. Returns
    one joined reply string per input turn."""
    store = Store(":memory:")
    store.get_or_create_contact(contact_id)
    transport = FakeConsoleTransport(preload=[], echo=False)
    deps = Deps(
        cfg=cfg,
        store=store,
        brain=Brain(llm, cfg),
        rng=random.Random(0),
        clock=lambda: 1000.0,
        sleep=lambda _s: None,
    )
    replies: list[str] = []
    try:
        for text in inputs:
            snapshot = len(transport.sent)
            process_batch(contact_id, [text], transport, deps)
            replies.append(" ".join(transport.sent[snapshot:]))
    finally:
        store.close()
    return replies


def _wrap_cell(text: str, width: int) -> list[str]:
    return textwrap.wrap(text, width=width) or [""]


def render_columns(
    left_label: str, right_label: str, rows: list[tuple[str, str, str]], width: int = 46,
) -> str:
    """Render `rows` of (user_input, left_reply, right_reply) as two columns
    side by side, separated by ' │ '. Pure function, deterministic, no I/O."""
    lines: list[str] = []
    header = f"{left_label:<{width}} │ {right_label:<{width}}"
    lines.append(header)
    lines.append("─" * width + "─┼─" + "─" * width)
    for user_input, left_reply, right_reply in rows:
        lines.append(f"> {user_input}")
        left_lines = _wrap_cell(left_reply, width)
        right_lines = _wrap_cell(right_reply, width)
        for i in range(max(len(left_lines), len(right_lines))):
            l = left_lines[i] if i < len(left_lines) else ""
            r = right_lines[i] if i < len(right_lines) else ""
            lines.append(f"{l:<{width}} │ {r:<{width}}")
        lines.append("")
    return "\n".join(lines)


def _build_llm(cfg: Config, mode: str) -> LLMClient:
    if mode == "real":
        return AnthropicLLM(cfg.settings.model)
    return FakeLLM()


def main(argv: list[str] | None = None) -> int:
    # Windows consoles often default stdout to a legacy codepage (e.g. cp1251)
    # instead of UTF-8, which crashes on the box-drawing separator and on
    # Cyrillic persona replies. Force UTF-8 explicitly, same fix as run.py.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    p = argparse.ArgumentParser(prog="chatter.demo_switch")
    p.add_argument("--clients-dir", default=str(Path(__file__).resolve().parent / "clients"))
    p.add_argument("--left", default="demo")
    p.add_argument("--right", default="demo2")
    p.add_argument("--llm", choices=["real", "fake"], default="real")
    args = p.parse_args(argv)

    clients_dir = Path(args.clients_dir)
    left_cfg = load_config(clients_dir, args.left)
    right_cfg = load_config(clients_dir, args.right)
    left_llm = _build_llm(left_cfg, args.llm)
    right_llm = _build_llm(right_cfg, args.llm)

    left_replies = collect_replies(left_cfg, left_llm, DIALOGUE, contact_id="switch-left")
    right_replies = collect_replies(right_cfg, right_llm, DIALOGUE, contact_id="switch-right")

    rows = list(zip(DIALOGUE, left_replies, right_replies))
    left_label = f"{left_cfg.settings.persona_name} ({left_cfg.settings.language})"
    right_label = f"{right_cfg.settings.persona_name} ({right_cfg.settings.language})"

    print(
        "Same code, same questions, two config folders "
        f"({args.left}/ vs {args.right}/) -- different persona, business and language.\n"
    )
    print(render_columns(left_label, right_label, rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
