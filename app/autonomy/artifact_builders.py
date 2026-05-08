from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from .artifact_models import ArtifactManifest, ArtifactTaskRequest
from .artifact_registry import ArtifactRegistry
from .deliverable_exports import (
    infer_columns,
    write_csv,
    write_json,
    write_text,
    write_xlsx,
)
from .sandbox_policy import assert_safe_name, resolve_under, slugify


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _validate_output(output_dir: Path, created_files: list[Path]) -> dict:
    existing = [p for p in created_files if p.exists() and p.is_file()]
    total_bytes = sum(p.stat().st_size for p in existing)
    return {
        "output_dir_exists": output_dir.exists(),
        "created_files_count": len(existing),
        "total_bytes": total_bytes,
        "valid": output_dir.exists() and len(existing) > 0 and total_bytes > 0,
    }


def _finalize(
    registry: ArtifactRegistry,
    request: ArtifactTaskRequest,
    category: str,
    slug: str,
    output_dir: Path,
    created_files: list[Path],
    summary: dict,
) -> dict:
    task_id = f"artifact_{uuid.uuid4().hex[:12]}"
    records = registry.build_file_records(output_dir, created_files)
    validation = _validate_output(output_dir, created_files)

    manifest = ArtifactManifest(
        task_id=task_id,
        task_type=request.task_type.value,
        name=request.name,
        slug=slug,
        created_at=ArtifactManifest.now_iso(),
        output_dir=str(output_dir),
        files=records,
        summary=summary,
        validation=validation,
        status="created" if validation.get("valid") else "failed_validation",
    )
    manifest_path = registry.write_manifest(manifest, output_dir)

    return {
        "ok": validation.get("valid", False),
        "task_id": task_id,
        "task_type": request.task_type.value,
        "category": category,
        "name": request.name,
        "slug": slug,
        "output_dir": str(output_dir),
        "manifest_path": str(manifest_path),
        "files": [r.model_dump() for r in records],
        "summary": summary,
        "validation": validation,
        "status": manifest.status,
    }


def build_table_artifact(project_root: Path, request: ArtifactTaskRequest) -> dict:
    registry = ArtifactRegistry(project_root)
    assert_safe_name(request.name)

    slug = slugify(request.name)
    folder = f"{_ts()}_{slug}"
    output_dir = registry.make_output_dir("tables", folder)

    payload = request.payload or {}
    rows = payload.get("rows") or [
        {"day": 1, "title": "Channel intro", "format": "short", "status": "planned"},
        {"day": 2, "title": "Problem / solution", "format": "short", "status": "planned"},
        {"day": 3, "title": "Tool breakdown", "format": "short", "status": "planned"},
    ]

    preferred_order = ["day", "title", "format", "status", "platform", "owner", "notes"]
    columns = infer_columns(preferred_order, rows)

    created_files: list[Path] = []

    csv_path = write_csv(resolve_under(output_dir, "table.csv"), rows, columns)
    created_files.append(csv_path)

    json_path = write_json(
        resolve_under(output_dir, "table.json"),
        {"name": request.name, "description": request.description, "rows": rows, "columns": columns},
    )
    created_files.append(json_path)

    xlsx_path = write_xlsx(resolve_under(output_dir, "table.xlsx"), rows, columns, sheet_name="Table")
    if xlsx_path is not None:
        created_files.append(xlsx_path)

    schema_path = write_json(
        resolve_under(output_dir, "schema.json"),
        {
            "artifact_kind": "table_bundle_v2",
            "columns": columns,
            "row_count": len(rows),
            "recommended_primary_key": "day" if "day" in columns else None,
        },
    )
    created_files.append(schema_path)

    readme = write_text(
        resolve_under(output_dir, "README.md"),
        f"# {request.name}\n\n"
        f"Generated table deliverable bundle.\n\n"
        f"- Rows: {len(rows)}\n"
        f"- Columns: {', '.join(columns)}\n"
        f"- Files: table.csv, table.json, table.xlsx, schema.json\n",
    )
    created_files.append(readme)

    summary = {
        "rows": len(rows),
        "columns": columns,
        "artifact_kind": "table_bundle_v2",
        "deliverables": ["table.csv", "table.json", "table.xlsx (optional)", "schema.json", "README.md"],
    }
    return _finalize(registry, request, "tables", slug, output_dir, created_files, summary)


