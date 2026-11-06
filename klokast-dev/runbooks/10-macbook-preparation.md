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

From the cloned public repository on the MacBook, run:

```sh
./klokast-dev/bin/kk setup --instance https://github.com/jfm-home/klokast-instance
```

Use your private Instance repository URL. The optional `.git` suffix is accepted.
Without `--instance`, setup prompts for the URL. In a non-interactive shell,
supply the option. Setup uses your configured GitHub SSH credentials through
`prepare-private-instance-worktree`; the HTTPS URL identifies the repository.

Setup requires a clean public worktree on a branch with an upstream. It runs
`git pull --ff-only`, then uses the updated code to run `kk doctor --install`.
The doctor installs supported missing Python dependencies and Mosh. Install
Homebrew, the standalone Tailscale package, and Apple tools separately if the
doctor reports that they are missing. MacBook scripts use Apple's Bash.
See [Shell Automation](../../doc/shell.md).

Setup clones the private repository into `~/src/private-klokast/klokast-instance`.
If that directory exists, its GitHub origin must match the selected repository.
Setup reuses it without pulling or changing private edits. Synchronize the
private checkout separately with ordinary Git. Its `klokast-instance.json`
must be readable JSON; setup does not perform full Instance validation.

After all checks pass, setup writes one marked block in `~/.zprofile`. It puts
the actual public checkout's `klokast-dev/bin` directory first in `PATH` and
sets `KLOKAST_INSTANCE` to the absolute private checkout path. It preserves
settings outside that block. Later setup runs replace the block and put it
last so earlier manual exports cannot override it. An existing `.zprofile`
symlink is preserved. Repair malformed or duplicate block markers before retrying.

Start a new login shell to use these settings:

```sh
exec /bin/zsh -l
```

You can then run `kk setup` from any directory. Setup cannot change the parent
shell's environment. Use the checked-out scripts in place; do not copy them
out of the repository. See the [application command interface](../README.md#application-commands-with-kk).
Run `kk doctor` after a Python replacement, upgrade, or `PATH` change.

After a successful check, the doctor prints the short list of direct software
dependencies that Klokast uses on the MacBook. The declared acquisition
classes are informational. The doctor does not verify acquisition provenance
or package hashes.

The doctor also checks Apple's native CryptoTokenKit and system OpenSSH
features: `sc_auth`, `ssh-keygen`, `ssh-agent`, `ssh-add`, and
`/usr/lib/ssh-keychain.dylib`. These checks do not install another OpenSSH
build or configure an ambient agent.
