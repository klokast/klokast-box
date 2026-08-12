# Shared helpers for MacBook-side Secret Authority approval wrappers.

KSA_CTK_SC_AUTH=/usr/sbin/sc_auth
KSA_CTK_SSH_KEYGEN=/usr/bin/ssh-keygen
KSA_CTK_SSH_AGENT=/usr/bin/ssh-agent
KSA_CTK_SSH_ADD=/usr/bin/ssh-add
KSA_CTK_PROVIDER=/usr/lib/ssh-keychain.dylib
KSA_APPROVAL_PROFILE_ROOT="$HOME/.local/share/klokast/approval-signers"

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
    ""|*[!A-Za-z0-9-]*|-*|*-) return 1 ;;
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

ksa_select_touchid_profile() {
  approval_purpose="$1"
  case "$approval_purpose" in
    private-instance)
      signer_id=human-private-instance
      ctk_label='Klokast private-instance approval'
      allowed_signers_path=/etc/klokast/secret-authority/allowed-signers-private-instance
      ;;
    static-site)
      signer_id=human-static-site
      ctk_label='Klokast static-site approval'
      allowed_signers_path=/etc/klokast/secret-authority/allowed-signers-static-site
      ;;
    *)
      ksa_die "approval purpose must be private-instance or static-site"
      ;;
  esac
  profile_dir="$KSA_APPROVAL_PROFILE_ROOT/$approval_purpose"
  manifest_path="$profile_dir/profile"
  key_path="$profile_dir/id_ecdsa_sk_rk"
  pub_path="${key_path}.pub"
  ssh_keygen="$KSA_CTK_SSH_KEYGEN"
  ssh_agent="$KSA_CTK_SSH_AGENT"
  ssh_add="$KSA_CTK_SSH_ADD"
  sk_provider="$KSA_CTK_PROVIDER"
}

ksa_require_native_touchid() {
  [ "$(uname -s)" = Darwin ] || ksa_die "run this approval workflow on the trusted MacBook"
  [ -x "$KSA_CTK_SC_AUTH" ] || ksa_die "Apple sc_auth is missing: $KSA_CTK_SC_AUTH"
  [ -x "$KSA_CTK_SSH_KEYGEN" ] || ksa_die "Apple ssh-keygen is missing: $KSA_CTK_SSH_KEYGEN"
  [ -x "$KSA_CTK_SSH_AGENT" ] || ksa_die "Apple ssh-agent is missing: $KSA_CTK_SSH_AGENT"
  [ -x "$KSA_CTK_SSH_ADD" ] || ksa_die "Apple ssh-add is missing: $KSA_CTK_SSH_ADD"
  [ -f "$KSA_CTK_PROVIDER" ] || ksa_die "Apple CryptoTokenKit SSH provider is missing: $KSA_CTK_PROVIDER"

  sc_auth_help="$($KSA_CTK_SC_AUTH 2>&1 || true)"
  printf '%s\n' "$sc_auth_help" | grep -q 'create-ctk-identity' || \
    ksa_die "this macOS release does not support sc_auth create-ctk-identity"
  printf '%s\n' "$sc_auth_help" | grep -q 'list-ctk-identities' || \
    ksa_die "this macOS release does not support sc_auth list-ctk-identities"

  ssh_keygen_help="$($KSA_CTK_SSH_KEYGEN -? 2>&1 || true)"
  printf '%s\n' "$ssh_keygen_help" | grep -q -- '-Y' || \
    ksa_die "Apple ssh-keygen does not support file signing (-Y)"
  printf '%s\n' "$ssh_keygen_help" | grep -q -- '-w' || \
    ksa_die "Apple ssh-keygen does not support an external SecurityKeyProvider (-w)"

  ssh_add_help="$($KSA_CTK_SSH_ADD -? 2>&1 || true)"
  printf '%s\n' "$ssh_add_help" | grep -Eq -- '(-K|\[[^]]*K[^]]*\])' || \
    ksa_die "Apple ssh-add does not support resident-key loading (-K)"
  printf '%s\n' "$ssh_add_help" | grep -q -- '-S' || \
    ksa_die "Apple ssh-add does not support an external SecurityKeyProvider (-S)"
}

ksa_manifest_field() {
  field="$1"
  file="$2"
  value="$(sed -n "s/^${field}=//p" "$file")"
  count="$(sed -n "s/^${field}=//p" "$file" | wc -l | tr -d ' ')"
  [ "$count" = 1 ] || ksa_die "approval profile has an invalid ${field} field: $file"
  printf '%s\n' "$value"
}

