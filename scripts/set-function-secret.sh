#!/bin/sh
# Set one secret environment variable on the deployed watcher function.
#
# Usage:
#   sh scripts/set-function-secret.sh <function-id> <VARIABLE_NAME> [region]
#
# The value is read from the terminal without echo. It is sent directly to the
# Scaleway API with your local `scw` credentials and never passes through Git,
# GitHub or the Terraform state.
#
# Note: the value briefly appears in the `scw` process arguments on this
# machine. Run this only on a trusted single-user workstation.
set -eu

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  echo "usage: $0 <function-id> <VARIABLE_NAME> [region]" >&2
  exit 2
fi

function_id=$1
name=$2
region=${3:-fr-par}

case "$name" in
  *[!A-Z0-9_]* | [!A-Z]*)
    echo "error: variable name must match ^[A-Z][A-Z0-9_]*$" >&2
    exit 2
    ;;
esac

command -v scw >/dev/null 2>&1 || { echo "error: scw CLI not found" >&2; exit 2; }

printf 'Value for %s: ' "$name" >&2
stty -echo
trap 'stty echo' EXIT INT TERM
IFS= read -r value
stty echo
trap - EXIT INT TERM
printf '\n' >&2

if [ -z "$value" ]; then
  echo "error: empty value" >&2
  exit 2
fi

scw function function update "$function_id" \
  region="$region" \
  "secret-environment-variables.0.key=$name" \
  "secret-environment-variables.0.value=$value" \
  redeploy=true >/dev/null

unset value
echo "Secret $name updated on function $function_id." >&2
