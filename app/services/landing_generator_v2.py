# -*- coding: utf-8 -*-
"""Landing page generator v2 — 5 styles, Claude-generated content, mobile-responsive."""
from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

_ROOT = Path(__file__).resolve().parent.parent.parent
_LANDINGS_DIR = _ROOT / "state" / "landings"

_STYLE_CONFIGS = {
    "modern": {
        "bg": "#FFFFFF", "primary": "#2563EB", "secondary": "#64748B",
        "accent": "#F59E0B", "text": "#1E293B", "light_bg": "#F8FAFC",
        "font_heading": "Inter", "font_body": "Inter",
        "border_radius": "12px", "hero_gradient": "linear-gradient(135deg, #2563EB 0%, #1D4ED8 100%)",
    },
    "warm_restaurant": {
        "bg": "#FFFBF7", "primary": "#C2410C", "secondary": "#92400E",
        "accent": "#F59E0B", "text": "#1C1917", "light_bg": "#FFF7ED",
        "font_heading": "Georgia, serif", "font_body": "Lato, sans-serif",
        "border_radius": "8px", "hero_gradient": "linear-gradient(135deg, #C2410C 0%, #9A3412 100%)",
    },
    "luxury": {
        "bg": "#0A0A0A", "primary": "#B8860B", "secondary": "#2C2C2C",
        "accent": "#FFD700", "text": "#F5F5DC", "light_bg": "#111111",
        "font_heading": "Georgia, serif", "font_body": "Georgia, serif",
        "border_radius": "4px", "hero_gradient": "linear-gradient(135deg, #1A1A1A 0%, #0A0A0A 100%)",
    },
    "playful": {
        "bg": "#FAFAFA", "primary": "#FF6B6B", "secondary": "#4ECDC4",
        "accent": "#FFE66D", "text": "#2D3436", "light_bg": "#FFF5F5",
        "font_heading": "Arial Rounded MT Bold, sans-serif", "font_body": "Arial, sans-serif",
        "border_radius": "20px", "hero_gradient": "linear-gradient(135deg, #FF6B6B 0%, #FF8E53 100%)",
    },
    "health": {
        "bg": "#F0FDF4", "primary": "#16A34A", "secondary": "#15803D",
        "accent": "#F59E0B", "text": "#14532D", "light_bg": "#ECFDF5",
        "font_heading": "Arial, sans-serif", "font_body": "Arial, sans-serif",
        "border_radius": "12px", "hero_gradient": "linear-gradient(135deg, #16A34A 0%, #15803D 100%)",
    },
}

_COLOR_SCHEME_TO_STYLE = {
    "тёплая": "warm_restaurant",
    "warm": "warm_restaurant",
    "холодная": "modern",
    "cold": "modern",
    "нейтральная": "modern",
    "neutral": "modern",
    "luxury": "luxury",
    "люкс": "luxury",
}

_STYLE_NAME_MAP = {
    "современный": "modern",
    "modern": "modern",
    "luxury": "luxury",
    "люкс": "luxury",
    "игривый": "playful",
    "playful": "playful",
    "деловой": "modern",
    "business": "modern",
    "health": "health",
    "здоровье": "health",
}


def _pick_style(brief: Dict[str, Any]) -> str:
    """Pick a style key from brief's color_scheme and style fields."""
    style_raw = str(brief.get("style", "")).lower().strip()
    color_raw = str(brief.get("color_scheme", "")).lower().strip()
    # Try style first
    for k, v in _STYLE_NAME_MAP.items():
        if k in style_raw:
            return v
    # Try color scheme
    for k, v in _COLOR_SCHEME_TO_STYLE.items():
        if k in color_raw:
            return v
    return "modern"


def _e(text: str) -> str:
    """Escape HTML entities."""
    return html.escape(str(text), quote=True)


