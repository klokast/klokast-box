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

Use [Homebrew](https://brew.sh/) if you need to install Python. On this Apple silicon MacBook,
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

The doctor can install Python and the pinned PyYAML module used by the
controller HA and private publication helpers. MacBook scripts use the Bash
supplied by macOS; no separate Bash installation is required. See
[Shell Automation](../../doc/shell.md). Run the doctor again after a Python
replacement, upgrade, or `PATH` change.

To run `kk` from any directory, add the checkout's tools to `PATH`. For the
standard checkout location, add this line to `~/.zprofile`:

```sh
export PATH="$HOME/src/klokast/klokast-box/klokast-dev/bin:$PATH"
```

Start a new login shell and run `kk doctor`. Use the checked-out script in
place; do not copy it out of
the repository. See the [application command interface](../README.md#application-commands-with-kk)
to select a private Instance worktree and run application commands.

After a successful check, the doctor prints the short list of direct software
dependencies that Klokast uses on the MacBook. The declared acquisition
classes are informational. The doctor does not verify acquisition provenance
or package hashes.

The doctor also checks Apple's native CryptoTokenKit and system OpenSSH
features: `sc_auth`, `ssh-keygen`, `ssh-agent`, `ssh-add`, and
`/usr/lib/ssh-keychain.dylib`. These checks do not install another OpenSSH
build or configure an ambient agent.
