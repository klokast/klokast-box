# Shared helpers for MacBook-side Secret Authority approval wrappers.

ksa_die() {
  printf '%s: %s\n' "${KSA_TOOL_NAME:-klokast-dev}" "$*" >&2
  exit 2
}

ksa_log() {
  printf '%s\n' "$*" >&2
}

ksa_need_command() {
  command -v "$1" >/dev/null 2>&1 || ksa_die "$1 not found"
}

ksa_resolve_executable() {
  candidate="$1"
  case "$candidate" in
    */*)
      [ -x "$candidate" ] || ksa_die "not executable: $candidate"
      printf '%s\n' "$candidate"
      ;;
    *)
      command -v "$candidate" || ksa_die "$candidate not found"
      ;;
  esac
}

ksa_expand_path() {
  case "$1" in
    "~") printf '%s\n' "$HOME" ;;
    "~/"*) printf '%s/%s\n' "$HOME" "${1#~/}" ;;
    *) printf '%s\n' "$1" ;;
  esac
}

ksa_valid_controller() {
  case "$1" in
    ""|*[!A-Za-z0-9.-]*) return 1 ;;
    *) return 0 ;;
  esac
}

ksa_resolve_controller() {
  controller="$1"
  if [ "$controller" = auto ]; then
    repo_root="${KLOKAST_REPO_ROOT:-$(cd "$(dirname "$0")/../.." && pwd -P)}"
    "$repo_root/ansible/bin/ops-controller-ha" resolve-active
  else
    printf '%s\n' "$controller"
  fi
}

ksa_valid_signer_id() {
  case "$1" in
    ""|*[!A-Za-z0-9._@+-]*) return 1 ;;
    *) return 0 ;;
  esac
}

ksa_valid_key_type() {
  case "$1" in
    ed25519-sk|ecdsa-sk) return 0 ;;
    *) return 1 ;;
  esac
}

ksa_valid_wait_seconds() {
  case "$1" in
    ""|*[!0-9]*) return 1 ;;
    *) return 0 ;;
  esac
}

ksa_valid_positive_integer() {
  case "$1" in
    ""|*[!0-9]*|0) return 1 ;;
    *) return 0 ;;
  esac
}

ksa_valid_box() {
  case "$1" in
    ""|*[!a-z0-9-]*) return 1 ;;
    *-bootstrap|*-dom0|*-router|*-bak|*-dmz|*-iot|*-agent|*-ops) return 1 ;;
    bootstrap|dom0|router|bak|dmz|iot|agent|ops) return 1 ;;
    *) return 0 ;;
  esac
}

ksa_valid_domain() {
  case "$1" in
    ""|*[!A-Za-z0-9.-]*|.*|*.) return 1 ;;
    *) return 0 ;;
  esac
}

ksa_valid_github_owner() {
  case "$1" in
    ""|*[!A-Za-z0-9-]*) return 1 ;;
    -*) return 1 ;;
    *-) return 1 ;;
    *) return 0 ;;
  esac
}

ksa_valid_github_repo() {
  case "$1" in
    ""|*[!A-Za-z0-9._-]*) return 1 ;;
    *) return 0 ;;
  esac
}

ksa_valid_git_ref() {
  case "$1" in
    ""|/*|*..*|*@\{*|*\\*|*//*|*.|*/|*[!A-Za-z0-9._/-]*) return 1 ;;
    *) return 0 ;;
  esac
}

ksa_choose_ssh_keygen() {
  requested="$1"
  if [ -n "$requested" ]; then
    requested="$(ksa_expand_path "$requested")"
    ksa_resolve_executable "$requested"
    return 0
  fi

  for candidate in /opt/homebrew/bin/ssh-keygen /usr/local/bin/ssh-keygen; do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  command -v ssh-keygen || ksa_die "ssh-keygen not found"
}

ksa_probe_yubikey() {
  if command -v ykman >/dev/null 2>&1; then
    if ykman list 2>/dev/null | grep -Eiq 'YubiKey|Security Key'; then
      return 0
    fi
    return 1
  fi

  if [ "$(uname -s)" = "Darwin" ] && command -v ioreg >/dev/null 2>&1; then
    if ioreg -p IOUSB -l -w0 2>/dev/null | grep -Eiq 'YubiKey|Yubico|FIDO'; then
      return 0
    fi
    return 1
  fi

  if command -v lsusb >/dev/null 2>&1; then
    if lsusb 2>/dev/null | grep -Eiq 'YubiKey|Yubico|1050:'; then
      return 0
    fi
    return 1
  fi

  return 2
}

