#!/bin/bash
# Make curl API calls with JSON/Form body, headers, cookie support, API key, and HTML pattern extraction, replacing repetitive curl chains.
# Usage: api-call/main.sh [--method=GET|POST|PUT|DELETE] <url> [--data=JSON] [--form-data=KEY=VALUE]... [--api-key=KEY] [--header=NAME:VALUE]... [--cookie-jar=FILE] [--cookie=FILE] [--output=FILE] [--extract=PATTERN] [--extract-all=PATTERN] [--status]
#   or:  api-call/main.sh [--method=GET] <url> [--api-key=KEY]
#   or:  echo '{"key":"val"}' | api-call/main.sh [--method=POST] <url> [--cookie-jar=cookies.txt]
#   or:  api-call/main.sh --method=POST <url> --form-data=csrf_token=TOKEN --form-data=email=user@test.com --form-data=password=secret --cookie-jar=cookies.txt --cookie=cookies.txt
#   --form-data=KEY=VALUE: Form field for URL-encoded POST (repeatable; auto-sets Content-Type: application/x-www-form-urlencoded)
#   --cookie-jar=FILE: Save response cookies to FILE (like curl -c)
#   --cookie=FILE: Send cookies from FILE (like curl -b)
#   --output=FILE: Save response body to FILE (for HTML pages)
#   --extract=PATTERN: Extract first match of grep -oP PATTERN from response body (for scraping)
#   --extract-all=PATTERN: Extract all matches of grep -oP PATTERN from response body
#   --status|-w: Show HTTP status code in stderr

method="GET"
url=""
data=""
api_key=""
headers=()
show_status=false
cookie_jar=""
cookie_file=""
output_file=""
extract_pattern=""
extract_all_pattern=""
form_fields=()
is_form=false

for arg in "$@"; do
  case "$arg" in
    --help|-h)
      echo "Usage: api-call/main.sh [--method=GET|POST|PUT|DELETE] <url> [--data=JSON] [--form-data=KEY=VALUE]... [--api-key=KEY] [--header=NAME:VALUE]... [--cookie-jar=FILE] [--cookie=FILE] [--output=FILE] [--extract=PATTERN] [--extract-all=PATTERN] [--status]"
      exit 0
      ;;
    --method=*) method="${arg#*=}" ;;
    --data=*) data="${arg#*=}" ;;
    --form-data=*) form_fields+=("${arg#*=}"); is_form=true ;;
    --api-key=*) api_key="${arg#*=}" ;;
    --header=*) headers+=("${arg#*=}") ;;
    --status|-w) show_status=true ;;
    --cookie-jar=*) cookie_jar="${arg#*=}" ;;
    --cookie=*) cookie_file="${arg#*=}" ;;
    --output=*) output_file="${arg#*=}" ;;
    --extract=*) extract_pattern="${arg#*=}" ;;
    --extract-all=*) extract_all_pattern="${arg#*=}" ;;
    *) url="$arg" ;;
  esac
done

if [ -z "$url" ]; then
  echo "Error: URL is required" >&2
  echo "Usage: api-call/main.sh [--method=GET|POST|PUT|DELETE] <url> [--data=JSON] [--form-data=KEY=VALUE]... [--api-key=KEY] [--header=NAME:VALUE]... [--cookie-jar=FILE] [--cookie=FILE] [--output=FILE] [--extract=PATTERN] [--status]" >&2
  exit 1
fi

# Build curl args
curl_args=(-s)

# Method
case "$method" in
  GET|get) curl_args+=(-X GET) ;;
  POST|post) curl_args+=(-X POST) ;;
  PUT|put) curl_args+=(-X PUT) ;;
  DELETE|delete) curl_args+=(-X DELETE) ;;
  *) curl_args+=(-X "$method") ;;
esac

# Headers
if [ "$is_form" = true ]; then
  curl_args+=(-H "Content-Type: application/x-www-form-urlencoded")
else
  curl_args+=(-H "Content-Type: application/json")
fi

for h in "${headers[@]}"; do
  curl_args+=(-H "$h")
done

# API key header
if [ -n "$api_key" ]; then
  curl_args+=(-H "Authentication: ${api_key}")
fi

# Cookie jar (save cookies)
if [ -n "$cookie_jar" ]; then
  curl_args+=(-c "$cookie_jar")
fi

# Cookie file (send cookies)
if [ -n "$cookie_file" ]; then
  curl_args+=(-b "$cookie_file")
fi

# Build data args for form or json
if [ "$is_form" = true ]; then
  for field in "${form_fields[@]}"; do
    curl_args+=(--data-urlencode "$field")
  done
elif [ -n "$data" ]; then
  curl_args+=(-d "$data")
elif [ ! -t 0 ]; then
  stdin_data=$(cat)
  if [ -n "$stdin_data" ]; then
    curl_args+=(-d "$stdin_data")
  fi
fi

# Execute
if [ -n "$output_file" ]; then
  if [ "$show_status" = true ]; then
    status=$(curl "${curl_args[@]}" -o "$output_file" -w "%{http_code}" "$url" 2>&1)
    echo "Saved to $output_file"
    echo "HTTP Status: $status" >&2
  else
    curl "${curl_args[@]}" -o "$output_file" "$url" 2>&1
    echo "Saved to $output_file"
  fi
elif [ -n "$extract_pattern" ] || [ -n "$extract_all_pattern" ]; then
  response=$(curl "${curl_args[@]}" "$url" 2>&1)
  if [ -n "$extract_pattern" ]; then
    echo "$response" | grep -oP "$extract_pattern" 2>/dev/null | head -1 || echo "(no match)"
  fi
  if [ -n "$extract_all_pattern" ]; then
    echo "$response" | grep -oP "$extract_all_pattern" 2>/dev/null || echo "(no matches)"
  fi
  if [ "$show_status" = true ]; then
    status=$(curl -s -o /dev/null -w "%{http_code}" "${curl_args[@]}" "$url" 2>&1)
    echo "HTTP Status: $status" >&2
  fi
elif [ "$show_status" = true ]; then
  output=$(curl "${curl_args[@]}" -w "
__HTTP_STATUS__:%{http_code}" "$url" 2>&1)
  body="${output%__HTTP_STATUS__:*}"
  status="${output##*__HTTP_STATUS__:}"
  echo -n "$body"
  echo "HTTP Status: $status" >&2
else
  curl "${curl_args[@]}" "$url" 2>&1
fi
