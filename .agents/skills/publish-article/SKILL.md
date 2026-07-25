---
name: publish-article
description: Create, revise, format, and publish sourced long-form static HTML articles. Use when Codex is asked to write an article or essay, turn transcripts or research into a narrative article, generate or place a banner visual, match Klokast-style daylight static-page formatting, publish under a versioned static URL such as /article-slug/, or upload article output to Nextcloud/WebDAV/static-site storage.
---

# Publish Article

## Core Workflow

1. Clarify the target only when required: topic, audience, URL slug, hosting target, and whether the article is new or a revision of a live version.
2. Gather sources before writing. Browse when the user requests current facts, specific URLs/transcripts, commentary, citations, or recommendations. Prefer primary sources for technical and policy claims.
3. Separate facts, inferences, and judgments in the prose. Do not hide behind "both sides have stories" framing when the user wants truth-testing.
4. Draft as one continuous long-form article with a beginning, middle, and end. Use headings sparingly. Avoid web-style cards, quadrant layouts, pull-quote boxes, and snippet blocks unless the user asks for them.
5. Create a static HTML page. Use `assets/article-template.html` as the base when no stronger local pattern exists. For Klokast-style pages, read `references/klokast-static-html.md`.
6. Add visuals when requested or when the article needs a first-viewport signal. Use a generated or sourced raster banner, then place it near the top as a full-width image.
7. Put sources where the user asks. If unspecified, put a concise source list at the end for long-form articles.
8. Publish only after local validation. For Nextcloud/WebDAV static publishing, read `references/nextcloud-static-publishing.md` and use `scripts/publish_webdav_static.py` when appropriate.
9. Verify the public URL, key text, and image assets after publishing. Clean temporary files. Report the live URL and any verification gaps.

## Writing Rules

- Favor coherent essay flow over briefing format.
- Make the argument legible to a skeptical reader: summarize the object-level event, then analyze it.
- Use emotional and cultural context only where it clarifies why people argued the way they did. Do not let mood replace logic.
- Keep claims sourceable. Say when a conclusion is an inference from sources rather than stated directly.
- Avoid invented direct quotes. Paraphrase unless a short exact quote is necessary.
- Avoid decorative final disclaimers such as "this page paraphrases sources" unless required.
- When revising a live article, preserve prior accepted improvements unless the user explicitly reverses them.

For more detailed editorial style, read `references/article-editorial.md`.

## Formatting Rules

- Use a static HTML file unless the user requests a framework.
- Prefer readable article typography: restrained serif body, strong title, generous line-height, narrow reading measure.
- Avoid dark mode for "daylight mirror" Klokast pages.
- Keep visual features integrated into the article. Tables and diagrams are acceptable when they carry factual structure; argument callouts should usually be prose.
- Use stable responsive dimensions for hero art and diagrams.
- Do not put sources in a top box unless the user asks. A short header meta line may link the primary artifact, for example: `Published May 27, 2026. Podcast Video. Full transcript.`

## Publishing Rules

- Never store secrets in the skill or committed files. Use user-provided credentials, environment variables, or platform private state.
- If working inside a repo with AGENTS.md or platform instructions, obey them before any hosting operation. Platform state-changing commands may need to run on a controller, not the local host.
- Stage generated output in a temporary ignored directory such as `.run/generated/<slug>/`.
- For versioned publishing, create a new remote folder rather than overwriting earlier accepted pages unless the user asks for an in-place edit.
- After WebDAV PUT, GET the remote files back and compare byte counts.
- Poll the public static URL until it returns `200` and contains expected text. Also verify the banner image returns `200` and has the expected byte count or image type.