ksa_wait_for_yubikey() {
  reason="$1"

  if [ "${skip_yubikey_gate:-false}" = true ]; then
    return 0
  fi
  if [ "${yubikey_gate_done:-false}" = true ]; then
    return 0
  fi

  ksa_log "Insert the YubiKey now for ${reason}."
  if [ -t 0 ]; then
    ksa_log "Press Return after inserting it to continue immediately, or wait for auto-detection."
  fi
  waited=0
  printed_wait=false
  while [ "$waited" -le "${yubikey_wait_seconds:-60}" ]; do
    if ksa_probe_yubikey; then
      rc=0
    else
      rc=$?
    fi
    case "$rc" in
      0)
        ksa_log "YubiKey detected."
        yubikey_gate_done=true
        return 0
        ;;
      2)
        printf 'No USB detector is available. Insert the YubiKey, then press Return to continue: ' >&2
        IFS= read -r _
        yubikey_gate_done=true
        return 0
        ;;
    esac

    if [ "$printed_wait" = false ]; then
      ksa_log "Waiting up to ${yubikey_wait_seconds:-60} seconds for USB detection..."
      printed_wait=true
    fi
    if [ -t 0 ]; then
      if IFS= read -r -t 1 _; then
        ksa_log "Continuing after operator confirmation."
        yubikey_gate_done=true
        return 0
      fi
    else
      sleep 1
    fi
    waited=$((waited + 1))
  done

  ksa_die "YubiKey was not detected after ${yubikey_wait_seconds:-60}s; insert it and rerun, or pass --skip-yubikey-gate if USB detection is unreliable"
}

ksa_write_fido2_pin_askpass() {
  askpass_path="$1"

  cat > "$askpass_path" <<'SH'
#!/bin/sh
tty=/dev/tty

if [ ! -r "$tty" ] || [ ! -w "$tty" ]; then
  exit 1
fi

printf 'Enter the FIDO2 PIN of your Yubikey: ' > "$tty"
old_tty=$(stty -g < "$tty") || exit 1
trap 'stty "$old_tty" < /dev/tty 2>/dev/null || true' EXIT HUP INT TERM
stty -echo < "$tty" || exit 1
IFS= read -r pin < "$tty" || exit 1
stty "$old_tty" < "$tty" || true
trap - EXIT HUP INT TERM
printf '\n' > "$tty"
printf '%s\n' "$pin"
SH
  chmod 0700 "$askpass_path"
}

ksa_filter_ssh_keygen_sign_output() {
  sed \
    -e '/^Signing file /d' \
    -e 's/^Enter PIN for .* key:$/Enter the FIDO2 PIN of your Yubikey:/' \
    -e 's/^Confirm user presence for key .*/Touch the Yubikey/' \
    -e 's/^Write signature to /Writing signature to /'
}

ksa_run_ssh_keygen_sign() {
  message_path="$1"
  askpass_path="$tmpdir/ssh-askpass-fido2-pin"
  sign_stdout="$tmpdir/ssh-keygen-sign.stdout"
  sign_stderr="$tmpdir/ssh-keygen-sign.stderr"

  ksa_write_fido2_pin_askpass "$askpass_path"
  rm -f "$sign_stdout" "$sign_stderr" "${message_path}.sig"
  ksa_log "Touch the Yubikey"
  if SSH_ASKPASS="$askpass_path" \
      SSH_ASKPASS_REQUIRE=force \
      DISPLAY="${DISPLAY:-klokast}" \
      "$ssh_keygen" -q -Y sign -f "$key_path" -n "$SIGNATURE_NAMESPACE" "$message_path" \
      > "$sign_stdout" 2> "$sign_stderr"; then
    ksa_log "Writing signature to ${message_path}.sig"
    return 0
  fi

  cat "$sign_stdout" "$sign_stderr" | ksa_filter_ssh_keygen_sign_output >&2
  return 1
}

ksa_sign_message_with_retries() {
  message_path="$1"
  attempt=1

  while [ "$attempt" -le "${signature_attempts:-3}" ]; do
    ksa_log "Approval signature attempt ${attempt}/${signature_attempts:-3}."
    if ksa_run_ssh_keygen_sign "$message_path"; then
      return 0
    fi

    if [ "$attempt" -lt "${signature_attempts:-3}" ]; then
      ksa_log "Approval signature attempt failed. Retrying so you can enter the YubiKey FIDO2 PIN again."
    fi
    attempt=$((attempt + 1))
  done

  return 1
}