def build_site_artifact(project_root: Path, request: ArtifactTaskRequest) -> dict:
    registry = ArtifactRegistry(project_root)
    assert_safe_name(request.name)

    slug = slugify(request.name)
    folder = f"{_ts()}_{slug}"
    output_dir = registry.make_output_dir("sites", folder)

    payload = request.payload or {}
    title = payload.get("title") or request.name
    hero = payload.get("hero") or "Build faster with Jarvis V3"
    subtitle = payload.get("subtitle") or "A richer sandbox-generated website bundle."
    cta = payload.get("cta") or "Get Started"

    created_files: list[Path] = []

    index_html = write_text(
        resolve_under(output_dir, "index.html"),
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title}</title>
  <link rel="stylesheet" href="styles.css" />
</head>
<body>
  <header class="nav">
    <div class="brand">{title}</div>
    <nav>
      <a href="index.html">Home</a>
      <a href="about.html">About</a>
      <a href="contact.html">Contact</a>
    </nav>
  </header>
  <main class="container">
    <section class="hero">
      <div class="badge">Jarvis Deliverable Bundle</div>
      <h1>{hero}</h1>
      <p>{subtitle}</p>
      <button id="ctaButton">{cta}</button>
    </section>

    <section class="grid">
      <article class="card"><h2>Fast</h2><p>Structured deliverables, generated locally.</p></article>
      <article class="card"><h2>Safe</h2><p>Runs inside the artifact sandbox.</p></article>
      <article class="card"><h2>Practical</h2><p>Ready for packaging and project iteration.</p></article>
    </section>
  </main>
  <script src="app.js"></script>
