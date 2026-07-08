#!/usr/bin/env python
"""changeset.py — print the current pending-diff fingerprint.

The reviewer runs this and records the output as `changeset` in its integration
review verdict. commit-gate.py computes the same value and only opens the gate
when they match — so a review authorizes exactly the diff it saw, nothing later.

    python .claude/scripts/changeset.py [repo_root]
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import harness_lib as lib  # noqa: E402

root = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.getcwd()
cs = lib.git_changeset(root)
print(cs if cs else "no-git")