ksa_identity_hashes_for_label() {
  "$KSA_CTK_SC_AUTH" identities | awk -F '\t' -v expected="$ctk_label" '
    NF >= 2 && $2 == expected { print $1 }
  '
}

ksa_exact_identity_hash() {
  hashes="$(ksa_identity_hashes_for_label)"
  count="$(printf '%s\n' "$hashes" | awk 'NF { count += 1 } END { print count + 0 }')"
  [ "$count" -le 1 ] || ksa_die "more than one CryptoTokenKit identity has label: $ctk_label"
  if [ "$count" -eq 0 ]; then
    return 1
  fi
  ctk_hash="$(printf '%s\n' "$hashes" | awk 'NF { print; exit }')"
  case "$ctk_hash" in
    ""|*[!0-9A-Fa-f]*)
      ksa_die "CryptoTokenKit returned an invalid identity hash for: $ctk_label"
      ;;
  esac
  [ "${#ctk_hash}" -eq 40 ] || ksa_die "CryptoTokenKit identity hash is not SHA-1 for: $ctk_label"
  printf '%s\n' "$ctk_hash"
}

ksa_exact_identity_ssh_fingerprint() {
  identity_rows="$($KSA_CTK_SC_AUTH list-ctk-identities -t ssh -e b64)" || \
    ksa_die "CryptoTokenKit could not list SSH fingerprints"
  fingerprints="$(printf '%s\n' "$identity_rows" | awk -v expected="$signer_id" '
    $1 == "p-256-ne" && $2 ~ /^SHA256:/ && $3 == "bio" {
      for (i = 4; i <= NF; i += 1) {
        if ($i == expected) {
          print $2
          break
        }
      }
    }
  ')"
  count="$(printf '%s\n' "$fingerprints" | awk 'NF { count += 1 } END { print count + 0 }')"
  [ "$count" -eq 1 ] || \
    ksa_die "CryptoTokenKit did not return exactly one biometric P-256 identity with common name: $signer_id"
  ctk_ssh_fingerprint="$(printf '%s\n' "$fingerprints" | awk 'NF { print; exit }')"
  printf '%s\n' "$ctk_ssh_fingerprint"
}

ksa_normalize_public_key() {
  awk 'NF >= 2 { print $1, $2; found = 1; exit } END { if (!found) exit 2 }' "$1"
}

ksa_key_fingerprint() {
  fingerprint_line="$($KSA_CTK_SSH_KEYGEN -E sha256 -lf "$1")" || \
    ksa_die "cannot read approval public-key fingerprint: $1"
  fingerprint="$(printf '%s\n' "$fingerprint_line" | awk 'NF >= 2 { print $2; exit }')"
  case "$fingerprint" in
    SHA256:*) printf '%s\n' "$fingerprint" ;;
    *) ksa_die "approval public key has an invalid SHA-256 fingerprint" ;;
  esac
}

