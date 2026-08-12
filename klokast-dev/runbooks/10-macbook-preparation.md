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

The Secret Authority signer uses Apple's native CryptoTokenKit identity
commands, system OpenSSH, and `/usr/lib/ssh-keychain.dylib`. The check fails if
this macOS release does not provide the required `sc_auth` or `ssh-keygen`
features. It does not install another OpenSSH build.

Configure Touch ID for the current Mac user. Then follow
`klokast-dev/runbooks/15-touchid-secret-authority.md` to create the separate
private-instance and static-site approval identities.
