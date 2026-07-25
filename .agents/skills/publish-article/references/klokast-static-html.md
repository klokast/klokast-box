# Klokast Static HTML Guidance

Use this reference when the user asks for a Klokast-style static article page or asks to match `klokast.ai`.

## Page Pattern

- Produce one `index.html` plus `assets/` for images.
- Use a daylight palette as the mirror of the dark Klokast front page: warm paper background, dark ink, muted brown/gray secondary text, restrained accent color.
- Use strong title typography and readable serif body text.
- Use a full-width banner near the top when a visual is requested.
- Keep article width around 760px for prose, with occasional wider figures up to about 1120px.
- Put sources at the end unless the user asks otherwise.
- Use query-string cache busters on image references for new versions, such as `assets/banner.png?v=7`.

## Avoid

- Do not create marketing hero copy when the task is an article.
- Do not use snippet cards, quadrant grids, or pull-quote boxes for core argument flow unless requested.
- Do not create nested cards.
- Do not use dark mode unless explicitly requested.
- Do not use one-note decorative palettes or gradient-orb backgrounds.

## Useful Header Pattern

```html
<p class="meta">
  Published May 27, 2026.
  <a href="PRIMARY_VIDEO_URL">Podcast Video</a>.
  <a href="PRIMARY_TRANSCRIPT_URL">Full transcript</a>.
  The relevant section begins at 00:57:36.
</p>
```

## Validation

- Confirm the page contains the requested title, primary links, and any exact phrases the user specified.
- Confirm images render with stable dimensions.
- Search for rejected phrasing from prior versions before publishing.
