# Touch ID Secret Authority Signers

Use this runbook on the trusted `og` MacBook. It creates two separate Apple
CryptoTokenKit identities:

- `Klokast private-instance approval` for private-instance repository actions;
- `Klokast static-site approval` for static-site actions.

Each identity uses a non-exportable P-256 Secure Enclave key with biometric
protection. The local Klokast profile contains the public key and identity
metadata. It does not contain the private key. These identities are not
passkeys.

Do not paste approval intent files, signatures, or secrets into chat.

## 1. Check The MacBook

```sh
cd path/to/klokast-box
git pull --ff-only
klokast-dev/bin/kk doctor
```

The check must find these Apple-native components:

- `/usr/sbin/sc_auth` with CryptoTokenKit identity commands;
- `/usr/bin/ssh-keygen` with `-Y` and `-w` support;
- `/usr/bin/ssh-agent` and `/usr/bin/ssh-add` with resident-key provider
  support;
- `/usr/lib/ssh-keychain.dylib`.

Apple OpenSSH gives the same default handle filename to multiple resident
P-256 CryptoTokenKit identities. The workflow therefore loads the identities
into a private Apple `ssh-agent`, selects the exact key by its SSH fingerprint,
and stops the agent after the operation. It does not use the ambient agent and
does not install another OpenSSH build.

## 2. Install The Private-Instance Signer

```sh
klokast-dev/bin/install-secret-authority-approval-signer \
  --controller boxb-ops \
  --purpose private-instance
```

Approve identity creation. Then use Touch ID when macOS asks for access to the
key. The command installs only the public key on the controller as
`human-private-instance`.

## 3. Install The Static-Site Signer

```sh
klokast-dev/bin/install-secret-authority-approval-signer \
  --controller boxb-ops \
  --purpose static-site
```

Use Touch ID for this separate identity. The command installs only the public
key on the controller as `human-static-site`.

If an earlier attempt stopped at `id_ecdsa_sk_rk already exists`, run the same
command again. Do not delete either CryptoTokenKit identity. The command shows
the existing static-site identity hash and SSH fingerprint. Approve recovery
of its incomplete local Klokast profile. The private key is not regenerated.

The two local profiles are under:

```text
~/.local/share/klokast/approval-signers/private-instance/
~/.local/share/klokast/approval-signers/static-site/
```

Do not copy these directories to another computer as an identity backup. The
public keys are not secret, but the matching private keys exist only on this
Mac.

## 4. Deploy The Scoped Controller Verification

```sh
tailscale ssh smith@boxb-ops
cd ~/src/klokast/klokast-box
git pull --ff-only
ansible/bin/converge-ops-controller --box boxb
```

The private-instance authority now reads only
`allowed-signers-private-instance`. The static-site authority reads only
`allowed-signers-static-site`.

## 5. Retire The Legacy Signer File

Return to the MacBook. Finalization asks for one Touch ID approval from each
identity. It also checks that the deployed controller wrappers report the two
new signer scopes. It removes the old generic signer file only after all checks
pass.

```sh
klokast-dev/bin/install-secret-authority-approval-signer \
  --controller boxb-ops \
  --finalize-migration
```

## Recovery

The Secure Enclave keys cannot be backed up or moved. Before you replace a
working Mac, create replacement identities on the new trusted Mac and install
their public keys. If the old Mac is lost, use the approved controller access
and local console recovery paths to install replacement public signers.

If an exact identity label exists without its Klokast profile metadata, the
helper shows its identity hash and SSH fingerprint and asks before it rebuilds
the public profile. Approve this only when you know that the interrupted
Klokast command created the identity. Do not adopt an identity of unknown
origin. Delete one with `sc_auth delete-ctk-identity -h HASH` only after you
confirm the exact hash.
