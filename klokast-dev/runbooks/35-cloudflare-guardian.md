# Cloudflare Guardian

Use this target flow when the Platform should manage Cloudflare without giving
the AI runner or `k002-ops` a long-lived Cloudflare bearer token.

Current placement:

- active controller: `k002-ops`
- Cloudflare hardware guardian: `k001-dom0`
- physical second key: YubiKey visible on `k001-dom0`

The guardian is an ephemeral root wrapper, not a daemon. It accepts only a
canonical Secret Authority intent signed by the human approval key, verifies
its local policy and replay log, unseals the Cloudflare authority through a
deployment-private YubiKey command, calls Cloudflare, and exits.

# 1. Converge Guardian

Run from `k002-ops` as `smith`:

```sh
cd ~/src/klokast/klokast-box
git pull --ff-only

ansible/bin/converge-cloudflare-guardian \
  --box k001 \
  --cloudflare-account-id ACCOUNT \
  --cloudflare-zone-id ZONE
```

# 2. Install Authority Material

Prepare these files on the MacBook without putting secrets in chat:

- `cloudflare-authority.enc`: encrypted Cloudflare authority token blob;
- `policy.json`: same static-site policy installed by Ansible;
- `allowed-signers`: approval signer file;
- `unseal-cloudflare-authority`: executable that uses the YubiKey to decrypt
  the encrypted blob and prints the Cloudflare token only to stdout.

Then run:

```sh
klokast-dev/bin/install-cloudflare-authority \
  --guardian k001-dom0 \
  --ciphertext cloudflare-authority.enc \
  --policy policy.json \
  --allowed-signers allowed-signers \
  --unseal-command unseal-cloudflare-authority
```

The helper refuses plaintext Cloudflare tokens.

# 3. Reconcile Static-Site Cloudflare

Generate and sign a fresh intent, then execute it from `k002-ops`:

```sh
ansible/bin/secret-authority intent cloudflare static-site-tunnel \
  --guardian k001-dom0 \
  --box k001 \
  --domain www.klokast.ai \
  --cloudflare-account-id ACCOUNT \
  --cloudflare-zone-id ZONE \
  > intent.json

ssh-keygen -Y sign -f ~/.ssh/klokast-approval-sk \
  -n klokast-secret-authority intent.json

ansible/bin/secret-authority cloudflare static-site-tunnel \
  --guardian k001-dom0 \
  --box k001 \
  --domain www.klokast.ai \
  --cloudflare-account-id ACCOUNT \
  --cloudflare-zone-id ZONE \
  --approval-intent intent.json \
  --approval-signature intent.json.sig \
  --signer-id xiaoju-og
```

Expected result: redacted JSON with tunnel and DNS identifiers, and no
Cloudflare bearer token printed. The returned connector token is stored
root-only in the controller Secret Authority static-site state.

# 4. Continue Static-Site Install

After the Cloudflare guardian action succeeds, continue with the static-site
Secret Authority `install` action. That action will read the stored tunnel
connector token and pass it only to the child installer environment.
