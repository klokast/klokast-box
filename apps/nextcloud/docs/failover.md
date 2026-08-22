# Failover And Failback

Failover is manual. Manual promotion is intentional: it avoids split-brain when
residential connectivity or power fails.

## Promote Passive Site

1. Confirm the old active site is down or explicitly fenced.
2. Confirm the latest encrypted backup has been restored on the passive backend.
3. Run:

   ```sh
   apps/nextcloud/bin/nextcloudctl promote \
     --old-active boxa \
     --new-active boxb \
     --domain cloud.example.tld \
     --resources-registry path/to/platform-resources.yml
   ```

4. Confirm `https://next.<tailnet>` reaches the promoted private ingress.
5. If Cloudflare is enabled, move the Cloudflare route for
   `cloud.example.tld` to the new active tunnel.
6. Verify `status.php`, login, WebDAV upload/download, cron mode, and mobile or
   desktop sync.

## Fail Back

1. Treat the promoted site as the source of truth.
2. Restore a fresh backup onto the original site.
3. Run `nextcloudctl install` with the original active/passive direction and
   `--resources-registry`.
4. Confirm `https://next.<tailnet>` points to the original active site again.
5. Move Cloudflare back after verification when Cloudflare is enabled.
