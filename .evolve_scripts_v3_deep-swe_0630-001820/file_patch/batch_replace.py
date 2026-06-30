#!/usr/bin/env python3
"""
Batch-replace: read multiple old/new pairs from stdin and apply all
replacements in a single read-write cycle.

Stdin format:
  OLD CONTENT 1
  ---
  NEW CONTENT 1
  ===  (or EOF)
  OLD CONTENT 2
  ---
  NEW CONTENT 2
  ===  (or EOF)

Usage: batch_replace.py <filepath> <mode:first|all>
"""
import sys

def main():
    if len(sys.argv) < 2:
        print("Usage: batch_replace.py <filepath> [mode]", file=sys.stderr)
        sys.exit(1)

    filepath = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "first"

    stdin_data = sys.stdin.read()

    # Parse stdin: pairs separated by ===, old/new within a pair separated by ---
    pairs = []
    current_side = "old"
    current_text = []
    current_old = ""

    for line in stdin_data.split("\n"):
        if line.strip() == "===" and current_side == "new":
            new_text = "\n".join(current_text)
            if new_text.endswith("\n"):
                new_text = new_text[:-1]
            if current_old:
                pairs.append((current_old, new_text))
            current_old = ""
            current_text = []
            current_side = "old"
        elif line.strip() == "---":
            if current_side == "old":
                current_old = "\n".join(current_text)
                if current_old.endswith("\n"):
                    current_old = current_old[:-1]
                current_text = []
                current_side = "new"
            else:
                current_text.append(line)
        else:
            current_text.append(line)

    # Handle last pair
    if current_text:
        if current_side == "old":
            current_old = "\n".join(current_text)
            if current_old.endswith("\n"):
                current_old = current_old[:-1]
        elif current_side == "new":
            new_text = "\n".join(current_text)
            if new_text.endswith("\n"):
                new_text = new_text[:-1]
            if current_old:
                pairs.append((current_old, new_text))

    if not pairs:
        print("Error: No replacement pairs found in stdin", file=sys.stderr)
        sys.exit(1)

    with open(filepath, "r") as f:
        content = f.read()

    replacements_done = 0
    for old_text, new_text in pairs:
        if old_text in content:
            if mode == "first":
                content = content.replace(old_text, new_text, 1)
            else:
                content = content.replace(old_text, new_text)
            replacements_done += 1
        else:
            print(f"Warning: Old content not found (skipped)", file=sys.stderr)

    with open(filepath, "w") as f:
        f.write(content)

    print(f"Applied {replacements_done} replacement(s) to {filepath}")


if __name__ == "__main__":
    main()