</body>
</html>
""",
    )
    created_files.append(index_html)

    about_html = write_text(
        resolve_under(output_dir, "about.html"),
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>About - {title}</title>
  <link rel="stylesheet" href="styles.css" />
</head>
<body>
  <header class="nav">
    <div class="brand">{title}</div>
    <nav>
      <a href="index.html">Home</a>
      <a href="about.html">About</a>
      <a href="contact.html">Contact</a>
    </nav>
  </header>
  <main class="container">
    <section class="card wide">
      <h1>About</h1>
      <p>{title} is a structured website bundle generated by Jarvis.</p>
      <p>Use it as a starter for landing pages, launch bundles, and local demos.</p>
    </section>
  </main>
</body>
</html>
""",
    )
    created_files.append(about_html)

    contact_html = write_text(
        resolve_under(output_dir, "contact.html"),
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Contact - {title}</title>
  <link rel="stylesheet" href="styles.css" />
</head>
<body>
  <header class="nav">
    <div class="brand">{title}</div>
    <nav>
      <a href="index.html">Home</a>
      <a href="about.html">About</a>
      <a href="contact.html">Contact</a>
    </nav>
  </header>
  <main class="container">
    <section class="card wide">
      <h1>Contact</h1>
      <p>Email: hello@example.com</p>
      <p>Use this page as a starter for future lead capture or contact forms.</p>
    </section>
  </main>
</body>
</html>
""",
    )
    created_files.append(contact_html)

    styles_css = write_text(
        resolve_under(output_dir, "styles.css"),
        """* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: Arial, sans-serif;
  background: #0f172a;
  color: #e2e8f0;
}
.nav {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 18px 24px;
  border-bottom: 1px solid rgba(255,255,255,0.1);
  background: rgba(255,255,255,0.02);
}
.nav a {
  color: #e2e8f0;
  text-decoration: none;
  margin-left: 14px;
}
.brand {
  font-weight: 700;
}
.container {
  max-width: 1100px;
  margin: 0 auto;
  padding: 40px 20px;
}
.hero, .card {
  padding: 24px;
  border-radius: 18px;
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.08);
}
.hero { margin-bottom: 20px; }
.badge {
  display: inline-block;
  margin-bottom: 14px;
  padding: 8px 12px;
  border-radius: 999px;
  background: rgba(255,255,255,0.08);
  font-size: 12px;
}
h1 { font-size: 42px; margin: 0 0 14px 0; }
p { font-size: 18px; line-height: 1.6; }
button {
  margin-top: 18px;
  padding: 12px 18px;
  border: 0;
  border-radius: 12px;
  cursor: pointer;
  font-size: 16px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 16px;
}
.wide { max-width: 760px; }
""",
    )
    created_files.append(styles_css)

    app_js = write_text(
        resolve_under(output_dir, "app.js"),
        """document.getElementById("ctaButton")?.addEventListener("click", () => {
  alert("Jarvis generated this richer website bundle successfully.");
});
""",
    )
    created_files.append(app_js)

    brand_json = write_json(
        resolve_under(output_dir, "assets", "brand.json"),
        {
            "title": title,
            "hero": hero,
            "subtitle": subtitle,
            "cta": cta,
            "bundle_version": "site_bundle_v2",
        },
    )
    created_files.append(brand_json)

    readme = write_text(
        resolve_under(output_dir, "README.md"),
        f"# {request.name}\n\n"
        f"Open `index.html` to preview the site.\n\n"
        f"Included pages: Home, About, Contact.\n",
    )
    created_files.append(readme)

    summary = {
        "artifact_kind": "site_bundle_v2",
        "title": title,
        "hero": hero,
        "pages": ["index.html", "about.html", "contact.html"],
        "deliverables": ["styles.css", "app.js", "assets/brand.json", "README.md"],
    }
    return _finalize(registry, request, "sites", slug, output_dir, created_files, summary)


def build_game_artifact(project_root: Path, request: ArtifactTaskRequest) -> dict:
    registry = ArtifactRegistry(project_root)
    assert_safe_name(request.name)

    slug = slugify(request.name)
    folder = f"{_ts()}_{slug}"
    output_dir = registry.make_output_dir("games", folder)

    payload = request.payload or {}
    title = payload.get("title") or request.name

    created_files: list[Path] = []

    index_html = write_text(
        resolve_under(output_dir, "index.html"),
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title}</title>
  <link rel="stylesheet" href="styles.css" />
</head>
<body>
  <main class="wrap">
    <h1>{title}</h1>
    <p>Reach the target score before the timer ends.</p>
    <div class="panel">
      <button id="clickBtn">+1 Score</button>
      <button id="resetBtn">Reset</button>
    </div>
    <div class="stats">
      <div>Score: <span id="score">0</span></div>
      <div>Time: <span id="time">20</span></div>
      <div>Target: <span id="target">15</span></div>
    </div>
    <p id="message"></p>
  </main>
  <script src="game.js"></script>
</body>
</html>
""",
    )
    created_files.append(index_html)

    styles_css = write_text(
        resolve_under(output_dir, "styles.css"),
        """body {
  margin: 0;
  font-family: Arial, sans-serif;
  background: #111827;
  color: #f9fafb;
  min-height: 100vh;
  display: grid;
  place-items: center;
}
.wrap {
  width: min(680px, 92vw);
  text-align: center;
  padding: 24px;
  border-radius: 20px;
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.08);
}
.panel {
  display: flex;
  justify-content: center;
  gap: 12px;
  margin: 20px 0;
}
button {
  font-size: 18px;
  padding: 12px 18px;
  border-radius: 12px;
  border: 0;
  cursor: pointer;
}
.stats {
  display: flex;
  justify-content: center;
  gap: 18px;
  font-size: 20px;
  margin: 18px 0;
}
#message {
  min-height: 28px;
  font-size: 18px;
}
""",
    )
    created_files.append(styles_css)

    game_js = write_text(
        resolve_under(output_dir, "game.js"),
        """const scoreEl = document.getElementById("score");
const timeEl = document.getElementById("time");
const targetEl = document.getElementById("target");
const messageEl = document.getElementById("message");
const clickBtn = document.getElementById("clickBtn");
const resetBtn = document.getElementById("resetBtn");

const initialState = { score: 0, time: 20, target: 15, finished: false };
let state = { ...initialState };
let timer = null;

function render() {
  scoreEl.textContent = String(state.score);
  timeEl.textContent = String(state.time);
  targetEl.textContent = String(state.target);
}

function finish(message) {
  state.finished = true;
  clickBtn.disabled = true;
  messageEl.textContent = message;
}

function startTimer() {
  if (timer) clearInterval(timer);
  timer = setInterval(() => {
    if (state.finished) {
      clearInterval(timer);
      return;
    }
    state.time -= 1;
    render();
    if (state.time <= 0) {
      clearInterval(timer);
      if (state.score >= state.target) {
        finish("You win!");
      } else {
        finish("Time is over. Try again.");
      }
    }
  }, 1000);
}

clickBtn.addEventListener("click", () => {
  if (state.finished) return;
  state.score += 1;
  render();
  if (state.score >= state.target) {
    clearInterval(timer);
    finish("Target reached. You win!");
  }
});

resetBtn.addEventListener("click", () => {
  state = { ...initialState };
  clickBtn.disabled = false;
  messageEl.textContent = "";
  render();
  startTimer();
});

render();
startTimer();
""",
    )
    created_files.append(game_js)

    game_state = write_json(
        resolve_under(output_dir, "game_state.json"),
        {
            "title": title,
            "mode": "clicker_target",
            "initial_state": {"score": 0, "time": 20, "target": 15},
            "bundle_version": "game_bundle_v2",
        },
    )
    created_files.append(game_state)

    readme = write_text(
        resolve_under(output_dir, "README.md"),
        f"# {request.name}\n\nOpen `index.html` in a browser to play the game.\n",
    )
    created_files.append(readme)

    summary = {
        "artifact_kind": "game_bundle_v2",
        "game_type": "clicker_target",
        "launch_file": "index.html",
        "deliverables": ["styles.css", "game.js", "game_state.json", "README.md"],
    }
    return _finalize(registry, request, "games", slug, output_dir, created_files, summary)


