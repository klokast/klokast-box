# Touch ID Secret Authority Signers

Use this runbook on the trusted `og` MacBook. It creates two separate Apple
CryptoTokenKit identities:

- `Klokast private-instance approval` for private-instance repository actions;
- `Klokast static-site approval` for static-site actions.

Each identity uses a non-exportable P-256 Secure Enclave key with biometric
protection. The local OpenSSH file is only a key handle. It does not contain
the private key. These identities are not passkeys.

Do not paste approval intent files, signatures, or secrets into chat.

## 1. Check The MacBook

```sh
cd path/to/klokast-box
git pull --ff-only
klokast-dev/bin/kk doctor
```

The check must find these Apple-native components:

- `/usr/sbin/sc_auth` with CryptoTokenKit identity commands;
- `/usr/bin/ssh-keygen` with `-K`, `-Y`, and `-w` support;
- `/usr/lib/ssh-keychain.dylib`.

The workflow does not use an SSH agent. It does not install another OpenSSH
build.

## 2. Install The Private-Instance Signer

```sh
klokast-dev/bin/install-secret-authority-approval-signer \
  --controller k002-ops \
  --purpose private-instance
```

Approve identity creation. Then use Touch ID when macOS asks for access to the
key. The command installs only the public key on the controller as
`human-private-instance`.

## 3. Install The Static-Site Signer

```sh
klokast-dev/bin/install-secret-authority-approval-signer \
  --controller k002-ops \
  --purpose static-site
```

Use Touch ID for this separate identity. The command installs only the public
key on the controller as `human-static-site`.

The two local profiles are under:

```text
~/.local/share/klokast/approval-signers/private-instance/
~/.local/share/klokast/approval-signers/static-site/
```

Do not copy these directories to another computer. The key handles work only
with the Secure Enclave identities on this Mac.

## 4. Deploy The Scoped Controller Verification

Run this step from the active controller as `smith`, not from the cloud
infra-agent:

```sh
cd ~/src/klokast/klokast-box
git pull --ff-only
ansible/bin/converge-ops-controller --box k002
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
  --controller k002-ops \
  --finalize-migration
```

## Recovery

The Secure Enclave keys cannot be backed up or moved. Before you replace a
working Mac, create replacement identities on the new trusted Mac and install
their public keys. If the old Mac is lost, use the approved controller access
and local console recovery paths to install replacement public signers.

Stop if an exact identity label exists without its Klokast profile metadata.
Do not adopt an identity of unknown origin. Inspect it and delete it with
`sc_auth delete-ctk-identity -h HASH` only after you confirm the exact hash.
