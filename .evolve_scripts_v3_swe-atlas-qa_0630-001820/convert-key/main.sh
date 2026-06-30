#!/bin/bash
# Convert private key between PKCS#8 (-----BEGIN PRIVATE KEY-----) and PKCS#1 (-----BEGIN RSA PRIVATE KEY-----) formats.
# Usage: convert-key/main.sh <keyfile> [--to=pkcs1|pkcs8] [--output=FILE] [--check] [--in-place]
#   <keyfile>:      Path to the private key file
#   --to=pkcs1:     Convert to PKCS#1 format (default: auto-detect and flip)
#   --to=pkcs8:     Convert to PKCS#8 format (default: auto-detect and flip)
#   --output=FILE:  Write converted key to FILE (default: print to stdout)
#   --in-place:     Overwrite the original file with converted key
#   --check:        Show current format without converting

keyfile=""
target=""
output_file=""
in_place=false
check_only=false

for arg in "$@"; do
  case "$arg" in
    --to=*) target="${arg#*=}" ;;
    --output=*) output_file="${arg#*=}" ;;
    --in-place) in_place=true ;;
    --check) check_only=true ;;
    --help|-h)
      echo "Usage: convert-key/main.sh <keyfile> [--to=pkcs1|pkcs8] [--output=FILE] [--in-place] [--check]"
      echo "  Converts private key between PKCS#8 (-----BEGIN PRIVATE KEY-----)"
      echo "  and PKCS#1 (-----BEGIN RSA PRIVATE KEY-----) formats using openssl."
      exit 0
      ;;
    *) keyfile="$arg" ;;
  esac
done

if [ -z "$keyfile" ]; then
  echo "Error: keyfile argument required" >&2
  echo "Usage: convert-key/main.sh <keyfile> [--to=pkcs1|pkcs8] [--output=FILE] [--in-place] [--check]" >&2
  exit 1
fi

if [ ! -f "$keyfile" ]; then
  echo "Error: file '$keyfile' not found" >&2
  exit 1
fi

# Detect current format
first_line=$(head -1 "$keyfile")
case "$first_line" in
  "-----BEGIN PRIVATE KEY-----")
    current="pkcs8"
    ;;
  "-----BEGIN RSA PRIVATE KEY-----")
    current="pkcs1"
    ;;
  "-----BEGIN EC PRIVATE KEY-----")
    current="pkcs8"
    echo "Note: EC key detected (-----BEGIN EC PRIVATE KEY-----), treating as PKCS#8" >&2
    ;;
  *)
    echo "Error: unknown key format. First line: $first_line" >&2
    echo "Expected: -----BEGIN PRIVATE KEY----- or -----BEGIN RSA PRIVATE KEY-----" >&2
    exit 1
    ;;
esac

# If --check, just report format
if [ "$check_only" = true ]; then
  if [ "$current" = "pkcs8" ]; then
    echo "PKCS#8 (-----BEGIN PRIVATE KEY-----)"
  else
    echo "PKCS#1 (-----BEGIN RSA PRIVATE KEY-----)"
  fi
  exit 0
fi

# Determine target format
if [ -z "$target" ]; then
  # Auto-detect: flip to opposite format
  if [ "$current" = "pkcs8" ]; then
    target="pkcs1"
  else
    target="pkcs8"
  fi
fi

# Validate target
if [ "$target" != "pkcs1" ] && [ "$target" != "pkcs8" ]; then
  echo "Error: --to must be 'pkcs1' or 'pkcs8', got '$target'" >&2
  exit 1
fi

if [ "$current" = "$target" ]; then
  echo "Key is already in $target format, no conversion needed." >&2
  if [ -n "$output_file" ]; then
    cp "$keyfile" "$output_file"
    echo "Copied to $output_file" >&2
  fi
  exit 0
fi

# Perform conversion using openssl
tmpfile=$(mktemp /tmp/convert_key_XXXXXX.pem)
trap 'rm -f "$tmpfile"' EXIT

if [ "$current" = "pkcs8" ] && [ "$target" = "pkcs1" ]; then
  # PKCS#8 -> PKCS#1 (TraditionalOpenSSL format)
  openssl pkey -in "$keyfile" -traditional -out "$tmpfile" 2>/dev/null
  conv_rc=$?
elif [ "$current" = "pkcs1" ] && [ "$target" = "pkcs8" ]; then
  # PKCS#1 -> PKCS#8
  openssl pkey -in "$keyfile" -out "$tmpfile" 2>/dev/null
  conv_rc=$?
fi

if [ "$conv_rc" -ne 0 ]; then
  echo "Error: openssl conversion failed (rc=$conv_rc)" >&2
  # Try alternative method using Python cryptography if available
  python3 -c "
from cryptography.hazmat.primitives import serialization
with open('$keyfile', 'rb') as f:
    key_data = f.read()
key = serialization.load_pem_private_key(key_data, password=None)
if '$target' == 'pkcs1':
    fmt = serialization.PrivateFormat.TraditionalOpenSSL
else:
    fmt = serialization.PrivateFormat.PKCS8
pem = key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=fmt,
    encryption_algorithm=serialization.NoEncryption()
).decode()
with open('$tmpfile', 'w') as f:
    f.write(pem)
print('Converted to $target format')
" 2>/dev/null || {
    echo "Fallback with Python cryptography also failed." >&2
    exit 1
  }
fi

if [ "$in_place" = true ]; then
  cp "$tmpfile" "$keyfile"
  echo "Converted $keyfile to $target format (--in-place)" >&2
  echo "First line: $(head -1 "$keyfile")" >&2
elif [ -n "$output_file" ]; then
  cp "$tmpfile" "$output_file"
  echo "Converted to $target format, saved to $output_file" >&2
  echo "First line: $(head -1 "$output_file")" >&2
else
  cat "$tmpfile"
fi

exit 0
