#!/usr/bin/env python3
"""
Safely load environment variables from a .env file.
Usage: python3 load_env.py <env_file_path>
Reads key=value pairs, handles comments (#), empty lines, and quoted values.
Sets variables in os.environ.
Prints the count of loaded variables.
"""
import os
import sys


def load_env_file(filepath):
    loaded = 0
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                continue
            key, _, val = line.partition('=')
            key = key.strip()
            val = val.strip()
            # Remove surrounding quotes if present
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                val = val[1:-1]
            os.environ[key] = val
            loaded += 1
    return loaded


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: load_env.py <env_file_path>", file=sys.stderr)
        sys.exit(1)
    filepath = sys.argv[1]
    if not os.path.isfile(filepath):
        print(f"Error: File '{filepath}' not found", file=sys.stderr)
        sys.exit(1)
    count = load_env_file(filepath)
    print(f"[Loaded {count} env vars from {os.path.basename(filepath)}]")
