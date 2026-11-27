# Sourced by /etc/bash/bashrc for each interactive bash, also a login shell.
export EDITOR=vi
export VISUAL=vi
export GIT_EDITOR=vi
export IGNOREEOF=999

if command -v tmux >/dev/null 2>&1 &&
   [ -z "${TMUX:-}" ] &&
   [ -n "${PS1:-}" ] &&
   [ -t 0 ] &&
   [ -t 1 ]; then
    if /usr/local/bin/klokast-air-workspace open --reconnect; then
        exit
    fi
    cd /home/agent/src/klokast/klokast-box 2>/dev/null || :
    printf '%s\n' 'air workspace: this is a plain shell without tmux.' >&2
fi
