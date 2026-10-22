#!/usr/bin/env bash
# Read Docker's allocated loopback port after a disposable service starts.
# Requires the caller's project-scoped compose() function. Never probe, reserve,
# kill or reuse a host listener: Docker alone allocates the port atomically.
integration_endpoint() {
  local mapping port
  mapping=$(compose port "$1" "$2") || return 1
  [[ $mapping =~ ^127\.0\.0\.1:([0-9]{1,5})$ ]] || return 1
  port=${BASH_REMATCH[1]}
  (( 10#$port > 0 && 10#$port <= 65535 )) || return 1
  printf 'http://%s\n' "$mapping"
}
