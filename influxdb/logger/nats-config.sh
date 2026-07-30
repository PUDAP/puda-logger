#!/bin/sh
# Shared fail-closed NATS configuration for Telegraf, stream init, and healthcheck.
# This file is sourced; do not enable xtrace because the caller may hold secrets.

nats_config_error() {
  printf '%s\n' "NATS configuration error: $1" >&2
  return 1
}

nats_reject_unsafe_text() {
  nats_value_name="$1"
  nats_value="$2"
  if [ -z "$nats_value" ] || printf '%s' "$nats_value" | LC_ALL=C grep -q '[[:cntrl:][:space:]"\\]'; then
    nats_config_error "$nats_value_name contains unsupported control, whitespace, quote, or backslash characters"
    return 1
  fi
}

nats_require_secret_file() {
  nats_file_name="$1"
  nats_file_path="$2"
  nats_reject_unsafe_text "$nats_file_name" "$nats_file_path" || return 1
  if [ -L "$nats_file_path" ]; then
    nats_config_error "$nats_file_name must not be a symlink"
    return 1
  fi
  if [ ! -f "$nats_file_path" ] || [ ! -r "$nats_file_path" ] || [ ! -s "$nats_file_path" ]; then
    nats_config_error "$nats_file_name must name a readable, non-empty regular file"
    return 1
  fi
  nats_file_mode=$(stat -c '%a' "$nats_file_path")
  case "$nats_file_mode" in
    *00) ;;
    *)
      nats_config_error "$nats_file_name must not be accessible by group or other users"
      return 1
      ;;
  esac
  if [ "$(stat -c '%u' "$nats_file_path")" != "$(id -u)" ]; then
    nats_config_error "$nats_file_name must be owned by the runtime user"
    return 1
  fi
}

nats_is_loopback_server() {
  nats_loopback_authority="${1#*://}"
  case "$nats_loopback_authority" in
    localhost|localhost:*|127.0.0.1|127.0.0.1:*|'[::1]'|'[::1]':*) return 0 ;;
    *) return 1 ;;
  esac
}

