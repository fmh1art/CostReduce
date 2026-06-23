#!/usr/bin/env python3
"""
Multi-replace tool - Perform multiple string replacements in a file in one pass.

Usage:
  python3 replace.py <file> <old1> <new1> [old2 new2] ...
  python3 replace.py <file> --pairs old1 new1 old2 new2 ...
  python3 replace.py <file> -f <script.py>    # Run a custom Python script

Each old/new pair is applied sequentially. This is more efficient than
running file_patch multiple times for different replacements in the same file.

Examples:
  python3 replace.py file.go "l.fill(" "l.frame.fill(l.selector, " "l.click(" "l.frame.click(l.selector, "
  python3 replace.py file.go --pairs "old1" "new1" "old2" "new2"
"""

import sys
import os


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    filepath = sys.argv[1]
    
    if not os.path.exists(filepath):
        print(f"ERROR: File not found: {filepath}")
        sys.exit(1)

    if sys.argv[2] == '-f':
        # Run a custom Python script for complex transformations
        script = sys.argv[3]
        if not os.path.exists(script):
            print(f"ERROR: Script not found: {script}")
            sys.exit(1)
        
        # Read the file content
        with open(filepath, 'r') as f:
            content = f.read()
        
        # Execute the script with the content as 'content' variable
        namespace = {'content': content, 'filepath': filepath}
        exec(open(script).read(), namespace)
        
        new_content = namespace.get('content', content)
        
        if new_content != content:
            with open(filepath, 'w') as f:
                f.write(new_content)
            print(f"Updated {filepath}")
        else:
            print(f"No changes made to {filepath}")
        return

    # Parse old/new pairs
    args = sys.argv[2:]
    
    if args[0] == '--pairs':
        args = args[1:]
    
    if len(args) < 2 or len(args) % 2 != 0:
        print("ERROR: Must provide pairs of old_text new_text")
        print(__doc__)
        sys.exit(1)
    
    # Read file
    with open(filepath, 'r') as f:
        content = f.read()
    
    original = content
    pairs = []
    for i in range(0, len(args), 2):
        old_text = args[i]
        new_text = args[i+1]
        pairs.append((old_text, new_text))
    
    # Apply all replacements
    for old_text, new_text in pairs:
        if old_text not in content:
            print(f"WARNING: Pattern not found: {old_text[:60]}...")
            continue
        count = content.count(old_text)
        content = content.replace(old_text, new_text)
        print(f"  Replaced '{old_text[:50]}...' -> '{new_text[:50]}...' ({count} occurrence(s))")
    
    if content != original:
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"Updated {filepath}")
    else:
        print(f"No changes made to {filepath}")


if __name__ == '__main__':
    main()
