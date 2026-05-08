"""Landing page generator — creates ready-to-use HTML landing pages."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

LANDING_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 text-gray-900">
    <!-- Header -->
    <header class="bg-white shadow-sm sticky top-0 z-50">
        <nav class="max-w-7xl mx-auto px-4 py-4 flex justify-between items-center">
            <div class="text-2xl font-bold text-blue-600">{brand}</div>
            <div class="space-x-6 hidden md:flex">
                <a href="#features" class="hover:text-blue-600 transition-colors">Features</a>
                <a href="#pricing" class="hover:text-blue-600 transition-colors">Pricing</a>
                <a href="#contact" class="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700 transition-colors">Contact</a>
            </div>
        </nav>
    </header>

    <!-- Hero -->
    <section class="max-w-7xl mx-auto px-4 py-20 text-center">
        <h1 class="text-5xl font-bold mb-6 leading-tight">{hero_title}</h1>
        <p class="text-xl text-gray-600 mb-8 max-w-2xl mx-auto">{hero_subtitle}</p>
        <button onclick="document.getElementById('contact').scrollIntoView()" class="bg-blue-600 text-white px-8 py-3 rounded-lg text-lg hover:bg-blue-700 transition-colors shadow-lg">
            {cta_text}
        </button>
    </section>

    <!-- Features -->
    <section id="features" class="bg-white py-20">
        <div class="max-w-7xl mx-auto px-4">
            <h2 class="text-3xl font-bold text-center mb-12">Features</h2>
            <div class="grid md:grid-cols-3 gap-8">
                {features_html}
            </div>
        </div>
    </section>

    <!-- Pricing -->
    <section id="pricing" class="py-20 bg-gray-50">
        <div class="max-w-7xl mx-auto px-4">
            <h2 class="text-3xl font-bold text-center mb-12">Pricing</h2>
            <div class="grid md:grid-cols-3 gap-8 max-w-5xl mx-auto">
                {pricing_html}
            </div>
        </div>
    </section>

    <!-- Contact -->
    <section id="contact" class="bg-blue-600 py-20 text-white text-center">
        <div class="max-w-2xl mx-auto px-4">
            <h2 class="text-3xl font-bold mb-4">Ready to get started?</h2>
            <p class="mb-8 opacity-90">Join thousands of satisfied customers today.</p>
            <a href="mailto:hello@example.com" class="bg-white text-blue-600 px-8 py-3 rounded-lg text-lg font-semibold hover:bg-gray-100 transition-colors">
                {cta_text}
            </a>
        </div>
    </section>

    <!-- Footer -->
    <footer class="bg-gray-900 text-white py-12">
        <div class="max-w-7xl mx-auto px-4 text-center">
            <p class="text-gray-400">&copy; 2026 {brand}. Built with Jarvis V3.</p>
        </div>
    </footer>
</body>
</html>"""


def generate_landing(topic: str, output_dir: str = "state/landings") -> str:
    """Generate landing page from topic description. Returns file path."""
    brand = _extract_brand(topic)

    html = LANDING_TEMPLATE.format(
        title=f"{topic} — Landing Page",
        brand=brand,
        hero_title=topic.title(),
        hero_subtitle=f"The best solution for {topic.lower()}. Fast, reliable, and easy to use.",
        cta_text="Get Started Free",
        features_html=_generate_features_html(),
        pricing_html=_generate_pricing_html(),
    )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_dir) / f"landing_{ts}.html"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")

    return str(output_path)


def _extract_brand(topic: str) -> str:
    words = topic.strip().split()
    if words:
        return words[0].title()
    return "Brand"


def _generate_features_html() -> str:
    features = [
        ("⚡", "Lightning Fast", "Built for speed — sub-second response times."),
        ("🛡", "Secure by Default", "Enterprise-grade security out of the box."),
        ("🎯", "99.9% Uptime", "Reliable infrastructure you can count on."),
    ]
    parts = []
    for icon, title, desc in features:
        parts.append(
            f'<div class="text-center p-6 rounded-xl border border-gray-100 hover:shadow-md transition-shadow">'
            f'<div class="text-5xl mb-4">{icon}</div>'
            f'<h3 class="text-xl font-bold mb-2">{title}</h3>'
            f'<p class="text-gray-600">{desc}</p>'
            f'</div>'
        )
    return "\n".join(parts)


def _generate_pricing_html() -> str:
    plans = [
        ("Free", "$0", "month", ["10 requests/day", "Basic support", "1 user"], False),
        ("Pro", "$29", "month", ["Unlimited requests", "Priority support", "Advanced features", "5 users"], True),
        ("Business", "$99", "month", ["Everything in Pro", "Team accounts", "Custom integrations", "Unlimited users"], False),
    ]
    parts = []
    for name, price, period, features, highlighted in plans:
        border = "border-blue-500 border-2" if highlighted else "border border-gray-200"
        badge = '<div class="absolute -top-3 left-1/2 -translate-x-1/2 bg-blue-600 text-white text-sm px-3 py-1 rounded-full">Popular</div>' if highlighted else ""
        features_list = "".join([f'<li class="py-2 flex items-center gap-2"><span class="text-green-500">✓</span> {f}</li>' for f in features])
        parts.append(
            f'<div class="relative bg-white {border} rounded-xl p-8 text-center">'
            f'{badge}'
            f'<h3 class="text-2xl font-bold mb-2">{name}</h3>'
            f'<div class="text-4xl font-bold mb-1">{price}</div>'
            f'<div class="text-gray-500 mb-6">/{period}</div>'
            f'<ul class="text-left mb-8 space-y-1">{features_list}</ul>'
            f'<button class="w-full bg-blue-600 text-white py-3 rounded-lg hover:bg-blue-700 transition-colors">Choose {name}</button>'
            f'</div>'
        )
    return "\n".join(parts)
