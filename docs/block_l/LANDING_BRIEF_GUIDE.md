# Landing Brief 2.0 Guide — Block L.4

## How to use /landing_brief

1. Send `/landing_brief` in Telegram
2. Answer 8 questions (one at a time)
3. Claude AI generates unique landing page content
4. Get a ready HTML file in `state/landings/`

## The 8 Questions

1. **Business name** — 1-3 words (e.g., "Cafe Lapin", "FitSpace", "DevCraft")
2. **Target audience** — age, interests (e.g., "families 25-45", "young professionals")
3. **Main product/service** — what you sell (e.g., "French cuisine", "fitness coaching")
4. **Key advantages** — 3 benefits, comma-separated (e.g., "Quality, Speed, Support")
5. **Call to action** — desired user action (e.g., "Book now", "Sign up", "Get quote")
6. **Color scheme** — тёплая / холодная / нейтральная / luxury
7. **Style** — современный / luxury / игривый / деловой / health
8. **Contacts** — email, phone, website (or "нет")

## Color Scheme → Template mapping

| Color Scheme | Template Style |
|---|---|
| тёплая / warm | Warm Restaurant (orange/brown) |
| холодная / cold | Modern Tech (blue) |
| нейтральная | Modern Tech (blue) |
| luxury / люкс | Luxury Service (black/gold) |

## Style → Template mapping

| Style | Template |
|---|---|
| современный / modern | Modern Tech |
| luxury / люкс | Luxury Service |
| игривый / playful | Playful Creative |
| деловой / business | Modern Tech |
| health / здоровье | Health Sport |

## Tips for great landings

- **Specific business name** — "Cafe Lapin" is better than "My Cafe"
- **Clear target audience** — helps Claude write relevant copy
- **3 real advantages** — what makes you different
- **Strong CTA** — "Book a Table" beats "Click Here"
- **Real contacts** — improves the contact section

## Commands

- `/landing_brief` — start new brief
- `/cancel` — cancel current brief
- `/landing_demo` — see example briefs
- `/landing <topic>` — quick landing without brief (old version)

## What the landing includes

- Responsive HTML (mobile + desktop)
- Navigation with smooth scroll
- Hero section with headline + CTA button
- Features section (3-6 cards from Claude)
- Testimonials (3 realistic quotes)
- FAQ section (5 Q&A)
- About section
- CTA banner
- Contacts section
- Footer with links
- Tailwind CSS + JavaScript interactivity

## Selling guide

A Claude-generated landing can sell for:
- Simple businesses (cafe, studio): **$50-100**
- Medium complexity (SaaS, agency): **$100-200**
- With customization + hosting: **$300-500**

The HTML is self-contained — just double-click to open in a browser.
