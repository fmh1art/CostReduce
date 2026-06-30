#!/bin/bash
# Check listening ports and running processes, consolidating ss + netstat + ps aux chains
# Usage: check-port/main.sh [--grep=KEYWORD]... [--ports] [--processes] [--numeric]
#   --grep=KEYWORD: Filter results by keyword (repeatable); shows only matching entries
#   --ports:        Show listening ports only (default: show all)
#   --processes:    Show running processes only (default: show all)
#   --numeric:      Show numeric addresses/ports
grep_keywords=()
show_ports=true
show_processes=true
numeric_flag=""
for arg in "$@"; do
  case "$arg" in
    --grep=*) grep_keywords+=("${arg#*=}") ;;
    --ports) show_processes=false ;;
    --processes) show_ports=false ;;
    --numeric|-n) numeric_flag="-n" ;;
  esac
done
# Build grep filter pipe
filter_results() {
  if [ ${#grep_keywords[@]} -gt 0 ]; then
    local cmd="cat"
    for kw in "${grep_keywords[@]}"; do
      cmd="$cmd | grep -i -- \"$kw\""
    done
    eval "$cmd"
  else
    cat
  fi
}
print_header() {
  echo ""
  echo "=== $1 ==="
  echo ""
}
# ---- Check listening ports ----
if [ "$show_ports" = true ]; then
  print_header "Listening Ports"
  
  # Try ss first (modern systems)
  if command -v ss >/dev/null 2>&1; then
    if [ ${#grep_keywords[@]} -gt 0 ]; then
      ss $numeric_flag -tlnp 2>/dev/null | filter_results
      ss $numeric_flag -ulnp 2>/dev/null | filter_results
    else
      ss $numeric_flag -tlnp 2>/dev/null
      ss $numeric_flag -ulnp 2>/dev/null
    fi
  # Fall back to netstat
  elif command -v netstat >/dev/null 2>&1; then
    if [ ${#grep_keywords[@]} -gt 0 ]; then
      netstat $numeric_flag -tlnp 2>/dev/null | filter_results
      netstat $numeric_flag -ulnp 2>/dev/null | filter_results
    else
      netstat $numeric_flag -tlnp 2>/dev/null
      netstat $numeric_flag -ulnp 2>/dev/null
    fi
  # Last resort: read /proc/net/tcp
  elif [ -f /proc/net/tcp ]; then
    echo "[ss and netstat not available, showing /proc/net/tcp]"
    if [ ${#grep_keywords[@]} -gt 0 ]; then
      cat /proc/net/tcp 2>/dev/null | filter_results
    else
      cat /proc/net/tcp 2>/dev/null
    fi
  else
    echo "(No port listing tools available)"
  fi
  
  # When grep keywords are used, also check /proc/net/tcp for additional matches
  # This catches services listening on non-standard ports not shown by ss/netstat
  if [ ${#grep_keywords[@]} -gt 0 ] && [ -f /proc/net/tcp ]; then
    proc_ports=$(cat /proc/net/tcp 2>/dev/null | awk '{print $2}' | cut -d: -f2 | sort -u)
    if [ -n "$proc_ports" ]; then
      for kw in "${grep_keywords[@]}"; do
        # Check if any running process matches the keyword and has an open socket
        if ps aux 2>/dev/null | grep -i -- "$kw" | grep -v grep | grep -q .; then
          # Check if /proc/net/tcp shows any listening port that might relate to this service
          listening_count=$(cat /proc/net/tcp 2>/dev/null | grep -c "0A")
          if [ "$listening_count" -eq 0 ]; then
            echo "(Service matching '$kw' is running but may be listening on Unix socket only)"
          fi
        fi
      done
    fi
  fi
fi
# ---- Check running processes ----
if [ "$show_processes" = true ]; then
  print_header "Running Processes"
  
  if command -v ps >/dev/null 2>&1; then
    if [ ${#grep_keywords[@]} -gt 0 ]; then
      # Use a single ps aux and filter
      ps aux 2>/dev/null | filter_results | grep -v "grep -i" || true
    else
      ps aux 2>/dev/null
    fi
  else
    echo "(ps not available)"
    if [ -d /proc ]; then
      ls /proc/ 2>/dev/null | grep -E '^[0-9]+$' | head -20 || true
    fi
  fi
fi
echo ""