ksa_with_ephemeral_touchid_agent() (
  operation="$1"
  output_path="$2"
  message_path="${3:-}"
  agent_dir="$(mktemp -d /tmp/ksa-touchid-agent.XXXXXX)" || \
    ksa_die "cannot create the temporary Apple agent directory"
  agent_socket="$agent_dir/agent.sock"
  agent_pid=""

  cleanup_agent() {
    if [ -n "$agent_pid" ]; then
      SSH_AUTH_SOCK="$agent_socket" SSH_AGENT_PID="$agent_pid" \
        "$KSA_CTK_SSH_AGENT" -k >/dev/null 2>&1 || true
    fi
    case "$agent_dir" in
      /tmp/ksa-touchid-agent.*) rm -rf "$agent_dir" ;;
    esac
  }
  trap cleanup_agent EXIT HUP INT TERM

  agent_output="$(
    SSH_AUTH_SOCK= SSH_AGENT_PID= \
      "$KSA_CTK_SSH_AGENT" -a "$agent_socket" -s
  )" || ksa_die "Apple ssh-agent could not start"
  agent_pid="$(printf '%s\n' "$agent_output" | sed -n 's/^SSH_AGENT_PID=\([0-9][0-9]*\);.*$/\1/p')"
  case "$agent_pid" in
    ""|*[!0-9]*) ksa_die "Apple ssh-agent returned an invalid process ID" ;;
  esac
  SSH_AUTH_SOCK="$agent_socket"
  SSH_AGENT_PID="$agent_pid"
  export SSH_AUTH_SOCK SSH_AGENT_PID

  ksa_log "Loading the Mac Secure Enclave identities into a private, short-lived Apple agent..."
  printf '\n' | "$KSA_CTK_SSH_ADD" -q -K -S "$KSA_CTK_PROVIDER" || \
    ksa_die "Apple OpenSSH could not load the CryptoTokenKit identities"
  "$KSA_CTK_SSH_ADD" -L > "$agent_dir/public-keys" || \
    ksa_die "Apple OpenSSH could not list the loaded identities"

  match_count=0
  : > "$agent_dir/matched.pub"
  while IFS= read -r agent_key; do
    [ -n "$agent_key" ] || continue
    printf '%s\n' "$agent_key" > "$agent_dir/candidate.pub"
    candidate_fingerprint="$(ksa_key_fingerprint "$agent_dir/candidate.pub")"
    if [ "$candidate_fingerprint" = "$ctk_ssh_fingerprint" ]; then
      ksa_normalize_public_key "$agent_dir/candidate.pub" > "$agent_dir/matched.pub" || \
        ksa_die "the selected Apple agent public key is invalid"
      match_count=$((match_count + 1))
    fi
  done < "$agent_dir/public-keys"
  [ "$match_count" -eq 1 ] || \
    ksa_die "Apple OpenSSH did not load exactly one key for: $ctk_label"

  case "$operation" in
    recover-public)
      install -m 0600 "$agent_dir/matched.pub" "$output_path"
      ;;
    sign)
      ksa_log "Approve the ${approval_purpose} signature with Touch ID."
      rm -f "${message_path}.sig"
      "$KSA_CTK_SSH_KEYGEN" -q -Y sign \
        -f "$agent_dir/matched.pub" -n "$SIGNATURE_NAMESPACE" "$message_path" || \
        exit 1
      [ -f "${message_path}.sig" ] || exit 1
      ;;
    *)
      ksa_die "invalid Apple agent operation: $operation"
      ;;
  esac
)

ksa_recover_touchid_public_key() {
  recovered_pub="$1"
  ksa_log "Getting the public key for the exact ${approval_purpose} identity."
  ksa_log "This can take a short time. The Secure Enclave key already exists; the helper is not generating another key."
  ksa_with_ephemeral_touchid_agent recover-public "$recovered_pub" || \
    ksa_die "OpenSSH could not obtain the public key for: $ctk_label"
}

ksa_write_touchid_manifest() {
  profile_schema="$1"
  manifest_tmp="$profile_dir/.profile.$$"
  {
    printf 'schema_version=%s\n' "$profile_schema"
    printf 'purpose=%s\n' "$approval_purpose"
    printf 'signer_id=%s\n' "$signer_id"
    printf 'ctk_label=%s\n' "$ctk_label"
    printf 'ctk_hash=%s\n' "$ctk_hash"
    printf 'key_fingerprint=%s\n' "$key_fingerprint"
    printf 'key_type=p-256-ne\n'
    printf 'protection=bio\n'
    if [ "$profile_schema" = 1 ]; then
      printf 'key_path=%s\n' "$key_path"
      printf 'signing_mode=direct-key-handle\n'
    else
      printf 'public_key_path=%s\n' "$pub_path"
      printf 'signing_mode=ephemeral-apple-agent\n'
    fi
    printf 'ssh_keygen=%s\n' "$KSA_CTK_SSH_KEYGEN"
    printf 'ssh_agent=%s\n' "$KSA_CTK_SSH_AGENT"
    printf 'ssh_add=%s\n' "$KSA_CTK_SSH_ADD"
    printf 'sk_provider=%s\n' "$KSA_CTK_PROVIDER"
  } > "$manifest_tmp"
  chmod 0600 "$manifest_tmp"
  mv -f "$manifest_tmp" "$manifest_path"
}

