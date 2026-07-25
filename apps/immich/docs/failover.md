# Failover And Failback

Failover is manual. Manual promotion is intentional: it avoids split-brain when
residential connectivity or power fails.

## Promote Passive Site

1. Confirm the old active site is down or explicitly fenced.
2. Confirm the latest Immich backup has been restored on the passive backend.
3. Run:

   ```sh
   apps/immich/bin/immichctl promote \
     --old-active k001 \
     --new-active k002 \
     --resources-registry path/to/platform-resources.yml
   ```

4. Confirm `https://photos.<tailnet>` reaches the promoted private ingress.
5. Verify health, login, upload, thumbnail generation, and mobile backup.

## Fail Back

1. Treat the promoted site as the source of truth.
2. Restore a fresh backup onto the original site.
3. Run `immichctl install` with the original active/passive direction and
   `--resources-registry`.
4. Confirm `https://photos.<tailnet>` points to the original active site again.