def build_youtube_pack_artifact(project_root: Path, request: ArtifactTaskRequest) -> dict:
    registry = ArtifactRegistry(project_root)
    assert_safe_name(request.name)

    slug = slugify(request.name)
    folder = f"{_ts()}_{slug}"
    output_dir = registry.make_output_dir("youtube", folder)

    payload = request.payload or {}
    niche = payload.get("niche") or "AI tools"
    audience = payload.get("audience") or "beginners and curious professionals"
    tone = payload.get("tone") or "clear, engaging, practical"

    created_files: list[Path] = []

    ideas = [
        f"Top 10 mistakes beginners make in {niche}",
        f"How to start in {niche} with zero budget",
        f"3 tools that instantly improve your {niche} workflow",
        f"My honest {niche} setup for 2026",
        f"Beginner guide to consistency in {niche}",
        f"Fast wins in {niche} you can apply today",
        f"What nobody tells you about {niche}",
        f"How I would build from zero in {niche}",
        f"1-week challenge for improving in {niche}",
        f"Best habits for long-term growth in {niche}",
    ]

    calendar_rows = []
    for idx, idea in enumerate(ideas, start=1):
        calendar_rows.append(
            {
                "day": idx,
                "title": idea,
                "format": "short",
                "platform": "youtube",
                "status": "planned",
            }
        )

    calendar_columns = ["day", "title", "format", "platform", "status"]

    scripts_md = "# Starter scripts\n\n"
    for idx, idea in enumerate(ideas[:5], start=1):
        scripts_md += (
            f"## Script {idx}: {idea}\n\n"
            f"Hook: Open with a surprising fact or bold promise about {niche}.\n\n"
            f"Body:\n"
            f"- pain point\n"
            f"- explanation\n"
            f"- practical example\n"
            f"- CTA\n\n"
        )

    channel_json = {
        "channel_name": request.name,
        "niche": niche,
        "audience": audience,
        "tone": tone,
        "positioning": f"Practical and useful content about {niche}.",
        "bundle_version": "youtube_pack_v2",
    }

    created_files.append(write_json(resolve_under(output_dir, "channel_profile.json"), channel_json))
    created_files.append(write_text(resolve_under(output_dir, "content_ideas.md"), "# Content ideas\n\n" + "\n".join([f"- {x}" for x in ideas]) + "\n"))
    created_files.append(write_text(resolve_under(output_dir, "scripts.md"), scripts_md))
    created_files.append(write_text(
        resolve_under(output_dir, "metadata_templates.md"),
        (
            "# Metadata templates\n\n"
            "## Title formula\n"
            f"How to get results in {niche} faster\n\n"
            "## Description formula\n"
            f"This video helps {audience} improve in {niche} with a {tone} delivery.\n\n"
            "## CTA\n"
            "Subscribe for more structured, practical videos.\n"
        ),
    ))
    created_files.append(write_text(
        resolve_under(output_dir, "thumbnail_prompts.md"),
        (
            "# Thumbnail prompts\n\n"
            f"- Bold thumbnail for {niche} growth with strong contrast and simple text\n"
            f"- Clean YouTube thumbnail about {niche} for beginners\n"
            f"- Strong hook thumbnail for {niche} productivity improvement\n"
        ),
    ))
    created_files.append(write_csv(resolve_under(output_dir, "content_calendar.csv"), calendar_rows, calendar_columns))
    yt_xlsx = write_xlsx(resolve_under(output_dir, "content_calendar.xlsx"), calendar_rows, calendar_columns, sheet_name="Calendar")
    if yt_xlsx is not None:
        created_files.append(yt_xlsx)
    created_files.append(write_json(
        resolve_under(output_dir, "shorts_plan.json"),
        {
            "channel_name": request.name,
            "niche": niche,
            "shorts_count": 10,
            "items": calendar_rows,
        },
    ))
    created_files.append(write_text(
        resolve_under(output_dir, "README.md"),
        f"# {request.name}\n\nGenerated channel draft bundle for niche: {niche}\n",
    ))

    summary = {
        "artifact_kind": "youtube_pack_v2",
        "niche": niche,
        "audience": audience,
        "ideas_count": len(ideas),
        "deliverables": [
            "channel_profile.json",
            "content_ideas.md",
            "scripts.md",
            "metadata_templates.md",
            "thumbnail_prompts.md",
            "content_calendar.csv",
            "content_calendar.xlsx (optional)",
            "shorts_plan.json",
            "README.md",
        ],
    }
    return _finalize(registry, request, "youtube", slug, output_dir, created_files, summary)


def build_artifact(project_root: Path, request: ArtifactTaskRequest) -> dict:
    if request.task_type.value == "table":
        return build_table_artifact(project_root, request)
    if request.task_type.value == "site":
        return build_site_artifact(project_root, request)
    if request.task_type.value == "game":
        return build_game_artifact(project_root, request)
    if request.task_type.value == "youtube_pack":
        return build_youtube_pack_artifact(project_root, request)
    raise ValueError(f"Unsupported artifact task type: {request.task_type.value}")

