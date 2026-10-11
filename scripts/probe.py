#!/usr/bin/env python3
"""Read-only project discovery and doc/code reconciliation for the completeness audit.

    python probe.py [PROJECT_DIR] [--json]

Never executes project code, never writes, never uses the network. It reports
facts (stack, manifests, entry points, commands found in source, commands
claimed in docs, broken relative links, Git branch/worktree state) so the
audit's LLM reasoning is spent only on semantics.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", "dist", "build", "target", "__pycache__", ".venv", "venv", ".tox", ".claude"}
DOC_EXT = {".md", ".rst", ".txt", ".mdx"}
MANIFESTS = {
    "pyproject.toml": "python", "setup.py": "python", "setup.cfg": "python", "requirements.txt": "python",
    "package.json": "node", "go.mod": "go", "Cargo.toml": "rust", "pom.xml": "java",
    "build.gradle": "java", "Gemfile": "ruby", "composer.json": "php", "Dockerfile": "docker",
    "docker-compose.yml": "docker", "compose.yaml": "docker", "Makefile": "make", "plugin.json": "claude-plugin",
}
MAX_FILES = 20000
MAX_READ = 400_000


def walk(root: Path):
    n = 0
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for f in fns:
            n += 1
            if n > MAX_FILES:
                return
            yield Path(dp) / f


def read(p: Path) -> str:
    try:
        if p.stat().st_size > MAX_READ:
            return ""
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def git(root: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=30, check=False)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def entry_points(root: Path) -> dict:
    names: set[str] = set()
    pp = read(root / "pyproject.toml")
    m = re.search(r"\[project\.scripts\]\s*\n((?:[^\[\n].*\n?)*)", pp)
    if m:
        names |= set(re.findall(r"^\s*([\w.-]+)\s*=", m.group(1), re.M))
    names |= set(re.findall(r"^\s*([\w.-]+)\s*=\s*\"?[\w.]+:[\w]+", read(root / "setup.cfg"), re.M))
    try:
        pj = json.loads(read(root / "package.json") or "{}")
        b = pj.get("bin")
        if isinstance(b, dict):
            names |= set(b)
        elif isinstance(b, str) and pj.get("name"):
            names.add(pj["name"].split("/")[-1])
    except ValueError:
        pass
    return {"binaries": sorted(names)}


SUB_PATTERNS = [
    re.compile(r"add_parser\(\s*[\"']([\w:-]+)[\"']"),                         # argparse
    re.compile(r"@\w+\.command\(\s*(?:name\s*=\s*)?[\"']([\w:-]+)[\"']"),      # click/typer named
    re.compile(r"app\.command\(\s*\)\s*\n\s*def\s+(\w+)"),                     # typer implicit
    re.compile(r"@(?:click\.)?command\(\s*[\"']([\w:-]+)[\"']"),
    re.compile(r"Use:\s*\"([\w-]+)"),                                          # cobra
    re.compile(r"\.command\(\s*[\"']([\w:-]+)"),                               # commander.js
    re.compile(r"case\s+[\"']([\w-]+)[\"']\s*:"),                              # switch dispatch
]


def source_commands(root: Path) -> set[str]:
    found: set[str] = set()
    for p in walk(root):
        if p.suffix not in {".py", ".js", ".ts", ".mjs", ".go", ".rs", ".sh"}:
            continue
        rel = p.relative_to(root).as_posix().lower()
        if "test" in rel or "/docs/" in rel:
            continue
        text = read(p)
        for pat in SUB_PATTERNS:
            found |= set(pat.findall(text))
        if p.suffix == ".py":
            for fn in re.findall(r"@\w+\.command\(\s*\)\s*\n(?:@.*\n)*\s*def\s+(\w+)", text):
                found.add(fn.replace("_", "-"))
    return {c for c in found if len(c) > 1}


def doc_files(root: Path) -> list[Path]:
    return [p for p in walk(root) if p.suffix.lower() in DOC_EXT]


def claimed_commands(root: Path, binaries: list[str]) -> dict[str, list[str]]:
    """{binary: [subcommands the docs show]} from code spans / fenced blocks."""
    claims: dict[str, set[str]] = {}
    for b in binaries:
        pat = re.compile(rf"(?:^|[\s`$>])(?:python -m )?{re.escape(b)}\s+([a-z][\w:-]+)")
        for d in doc_files(root):
            rel = d.relative_to(root).as_posix().lower()
            if "changelog" in rel or "history" in rel or "/archive" in rel:
                continue  # historical text may name removed commands
            for line in read(d).splitlines():
                if "`" in line or line.startswith(("    ", "$", ">")) or line.lstrip().startswith(b):
                    for sub in pat.findall(line):
                        claims.setdefault(b, set()).add(sub)
    return {b: sorted(s) for b, s in claims.items()}


LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def broken_links(root: Path) -> list[str]:
    bad: list[str] = []
    for d in doc_files(root):
        if d.suffix.lower() not in {".md", ".mdx"}:
            continue
        for target in LINK_RE.findall(read(d)):
            if re.match(r"^(https?:|mailto:|#|/|data:)", target):
                continue
            path = target.split("#")[0].split("?")[0]
            if path and not (d.parent / path).exists():
                bad.append(f"{d.relative_to(root).as_posix()} -> {target}")
    return bad[:100]


def versions(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    m = re.search(r"^version\s*=\s*\"([^\"]+)\"", read(root / "pyproject.toml"), re.M)
    if m:
        out["pyproject.toml"] = m.group(1)
    try:
        v = json.loads(read(root / "package.json") or "{}").get("version")
        if v:
            out["package.json"] = v
    except ValueError:
        pass
    try:
        v = json.loads(read(root / ".claude-plugin" / "plugin.json") or "{}").get("version")
        if v:
            out["plugin.json"] = v
    except ValueError:
        pass
    m = re.search(r"^##\s*\[?v?(\d+\.\d+\.\d+[\w.+-]*)", read(root / "CHANGELOG.md"), re.M)
    if m:
        out["CHANGELOG.md (latest)"] = m.group(1)
    return out


def git_state(root: Path) -> dict:
    branches = git(root, "for-each-ref", "--format=%(refname:short)|%(upstream:track)|%(committerdate:short)", "refs/heads").splitlines()
    default = (git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").strip().replace("origin/", "")
               or ("main" if any(b.startswith("main|") for b in branches) else "master"))
    unmerged = [b.split("|")[0] for b in git(root, "branch", "--no-merged", default).split("\n") if b.strip()] if default else []
    wts = [ln[9:] for ln in git(root, "worktree", "list", "--porcelain").splitlines() if ln.startswith("worktree ")]
    return {
        "head": git(root, "rev-parse", "--short", "HEAD").strip(),
        "branch": git(root, "branch", "--show-current").strip(),
        "default_branch": default,
        "branches": [b.split("|")[0] for b in branches],
        "unmerged_into_default": [u.strip().lstrip("* ") for u in unmerged],
        "worktrees": len(wts),
        "dirty": bool(git(root, "status", "--porcelain").strip()),
        "remotes": git(root, "remote").split(),
    }


def probe(root: Path) -> dict:
    stack = sorted({v for f, v in MANIFESTS.items() if (root / f).exists()})
    ep = entry_points(root)
    src = source_commands(root)
    claims = claimed_commands(root, ep["binaries"])
    undocumented, phantom = {}, {}
    all_docs = "\n".join(read(d) for d in doc_files(root))
    for b in ep["binaries"]:
        undocumented[b] = sorted(c for c in src if not re.search(rf"\b{re.escape(c)}\b", all_docs))
        phantom[b] = sorted(c for c in claims.get(b, []) if c not in src and c not in {"--help", "help"})
    present = lambda *n: any((root / x).exists() for x in n)  # noqa: E731
    return {
        "root": str(root),
        "stack": stack,
        "entry_points": ep,
        "commands_in_source": sorted(src),
        "commands_claimed_in_docs": claims,
        "source_commands_not_mentioned_in_any_doc": undocumented,
        "doc_claims_without_source": phantom,
        "broken_relative_links": broken_links(root),
        "versions": versions(root),
        "has": {
            "tests": present("tests", "test", "__tests__", "spec"),
            "ci": present(".github/workflows", ".gitlab-ci.yml", ".circleci"),
            "changelog": present("CHANGELOG.md", "CHANGELOG"),
            "license": present("LICENSE", "LICENSE.md", "COPYING"),
            "readme": present("README.md", "README.rst", "README"),
            "docker": present("Dockerfile", "docker-compose.yml", "compose.yaml"),
        },
        "git": git_state(root),
        "limits": "heuristic: regex discovery, no code executed; confirm findings by running the real interface",
    }


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    root = Path(args[0]).resolve() if args else Path.cwd()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    print(json.dumps(probe(root), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
