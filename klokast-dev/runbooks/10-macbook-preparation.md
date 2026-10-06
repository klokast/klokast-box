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

Install [Homebrew](https://brew.sh/) first. On this Apple silicon MacBook,
put Homebrew's `bin` directory before Apple's system directories in `PATH`:

```sh
eval "$(/opt/homebrew/bin/brew shellenv)"
```

For a zsh login shell, add the same line to `~/.zprofile`. For another login
shell, configure its startup `PATH` accordingly. Keep the chosen terminal's
`PATH` consistent with that setting.

From a checkout of this repo on the MacBook:

```sh
klokast-dev/bin/kk doctor --install
```

The doctor installs [Homebrew Bash](https://formulae.brew.sh/formula/bash),
Python, and the pinned PyYAML module used by the controller HA and private
publication helpers. MacBook Bash scripts require version 5.2 or newer, as
defined in [Shell Automation](../../doc/shell.md). The doctor verifies that
`PATH` selects Homebrew Bash and reports its version and executable path.
Run it again after a Bash or Python replacement, upgrade, or `PATH` change.

Installing Bash does not change the MacBook login shell. If the doctor reports
that `PATH` selects Apple's Bash, correct the Homebrew `PATH` setting and run
the doctor again. Scripts use `#!/usr/bin/env bash` to select the executable.

After a successful check, the doctor prints the short list of direct software
dependencies that Klokast uses on the MacBook. The declared acquisition
classes are informational. The Bash check verifies the selected executable
against Homebrew's formula path and checks its minimum version. The other
entries do not yet verify resolved versions, acquisition provenance, or
package hashes.

The Secret Authority signer uses Apple's native CryptoTokenKit identity
commands, system OpenSSH, and `/usr/lib/ssh-keychain.dylib`. It uses a private
Apple `ssh-agent` only for one signing operation. The check fails if this macOS
release does not provide the required `sc_auth`, `ssh-keygen`, `ssh-agent`, or
`ssh-add` features. It does not install another OpenSSH build or configure an
ambient agent.

Configure Touch ID for the current Mac user. Then follow
`klokast-dev/runbooks/15-touchid-secret-authority.md` to create the separate
private-instance, static-site, and platform-apply approval identities.
