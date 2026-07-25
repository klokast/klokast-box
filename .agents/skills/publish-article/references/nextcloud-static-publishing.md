# Nextcloud Static Publishing

Use this reference when publishing article output to a Nextcloud-backed static site.

## Inputs

Require or discover:

- Local article directory containing `index.html` and assets.
- Remote WebDAV directory, usually `www/<slug>`.
- WebDAV base URL, username, and password. Use environment variables when available.
- Optional reverse-proxy headers such as `Host`, `X-Forwarded-Proto`, and `X-Forwarded-Host`.
- Public URL to verify, usually `https://www.example.com/<slug>/`.

Never put credentials in skill files, repo files, or final answers.

## Platform Locus

If the current repo has AGENTS.md or platform docs, read and obey them before publishing.

For Klokast Platform work, do not run state-changing platform operations on an infra-agent host when instructions require the active ops controller. Enter or dispatch through the controller, then publish from the correct backend host if needed.

## Script

Prefer:

```bash
python3 scripts/publish_webdav_static.py \
  --local-dir .run/generated/my-article \
  --remote-dir www/my-article \
  --base-url "$STATIC_SITE_NEXTCLOUD_WEBDAV_BASE_URL" \
  --username "$STATIC_SITE_NEXTCLOUD_USERNAME" \
  --password-env STATIC_SITE_NEXTCLOUD_PASSWORD \
  --header "Host: next.example.com" \
  --header "X-Forwarded-Proto: https" \
  --header "X-Forwarded-Host: next.example.com"
```

When the script cannot run from the local host because of network topology, copy the generated directory and script to the correct host, then execute it there.

## Verification

After upload:

1. Confirm WebDAV GET byte counts match local files.
2. Poll the public article URL until it returns 200 and contains expected article text.
3. Fetch the banner image URL and confirm it returns 200 and has an image content type or expected byte count.
4. Clean temporary staging files.