ksa_validate_touchid_manifest() {
  profile_schema="$(ksa_manifest_field schema_version "$manifest_path")"
  case "$profile_schema" in
    1|2) ;;
    *) ksa_die "approval profile schema is not supported: $manifest_path" ;;
  esac
  [ "$(ksa_manifest_field purpose "$manifest_path")" = "$approval_purpose" ] || \
    ksa_die "approval profile purpose does not match its directory: $manifest_path"
  [ "$(ksa_manifest_field signer_id "$manifest_path")" = "$signer_id" ] || \
    ksa_die "approval profile signer ID does not match purpose: $manifest_path"
  [ "$(ksa_manifest_field ctk_label "$manifest_path")" = "$ctk_label" ] || \
    ksa_die "approval profile label does not match purpose: $manifest_path"
  [ "$(ksa_manifest_field ctk_hash "$manifest_path")" = "$ctk_hash" ] || \
    ksa_die "approval profile identity hash no longer matches CryptoTokenKit"
  [ "$(ksa_manifest_field key_type "$manifest_path")" = p-256-ne ] || \
    ksa_die "approval profile is not marked non-exportable P-256"
  [ "$(ksa_manifest_field protection "$manifest_path")" = bio ] || \
    ksa_die "approval profile is not marked as biometric-protected"
  if [ "$profile_schema" = 1 ]; then
    [ "$(ksa_manifest_field key_path "$manifest_path")" = "$key_path" ] || \
      ksa_die "approval profile key path is not the fixed purpose path"
  else
    [ "$(ksa_manifest_field public_key_path "$manifest_path")" = "$pub_path" ] || \
      ksa_die "approval profile public-key path is not the fixed purpose path"
    [ "$(ksa_manifest_field signing_mode "$manifest_path")" = ephemeral-apple-agent ] || \
      ksa_die "approval profile does not use the short-lived Apple agent"
  fi
  [ "$(ksa_manifest_field ssh_keygen "$manifest_path")" = "$KSA_CTK_SSH_KEYGEN" ] || \
    ksa_die "approval profile does not use Apple ssh-keygen"
  if [ "$profile_schema" = 2 ]; then
    [ "$(ksa_manifest_field ssh_agent "$manifest_path")" = "$KSA_CTK_SSH_AGENT" ] || \
      ksa_die "approval profile does not use Apple ssh-agent"
    [ "$(ksa_manifest_field ssh_add "$manifest_path")" = "$KSA_CTK_SSH_ADD" ] || \
      ksa_die "approval profile does not use Apple ssh-add"
  fi
  [ "$(ksa_manifest_field sk_provider "$manifest_path")" = "$KSA_CTK_PROVIDER" ] || \
    ksa_die "approval profile does not use Apple's CryptoTokenKit provider"
  key_fingerprint="$(ksa_manifest_field key_fingerprint "$manifest_path")"
}

