#!/usr/bin/env bash
# Store the jobs' secrets in SSM Parameter Store as SecureStrings (free, standard tier), under /nfl-alt-lines/.
# Values come from .env, except DATABASE_URL, which is asked for: the laptop's .env points at local SQLite.
#
# No value is ever printed or put on a command line (where `ps` and the shell history would show it): each
# parameter goes to the AWS CLI as JSON in a temporary file only this user can read, deleted on exit (the CLI
# cannot read --cli-input-json from a pipe). Only names are printed.
#
#   AWS_PROFILE=<admin profile> infra/scripts/put_parameters.sh [path/to/.env]
#
# Re-running it overwrites the parameters, which is how a key is rotated.
set -euo pipefail

env_file="${1:-$(dirname "$0")/../../.env}"
prefix="/nfl-alt-lines"
region="us-east-2"
required=(ODDS_API_KEY SMTP_USER SMTP_APP_PASSWORD)
optional=(BANKROLL REPORT_EMAIL_BETS_TO REPORT_EMAIL_TO REPORT_SUBJECT_PREFIX) # infra/live: optional_report_settings

# NAME=value from the env file, split at the first `=` like nfl_alt_lines/common/settings.py; empty if absent.
value_of() {
  local line
  line="$(grep -E "^[[:space:]]*$1[[:space:]]*=" "$env_file" | tail -n 1 || true)"
  line="${line#*=}"
  printf '%s' "${line#"${line%%[![:space:]]*}"}" | sed -e 's/[[:space:]]*$//'
}

umask 077
request="$(mktemp)"
trap 'rm -f "$request"' EXIT

put() {
  jq -n --arg name "$prefix/$1" --arg value "$2" \
    '{Name: $name, Value: $value, Type: "SecureString", Overwrite: true, Tier: "Standard"}' >"$request"
  aws ssm put-parameter --region "$region" --cli-input-json "file://$request" >/dev/null
  : >"$request"
  echo "stored $1"
}

[[ -f "$env_file" ]] || { echo "no env file at $env_file" >&2; exit 1; }

for name in "${required[@]}"; do
  value="$(value_of "$name")"
  [[ -n "$value" ]] || { echo "$name is missing from $env_file" >&2; exit 1; }
  put "$name" "$value"
done

set_optional=()
for name in "${optional[@]}"; do
  value="$(value_of "$name")"
  if [[ -n "$value" ]]; then
    put "$name" "$value"
    set_optional+=("$name")
  fi
done

read -r -s -p "Cloud DATABASE_URL (postgresql+psycopg://...; not shown): " database_url
echo
[[ "$database_url" == postgresql://* || "$database_url" == postgresql+psycopg://* ]] ||
  { echo "DATABASE_URL must start with postgresql+psycopg://" >&2; exit 1; }
put DATABASE_URL "$database_url"

echo
echo "optional_report_settings = [$(printf '"%s", ' "${set_optional[@]}" | sed 's/, $//')]"
echo "(put that line in infra/live/terraform.tfvars if it differs from the default: all four)"
