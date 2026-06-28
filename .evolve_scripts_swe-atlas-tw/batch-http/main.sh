#!/usr/bin/env bash
set -euo pipefail

# batch-http: Make multiple HTTP requests in one call, collapsing 3+ curl calls into 1 step.
# Usage:
#   batch-http URL1 [URL2 ...]                  # show response body of each URL
#   batch-http --headers URL1 [URL2 ...]         # show response headers
#   batch-http --summary URL1 [URL2 ...]         # show status code + redirect summary
#   batch-http --headers --summary URL1 [URL2 ...]  # show headers then summary

MODE="body"  # body, headers, summary
URLS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --headers|-I)
      MODE="headers"
      shift
      ;;
    --summary|-s)
      MODE="summary"
      shift
      ;;
    -*)
      echo "Unknown option: $1" >&2
      echo "Usage: $0 [--headers] [--summary] <url1> [url2 ...]" >&2
      exit 1
      ;;
    *)
      URLS+=("$1")
      shift
      ;;
  esac
done

if [[ ${#URLS[@]} -eq 0 ]]; then
  echo "Usage: $0 [--headers] [--summary] <url1> [url2 ...]" >&2
  exit 1
fi

UA="Mozilla/5.0"
MAX_REDIR=5
TIMEOUT=15

for url in "${URLS[@]}"; do
  if [[ ${#URLS[@]} -gt 1 ]]; then
    echo "--- $url ---"
  fi
  case "$MODE" in
    headers)
      curl -s -I -L --max-redirs "$MAX_REDIR" --connect-timeout "$TIMEOUT" -H "User-Agent: $UA" "$url" 2>&1 | head -20 || echo "Error: Failed to fetch $url"
      ;;
    summary)
      curl -s -o /dev/null -w "HTTP %{http_code} | Final: %{url_effective} | Redirects: %{num_redirects} | Time: %{time_total}s\n" -L --max-redirs "$MAX_REDIR" --connect-timeout "$TIMEOUT" -H "User-Agent: $UA" "$url" 2>&1 || echo "Error: Failed to fetch $url"
      ;;
    body)
      curl -s -L --max-redirs "$MAX_REDIR" --connect-timeout "$TIMEOUT" -H "User-Agent: $UA" "$url" 2>&1 || echo "Error: Failed to fetch $url"
      ;;
  esac
done
