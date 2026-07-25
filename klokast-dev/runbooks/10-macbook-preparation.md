The developer machine is a MacBook (Apple silicon).

# Ghostty on MacBook

MacBook > Ghostty > settings >
```
scrollbar = system
mouse-scroll-multiplier = 1
scrollback-limit = 100000
mouse-reporting = false
clipboard-write = allow
copy-on-select = clipboard

```
 Then reload the configuration with `⌘⇧,`

# Install Tailscale on MacBook

For Tailscale ssh to work from MacBook, Tailscale must be installed using the `.pkg` file from `https://pkgs.tailscale.com/stable/#macos`. Tailscale must not be installed with `brew`, or from the App Store.

# Install MacBook CLI Dependencies

From a checkout of this repo on the MacBook:

```sh
klokast-dev/bin/kk doctor --install
```

This installs Homebrew OpenSSH when it is missing. The Secret Authority approval
signer needs a recent OpenSSH build with FIDO/YubiKey support; Apple’s bundled
`ssh-keygen` may not include the FIDO provider needed for `ed25519-sk` or
`ecdsa-sk` approval keys.

If Git starts failing with this error after Homebrew OpenSSH is installed:

```text
Bad configuration option: usekeychain
```

the Git install is not broken. Git is finding Homebrew `ssh`, and Homebrew
OpenSSH does not recognize Apple's `UseKeychain` option unless the SSH config
explicitly says to ignore that option when unknown.

Fix it with:

```sh
klokast-dev/bin/kk doctor --fix-ssh-config
```

The command adds this line before the existing config and writes a timestamped
backup next to `~/.ssh/config`:

```sshconfig
IgnoreUnknown UseKeychain
```