nats_validate_config() {
  : "${NATS_SERVERS:?NATS_SERVERS is required}"

  nats_reject_unsafe_text NATS_SERVERS "$NATS_SERVERS" || return 1
  case "$NATS_SERVERS" in
    ,*|*,|*,,*)
      nats_config_error "NATS_SERVERS must not contain empty entries"
      return 1
      ;;
  esac

  NATS_ALLOW_UNAUTHENTICATED_LOOPBACK="${NATS_ALLOW_UNAUTHENTICATED_LOOPBACK:-0}"
  case "$NATS_ALLOW_UNAUTHENTICATED_LOOPBACK" in
    0|1) ;;
    *)
      nats_config_error "NATS_ALLOW_UNAUTHENTICATED_LOOPBACK must be 0 or 1"
      return 1
      ;;
  esac

  NATS_CREDS_FILE="${NATS_CREDS_FILE:-}"
  NATS_TLS_CA_FILE="${NATS_TLS_CA_FILE:-}"
  NATS_TLS_CERT_FILE="${NATS_TLS_CERT_FILE:-}"
  NATS_TLS_KEY_FILE="${NATS_TLS_KEY_FILE:-}"
  NATS_TIMEOUT="${NATS_TIMEOUT:-5s}"
  nats_reject_unsafe_text NATS_TIMEOUT "$NATS_TIMEOUT" || return 1
  if ! printf '%s' "$NATS_TIMEOUT" | LC_ALL=C grep -Eq '^[1-9][0-9]*(ms|s|m)$'; then
    nats_config_error "NATS_TIMEOUT must be a positive duration in ms, s, or m"
    return 1
  fi

  nats_servers_toml=""
  nats_all_loopback=1
  nats_transport=""
  nats_old_ifs="$IFS"
  IFS=','
  for nats_server in $NATS_SERVERS; do
    case "$nats_server" in
      nats://*|ws://*) nats_server_transport="plain" ;;
      tls://*|wss://*) nats_server_transport="tls" ;;
      *)
        IFS="$nats_old_ifs"
        nats_config_error "NATS_SERVERS contains an unsupported URL scheme"
        return 1
        ;;
    esac

    nats_authority="${nats_server#*://}"
    case "$nats_authority" in
      ""|*/*|*'?'*|*'#'*)
        IFS="$nats_old_ifs"
        nats_config_error "NATS server URLs must contain only a non-empty authority"
        return 1
        ;;
      *@*)
        IFS="$nats_old_ifs"
        nats_config_error "NATS URL userinfo is not supported; use NATS_CREDS_FILE"
        return 1
        ;;
    esac

    if [ -n "$nats_transport" ] && [ "$nats_transport" != "$nats_server_transport" ]; then
      IFS="$nats_old_ifs"
      nats_config_error "NATS_SERVERS must not mix encrypted and unencrypted transports"
      return 1
    fi
    nats_transport="$nats_server_transport"

    if ! nats_is_loopback_server "$nats_server"; then
      nats_all_loopback=0
    fi
    nats_servers_toml="${nats_servers_toml}\"${nats_server}\","
  done
  IFS="$nats_old_ifs"

  if [ -z "$nats_servers_toml" ]; then
    nats_config_error "NATS_SERVERS must contain at least one server URL"
    return 1
  fi
  NATS_SERVERS_TOML="[${nats_servers_toml%,}]"

  if [ -n "$NATS_CREDS_FILE" ]; then
    nats_require_secret_file NATS_CREDS_FILE "$NATS_CREDS_FILE" || return 1
  elif [ "$NATS_ALLOW_UNAUTHENTICATED_LOOPBACK" != "1" ]; then
    nats_config_error "NATS credentials are required; unauthenticated access is test-only"
    return 1
  elif [ "$nats_all_loopback" != "1" ]; then
    nats_config_error "unauthenticated NATS test mode permits loopback servers only"
    return 1
  fi

  if { [ -n "$NATS_TLS_CERT_FILE" ] && [ -z "$NATS_TLS_KEY_FILE" ]; } || \
     { [ -z "$NATS_TLS_CERT_FILE" ] && [ -n "$NATS_TLS_KEY_FILE" ]; }; then
    nats_config_error "NATS TLS client certificate and key must be configured together"
    return 1
  fi

  for nats_tls_name in NATS_TLS_CA_FILE NATS_TLS_CERT_FILE NATS_TLS_KEY_FILE; do
    eval "nats_tls_path=\${$nats_tls_name}"
    if [ -n "$nats_tls_path" ]; then
      nats_require_secret_file "$nats_tls_name" "$nats_tls_path" || return 1
    fi
  done

  NATS_SECURE=false
  if [ "$nats_transport" = "tls" ] || [ -n "$NATS_TLS_CA_FILE$NATS_TLS_CERT_FILE$NATS_TLS_KEY_FILE" ]; then
    NATS_SECURE=true
  fi

  # The NATS CLI reads these variables. They contain only validated endpoints and
  # file paths; credential contents are never placed in argv, output, or the
  # rendered Telegraf configuration.
  NATS_URL="$NATS_SERVERS"
  NATS_CREDS="$NATS_CREDS_FILE"
  NATS_CA="$NATS_TLS_CA_FILE"
  NATS_CERT="$NATS_TLS_CERT_FILE"
  NATS_KEY="$NATS_TLS_KEY_FILE"

  export NATS_ALLOW_UNAUTHENTICATED_LOOPBACK NATS_CREDS_FILE
  export NATS_TLS_CA_FILE NATS_TLS_CERT_FILE NATS_TLS_KEY_FILE
  export NATS_TIMEOUT NATS_SERVERS_TOML NATS_SECURE
  export NATS_URL NATS_CREDS NATS_CA NATS_CERT NATS_KEY
}

nats_cli() {
  # Disable ambient CLI contexts so an undeclared local credential cannot turn a
  # broken deployment into a false success.
  nats --no-context "$@"
}