ksa_ensure_touchid_profile() {
  purpose="$1"
  work_dir="$2"
  ksa_select_touchid_profile "$purpose"
  ksa_require_native_touchid

  install -d -m 0700 "$KSA_APPROVAL_PROFILE_ROOT"
  install -d -m 0700 "$profile_dir"

  current_hash=""
  ctk_hash=""
  if current_hash="$(ksa_exact_identity_hash)"; then
    ctk_hash="$current_hash"
  fi
  if [ -n "$ctk_hash" ]; then
    ctk_ssh_fingerprint="$(ksa_exact_identity_ssh_fingerprint)"
  fi

  if [ -f "$manifest_path" ]; then
    [ -n "$ctk_hash" ] || ksa_die "approval profile exists, but its CryptoTokenKit identity is missing"
    ksa_validate_touchid_manifest
  else
    [ ! -e "$key_path" ] && [ ! -e "$pub_path" ] || \
      ksa_die "approval files exist without trusted profile metadata: $profile_dir"
    if [ -n "$ctk_hash" ]; then
      printf 'The identity "%s" exists, but its local Klokast profile is incomplete.\n' "$ctk_label" >&2
      printf 'Identity hash: %s\n' "$ctk_hash" >&2
      printf 'SSH fingerprint: %s\n' "$ctk_ssh_fingerprint" >&2
      printf 'Recover its public profile now? [y/N] ' >&2
      IFS= read -r answer || ksa_die "input ended before profile recovery confirmation"
      case "$answer" in
        y|Y|yes|YES|Yes) ;;
        *) ksa_die "approval identity profile recovery was not approved" ;;
      esac
    else
      printf 'Create the non-exportable Touch ID identity "%s"? [y/N] ' "$ctk_label" >&2
      IFS= read -r answer || ksa_die "input ended before identity creation confirmation"
      case "$answer" in
        y|Y|yes|YES|Yes) ;;
        *) ksa_die "approval identity creation was not approved" ;;
      esac
      "$KSA_CTK_SC_AUTH" create-ctk-identity \
        -l "$ctk_label" -k p-256-ne -t bio -N "$signer_id" || \
        ksa_die "Apple CryptoTokenKit could not create the approval identity"
      ctk_hash="$(ksa_exact_identity_hash)" || \
        ksa_die "created CryptoTokenKit identity was not found by exact label"
      ctk_ssh_fingerprint="$(ksa_exact_identity_ssh_fingerprint)"
    fi
  fi

  if [ ! -f "$manifest_path" ]; then
    recovered_pub="$work_dir/${purpose}.recovered.pub"
    ksa_recover_touchid_public_key "$recovered_pub"
    key_fingerprint="$(ksa_key_fingerprint "$recovered_pub")"
    [ "$key_fingerprint" = "$ctk_ssh_fingerprint" ] || \
      ksa_die "recovered public key does not match the selected CryptoTokenKit identity"
    install -m 0600 "$recovered_pub" "$pub_path"
    ksa_write_touchid_manifest 2
  fi

  [ -f "$pub_path" ] || ksa_die "approval public key is missing: $profile_dir"
  chmod 0600 "$pub_path"
  if [ -f "$key_path" ]; then
    chmod 0600 "$key_path"
  fi
  normalized_pub="$work_dir/${purpose}.pub"
  ksa_normalize_public_key "$pub_path" > "$normalized_pub" || \
    ksa_die "approval public key is invalid: $pub_path"
  grep -Eq '^sk-ecdsa-sha2-nistp256@openssh.com ' "$normalized_pub" || \
    ksa_die "approval public key is not a P-256 OpenSSH security-key handle"
  key_fingerprint="$(ksa_key_fingerprint "$normalized_pub")"

  if [ -f "$manifest_path" ]; then
    recorded_fingerprint="$(ksa_manifest_field key_fingerprint "$manifest_path")"
    [ "$key_fingerprint" = "$recorded_fingerprint" ] || \
      ksa_die "approval public key no longer matches the recorded fingerprint"
  fi
  [ "$key_fingerprint" = "$ctk_ssh_fingerprint" ] || \
    ksa_die "approval public key does not match the selected CryptoTokenKit identity"
  ksa_validate_touchid_manifest
}

ksa_load_touchid_profile() {
  purpose="$1"
  ksa_select_touchid_profile "$purpose"
  [ -f "$manifest_path" ] || \
    ksa_die "Touch ID approval profile is missing; run install-secret-authority-approval-signer --purpose $purpose"
  ctk_hash="$(ksa_exact_identity_hash)" || \
    ksa_die "CryptoTokenKit approval identity is missing: $ctk_label"
  ctk_ssh_fingerprint="$(ksa_exact_identity_ssh_fingerprint)"
  ksa_validate_touchid_manifest
  [ -f "$pub_path" ] || \
    ksa_die "Touch ID approval public key is missing: $profile_dir"
  if [ "$profile_schema" = 1 ]; then
    [ -f "$key_path" ] || ksa_die "Touch ID approval key handle is missing: $profile_dir"
    chmod 0600 "$key_path"
  fi
  chmod 0600 "$pub_path"
  actual_fingerprint="$(ksa_key_fingerprint "$pub_path")"
  [ "$actual_fingerprint" = "$key_fingerprint" ] || \
    ksa_die "Touch ID approval public key does not match its profile"
  [ "$actual_fingerprint" = "$ctk_ssh_fingerprint" ] || \
    ksa_die "Touch ID approval public key does not match its CryptoTokenKit identity"
}

ksa_sign_touchid_message() {
  message_path="$1"
  rm -f "${message_path}.sig"
  if [ "$profile_schema" = 1 ]; then
    ksa_log "Approve the ${approval_purpose} signature with Touch ID."
    KEYCHAIN_CERTIFICATES="$ctk_hash" \
      "$KSA_CTK_SSH_KEYGEN" -q -w "$KSA_CTK_PROVIDER" -Y sign \
        -f "$key_path" -n "$SIGNATURE_NAMESPACE" "$message_path" || \
      return 1
  else
    ksa_with_ephemeral_touchid_agent sign "$pub_path" "$message_path" || return 1
  fi
  [ -f "${message_path}.sig" ] || return 1
}
