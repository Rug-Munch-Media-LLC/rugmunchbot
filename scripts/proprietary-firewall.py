#!/usr/bin/env python3
"""
Pre-commit firewall — blocks proprietary info from public repos.
Runs before every commit in rmi-backend, rmi-frontend, pryscraper,
walletpress, degenfeed-web, rugmunchbot.

WHAT IT BLOCKS:
- References to private repos (standards, fleet-infra, fleet-template, rmi-security-framework)
- Internal architecture docs (DISPATCH-MATRIX, PAYMENT-MODEL, SECURITY-FRAMEWORK)
- Agent definitions and fleet configuration
- Internal IPs and hostnames (100.104.x.x, 100.64.x.x, 100.74.x.x)
- DDG MCP server code
- Competitor teardown references (/tmp/compete/)
- Payment model and cost structure
- Private API keys and tokens (gopass references)
"""

import os
import re
import subprocess
import sys

BLOCKED_PATTERNS = [
    # Private repo names
    (
        r"\bstandards\b.*(?:DISPATCH|PAYMENT|SECURITY.FRAMEWORK|agent.fleet)",
        "PRIVATE-REPO-REFERENCE",
    ),
    (r"\bfleet-infra\b", "PRIVATE-REPO-REFERENCE"),
    (r"\bfleet-template\b", "PRIVATE-REPO-REFERENCE"),
    (r"\brmi-security-framework\b", "PRIVATE-REPO-REFERENCE"),
    # Internal docs
    (r"DISPATCH[-_]MATRIX", "PRIVATE-DOC-REFERENCE"),
    (r"PAYMENT[-_]MODEL", "PRIVATE-DOC-REFERENCE"),
    (r"SECURITY[-_]FRAMEWORK", "PRIVATE-DOC-REFERENCE"),
    (r"AGENTS\.md.*dispatch", "PRIVATE-DOC-REFERENCE"),
    # Internal IPs
    (r"100\.104\.\d+\.\d+", "INTERNAL-IP"),
    (r"100\.64\.\d+\.\d+", "INTERNAL-IP"),
    (r"100\.74\.\d+\.\d+", "INTERNAL-IP"),
    (r"talos\.rugmunch\.io", "INTERNAL-HOSTNAME"),
    (r"cinnabox", "INTERNAL-HOSTNAME"),
    # Competitor intel
    (r"/tmp/compete/", "COMPETITOR-INTEL"),
    (r"competitor.teardown", "COMPETITOR-INTEL"),
    # Payment/cost
    (r"\$0\.0[0-9]+/M.*(?:input|token)", "PAYMENT-MODEL"),
    (r"openrouter.*\$[0-9]", "PAYMENT-MODEL"),
    (r"6.free.layers", "PAYMENT-MODEL"),
    # DDG MCP
    (r"ddg.search.server", "PRIVATE-MCP"),
    (r"ddg-search-server\.py", "PRIVATE-MCP"),
    # Gopass/secrets
    (r"gopass.*show", "SECRET-REFERENCE"),
    (r"rmi/(?:codeberg|gitlab|forgejo|github)/", "SECRET-REFERENCE"),
]


def check_file(filepath):
    """Check a single file for blocked patterns."""
    findings = []
    try:
        with open(filepath, errors="ignore") as f:
            for lineno, line in enumerate(f, 1):
                for pattern, rule in BLOCKED_PATTERNS:
                    if re.search(pattern, line, re.IGNORECASE):
                        findings.append(
                            f"  {filepath}:{lineno} [{rule}] {line.strip()[:80]}"
                        )
    except Exception:
        pass
    return findings


def main():
    # Get staged files
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], capture_output=True, text=True
    )
    staged = result.stdout.strip().split("\n")
    staged = [f for f in staged if f and os.path.exists(f)]

    all_findings = []
    for f in staged:
        # Skip binary files
        if any(
            f.endswith(ext)
            for ext in [".png", ".jpg", ".gif", ".woff", ".ttf", ".ico", ".svg"]
        ):
            continue
        all_findings.extend(check_file(f))

    if all_findings:
        print("\n🛡️  PROPRIETARY FIREWALL — BLOCKED:\n")
        for f in all_findings:
            print(f)
        print(f"\n{len(all_findings)} violation(s) found. Commit blocked.")
        print(
            "This content belongs in private repos (standards, fleet-infra) — not public."
        )
        sys.exit(1)

    print("🛡️  Firewall: CLEAN — no proprietary content detected.")
    sys.exit(0)


if __name__ == "__main__":
    main()
