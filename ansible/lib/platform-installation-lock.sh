# Source this file before a provisioning or replacement mutation. The fixed
# descriptor is inherited by nested shell wrappers; no child releases it.
platform_installation_lock() {
  KLOKAST_INSTALLATION_LOCK=/var/lib/klokast/updates/operation.lock
  command -v flock >/dev/null 2>&1 || { echo 'flock is required for installation serialization' >&2; return 1; }
  [ ! -L /var/lib/klokast/updates ] &&
    [ "$(stat -c '%u:%a' /var/lib/klokast/updates 2>/dev/null)" = '0:755' ] || {
      echo 'Platform installation lock directory is absent or unsafe; install platform-update-authority' >&2; return 1;
    }
  [ ! -L "$KLOKAST_INSTALLATION_LOCK" ] &&
    [ "$(stat -c '%u:%g:%a:%h' "$KLOKAST_INSTALLATION_LOCK" 2>/dev/null)" = "0:$(id -g):660:1" ] || {
      echo 'Platform installation lock is absent or unsafe; install platform-update-authority' >&2; return 1;
    }
  if [ -n "${KLOKAST_INSTALLATION_LOCK_FD:-}" ]; then
    [ "$KLOKAST_INSTALLATION_LOCK_FD" = 9 ] || {
      echo 'Inherited Platform lock descriptor is invalid' >&2; return 1;
    }
    [ "$(stat -Lc '%d:%i' /proc/$$/fd/9 2>/dev/null)" = "$(stat -c '%d:%i' "$KLOKAST_INSTALLATION_LOCK")" ] || {
      echo 'Inherited Platform lock descriptor does not identify the installation lock' >&2; return 1;
    }
  else
    exec 9<>"$KLOKAST_INSTALLATION_LOCK"
    KLOKAST_INSTALLATION_LOCK_FD=9
    export KLOKAST_INSTALLATION_LOCK_FD
  fi
  flock -n 9 || {
    echo 'Another VM update or provisioning operation holds the installation lock' >&2; return 1;
  }
}
