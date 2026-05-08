# Photo Quality Guide

## Model Routing

Image requests are automatically routed to the best model based on content:

| Content type | Model | Cost |
|---|---|---|
| People / portraits | `flux-1.1-pro-ultra` | ~$0.06/image |
| Landscapes / cities | `flux-1.1-pro-ultra` | ~$0.06/image |
| Everything else | `flux-1.1-pro` | ~$0.04/image |

### People keywords (any case form)
девушк, женщин, парн, мужчин, человек, ребёнок, портрет, лицо,
girl, woman, man, guy, person, people, child, portrait, face

### Landscape keywords (any case form)
пейзаж, природа, горы, море, океан, лес, город, улица, архитектура, здание,
landscape, nature, mountains, ocean, forest, city, street, architecture, building

## Prompt Enhancement

Prompts are automatically enhanced before sending to Replicate.

**People prompts** receive:
- `photorealistic portrait photography, professional photoshoot, natural lighting`
- `shallow depth of field, sharp focus on subject, real human, hyperrealistic skin texture`
- `shot on Canon EOS R5, 85mm f/1.4 lens, DSLR quality`
- `no illustration, no anime, no cgi, magazine cover quality, 8k resolution`

**All other prompts** receive:
- `photorealistic photography, professional camera, natural lighting, sharp focus, high detail`
- `shot on Canon EOS R5, 50mm lens, f/1.8, DSLR quality, hyperrealistic`
- `no illustration, real photo, 4k resolution, professional photo`

## Tips for Better Results

- Be specific: "девушка на закате в красном платье" beats "девушка"
- Mention lighting: "golden hour", "studio lighting", "overcast sky"
- Mention angle: "close-up", "wide shot", "aerial view"
- For portraits add: "bokeh background", "looking at camera"

## Approximate Costs

| Images | Model | Cost |
|---|---|---|
| 1 | flux-1.1-pro | ~$0.04 |
| 1 | flux-1.1-pro-ultra | ~$0.06 |
| 3 | flux-1.1-pro-ultra | ~$0.18 |
| 10 | flux-1.1-pro | ~$0.40 |