def _render_features(features: List[Dict], cfg: Dict) -> str:
    icons = {"zap": "&#9889;", "shield": "&#128737;", "star": "&#11088;",
             "check": "&#10003;", "heart": "&#10084;", "award": "&#127942;"}
    parts = []
    for f in features[:6]:
        icon = icons.get(f.get("icon_name", "star"), "&#11088;")
        parts.append(
            f'<div class="feature-card">'
            f'<div class="feature-icon">{icon}</div>'
            f'<h3>{_e(f.get("title", "Feature"))}</h3>'
            f'<p>{_e(f.get("description", ""))}</p>'
            f'</div>'
        )
    return "\n".join(parts)


def _render_testimonials(testimonials: List[Dict], cfg: Dict) -> str:
    parts = []
    for t in testimonials[:3]:
        parts.append(
            f'<div class="testimonial-card">'
            f'<div class="quote-mark">"</div>'
            f'<p class="quote-text">{_e(t.get("quote", ""))}</p>'
            f'<div class="author">'
            f'<strong>{_e(t.get("name", "Customer"))}</strong>'
            f'<span>{_e(t.get("role", ""))}</span>'
            f'</div>'
            f'</div>'
        )
    return "\n".join(parts)


def _render_faq(faq: List[Dict], cfg: Dict) -> str:
    parts = []
    for i, item in enumerate(faq[:5]):
        parts.append(
            f'<div class="faq-item">'
            f'<button class="faq-question" onclick="toggleFaq({i})">'
            f'{_e(item.get("question", ""))} <span>+</span>'
            f'</button>'
            f'<div class="faq-answer" id="faq-{i}" style="display:none">'
            f'<p>{_e(item.get("answer", ""))}</p>'
            f'</div>'
            f'</div>'
        )
    return "\n".join(parts)


def _render_footer_links(links: List[str]) -> str:
    return " &bull; ".join(f'<a href="#">{_e(lnk)}</a>' for lnk in links)


def generate_landing_v2(brief: Dict[str, Any], content: Dict[str, Any]) -> str:
    """Generate a complete HTML landing page from brief and content."""
    style_key = _pick_style(brief)
    cfg = _STYLE_CONFIGS.get(style_key, _STYLE_CONFIGS["modern"])

    biz_name = _e(brief.get("business_name", "Business"))
    contacts = _e(brief.get("contacts", ""))
    hero_headline = _e(content.get("hero_headline", f"Welcome to {biz_name}"))
    hero_sub = _e(content.get("hero_subheadline", ""))
    cta_text = _e(content.get("cta_text", "Get Started"))
    about_text = _e(content.get("about_text", ""))
    features_html = _render_features(content.get("features", []), cfg)
    testimonials_html = _render_testimonials(content.get("testimonials", []), cfg)
    faq_html = _render_faq(content.get("faq", []), cfg)
    footer_links = _render_footer_links(content.get("footer_links", ["Contact"]))

    is_dark = style_key == "luxury"
    nav_text_color = "#F5F5DC" if is_dark else cfg["text"]

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="{hero_sub}">
<meta property="og:title" content="{biz_name}">
<meta property="og:description" content="{hero_sub}">
<title>{biz_name}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<script src="https://cdn.tailwindcss.com"></script>
<style>
  :root {{
    --primary: {cfg["primary"]};
    --secondary: {cfg["secondary"]};
    --accent: {cfg["accent"]};
    --bg: {cfg["bg"]};
    --text: {cfg["text"]};
    --light-bg: {cfg["light_bg"]};
    --radius: {cfg["border_radius"]};
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: {cfg["font_body"]}; background: var(--bg); color: var(--text); line-height: 1.6; }}
  h1, h2, h3 {{ font-family: {cfg["font_heading"]}; }}
  /* Nav */
  nav {{ background: var(--bg); padding: 16px 24px; display: flex; justify-content: space-between;
         align-items: center; position: sticky; top: 0; z-index: 100;
         box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
  .nav-logo {{ font-size: 1.4rem; font-weight: 700; color: var(--primary); text-decoration: none; }}
  .nav-links a {{ margin-left: 20px; color: {nav_text_color}; text-decoration: none; font-size: 0.9rem; }}
  .nav-links a:hover {{ color: var(--primary); }}
  .nav-cta {{ background: var(--primary); color: #fff !important; padding: 8px 18px;
               border-radius: var(--radius); font-weight: 600; }}
  /* Hero */
  .hero {{ background: {cfg["hero_gradient"]}; color: #fff; padding: 80px 24px; text-align: center; }}
  .hero h1 {{ font-size: clamp(2rem, 5vw, 3.5rem); font-weight: 800; margin-bottom: 20px; line-height: 1.2; }}
  .hero p {{ font-size: 1.2rem; max-width: 600px; margin: 0 auto 32px; opacity: 0.9; }}
  .cta-btn {{ background: var(--accent); color: #1a1a1a; padding: 16px 36px; border-radius: var(--radius);
               font-size: 1.1rem; font-weight: 700; text-decoration: none; display: inline-block;
               transition: transform 0.2s, box-shadow 0.2s; }}
  .cta-btn:hover {{ transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,0.2); }}
  /* Sections */
  section {{ padding: 60px 24px; max-width: 1100px; margin: 0 auto; }}
  .section-title {{ font-size: 2rem; font-weight: 700; text-align: center; margin-bottom: 48px; color: var(--primary); }}
  /* Features */
  .features-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 24px; }}
  .feature-card {{ background: var(--light-bg); padding: 32px; border-radius: var(--radius);
                   transition: transform 0.2s, box-shadow 0.2s; border: 1px solid rgba(0,0,0,0.05); }}
  .feature-card:hover {{ transform: translateY(-4px); box-shadow: 0 8px 24px rgba(0,0,0,0.1); }}
  .feature-icon {{ font-size: 2.5rem; margin-bottom: 16px; }}
  .feature-card h3 {{ font-size: 1.2rem; font-weight: 600; margin-bottom: 12px; color: var(--primary); }}
  .feature-card p {{ color: var(--secondary); font-size: 0.95rem; }}
  /* Testimonials */
  .testimonials-section {{ background: var(--light-bg); padding: 60px 24px; }}
  .testimonials-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 24px;
                         max-width: 1100px; margin: 0 auto; }}
  .testimonial-card {{ background: var(--bg); padding: 28px; border-radius: var(--radius);
                        box-shadow: 0 4px 12px rgba(0,0,0,0.06); }}
  .quote-mark {{ font-size: 3rem; color: var(--primary); line-height: 1; margin-bottom: 8px; opacity: 0.3; }}
  .quote-text {{ font-style: italic; margin-bottom: 16px; color: var(--secondary); }}
  .author strong {{ display: block; font-weight: 600; }}
  .author span {{ font-size: 0.85rem; color: var(--secondary); }}
  /* About */
  .about-section {{ max-width: 800px; margin: 0 auto; padding: 60px 24px; text-align: center; }}
  .about-section p {{ color: var(--secondary); line-height: 1.8; margin-bottom: 16px; }}
  /* FAQ */
  .faq-section {{ max-width: 700px; margin: 0 auto; padding: 60px 24px; }}
  .faq-item {{ border-bottom: 1px solid rgba(0,0,0,0.08); padding: 16px 0; }}
  .faq-question {{ width: 100%; background: none; border: none; text-align: left; font-size: 1rem;
                    font-weight: 600; cursor: pointer; color: var(--text); display: flex;
                    justify-content: space-between; align-items: center; padding: 4px 0; }}
  .faq-question:hover {{ color: var(--primary); }}
  .faq-answer {{ padding: 12px 0 4px; color: var(--secondary); }}
  /* CTA Banner */
  .cta-banner {{ background: {cfg["hero_gradient"]}; padding: 60px 24px; text-align: center; color: #fff; }}
  .cta-banner h2 {{ font-size: 2rem; margin-bottom: 20px; }}
  /* Contacts */
  .contacts-section {{ background: var(--light-bg); padding: 40px 24px; text-align: center; }}
  .contacts-section a {{ color: var(--primary); text-decoration: none; }}
  /* Footer */
  footer {{ background: var(--secondary); color: #fff; padding: 24px; text-align: center; font-size: 0.85rem; }}
  footer a {{ color: rgba(255,255,255,0.7); text-decoration: none; margin: 0 8px; }}
  footer a:hover {{ color: #fff; }}
  /* Responsive */
  @media (max-width: 640px) {{
    nav .nav-links {{ display: none; }}
    .hero h1 {{ font-size: 1.8rem; }}
    .features-grid {{ grid-template-columns: 1fr; }}
    .testimonials-grid {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>

<!-- Navigation -->
<nav>
  <a href="#" class="nav-logo">{biz_name}</a>
  <div class="nav-links">
    <a href="#features">Features</a>
    <a href="#about">About</a>
    <a href="#faq">FAQ</a>
    <a href="#contacts" class="nav-cta">{cta_text}</a>
  </div>
</nav>

<!-- Hero -->
<section class="hero" id="home">
  <h1>{hero_headline}</h1>
  <p>{hero_sub}</p>
  <a href="#contacts" class="cta-btn">{cta_text}</a>
</section>

<!-- Features -->
<section id="features">
  <h2 class="section-title">Why Choose Us</h2>
  <div class="features-grid">
    {features_html}
  </div>
</section>

<!-- Testimonials -->
<div class="testimonials-section" id="reviews">
  <h2 class="section-title">What Our Customers Say</h2>
  <div class="testimonials-grid">
    {testimonials_html}
  </div>
</div>

<!-- About -->
<div class="about-section" id="about">
  <h2 class="section-title">About Us</h2>
  <p>{about_text}</p>
</div>

<!-- FAQ -->
<div class="faq-section" id="faq">
  <h2 class="section-title">Frequently Asked Questions</h2>
  {faq_html}
</div>

<!-- CTA Banner -->
<div class="cta-banner">
  <h2>Ready to get started?</h2>
  <p style="margin-bottom:28px; opacity:0.9">Join our happy customers today</p>
  <a href="#contacts" class="cta-btn">{cta_text}</a>
</div>

<!-- Contacts -->
<div class="contacts-section" id="contacts">
  <h2 class="section-title">Contact Us</h2>
  <p>{contacts if contacts and contacts != 'нет' else 'Contact us for more information'}</p>
</div>

<!-- Footer -->
<footer>
  <p style="margin-bottom:8px">&copy; {datetime.now().year} {biz_name}. All rights reserved.</p>
  <p>{footer_links}</p>
</footer>

<script>
function toggleFaq(idx) {{
  const el = document.getElementById('faq-' + idx);
  const btn = el.previousElementSibling;
  const span = btn.querySelector('span');
  if (el.style.display === 'none') {{
    el.style.display = 'block';
    span.textContent = '-';
  }} else {{
    el.style.display = 'none';
    span.textContent = '+';
  }}
}}
// Smooth scroll
document.querySelectorAll('a[href^="#"]').forEach(a => {{
  a.addEventListener('click', e => {{
    e.preventDefault();
    const target = document.querySelector(a.getAttribute('href'));
    if (target) target.scrollIntoView({{ behavior: 'smooth' }});
  }});
}});
</script>
</body>
</html>"""


def save_landing_v2(brief: Dict[str, Any], content: Dict[str, Any]) -> str:
    """Generate and save landing page. Returns file path."""
    _LANDINGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    biz = re.sub(r"[^\w\-]", "_", brief.get("business_name", "landing"))[:20]
    filename = f"landing_v2_{biz}_{ts}.html"
    path = _LANDINGS_DIR / filename
    html_content = generate_landing_v2(brief, content)
    path.write_text(html_content, encoding="utf-8")
    return str(path)
