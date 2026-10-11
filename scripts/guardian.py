#!/usr/bin/env python3
"""project-completion-guardian: deterministic milestone detection and anti-loop state.

Subcommands
  session-start   hook: record the session baseline snapshot (no output)
  stop            hook: decide whether to ask for a completeness audit
  mark            record that the CURRENT project state was audited
  status          print the guardian state for this project
  config          print the effective per-project configuration as JSON
  reset           forget the stored state for this project

Standard library only. Read-only with respect to the project: the only writes
are the state file outside the repository and (for `mark`) nothing else.
"""
from __future__ import annotations

import argparse
import difflib
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

VERSION = "0.1.0"
PLUGIN = "project-completion-guardian"
MAX_SNAPSHOT_FILES = 50_000
MAX_TEXT_BYTES = 256 * 1024
MAX_STORED_BLOBS = 200
KEEP_FINGERPRINTS = 25

DEFAULT_CONFIG = {
    "enabled": True,
    "audit_level": "balanced",
    "final_requires_clean_ci": True,
    "check_public_surface": True,
    "check_deployment": False,
    "external_auditors": [],
}

# (source churn, multi-file churn, surface churn, docs churn)
THRESHOLDS = {
    "relaxed": (120, 60, 8, 80),
    "balanced": (60, 30, 3, 40),
    "strict": (25, 12, 1, 15),
}

NOISE_GLOBS = [
    ".git/*", "node_modules/*", "*/node_modules/*", "dist/*", "build/*", "target/*",
    "__pycache__/*", "*/__pycache__/*", "*.pyc", ".venv/*", "venv/*", ".tox/*", ".idea/*",
    ".vscode/*", ".claude/*", "*.local.md", "*.log", "*.lock", "package-lock.json",
    "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock", "go.sum", "uv.lock",
    "*.min.js", "*.min.css", "*.map", "coverage/*", ".coverage", "*.egg-info/*",
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.ico", "*.svg", "*.pdf", "*.zip", "*.gz",
    "*.woff", "*.woff2", "*.ttf", ".DS_Store",
]
CODE_EXT = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt",
    ".rb", ".php", ".cs", ".c", ".cc", ".cpp", ".h", ".hpp", ".swift", ".scala", ".sh",
    ".ps1", ".lua", ".dart", ".ex", ".exs", ".vue", ".svelte", ".sql",
}
DOC_EXT = {".md", ".rst", ".txt", ".adoc", ".mdx"}
SURFACE_NAMES = {
    "package.json", "pyproject.toml", "setup.py", "setup.cfg", "cargo.toml", "go.mod",
    "dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yaml", "plugin.json",
    "marketplace.json", "action.yml", "action.yaml", "openapi.json", "openapi.yaml",
    "pom.xml", "build.gradle", "makefile", "mkdocs.yml", "manifest.json",
}
SURFACE_PARTS = {
    "cli", "cmd", "commands", "command", "routes", "route", "api", "endpoints", "handlers",
    "controllers", "bin", "mcp", "migrations", "workflows", "hooks",
}
SURFACE_SIG = re.compile(
    r"add_parser\(|add_argument\(|@\w+\.(command|group|route|get|post|put|delete|patch|tool)\b|"
    r"\.command\(|\bapp\.(get|post|put|delete|use)\(|\bcase\s+[\"'][\w-]+[\"']\s*:|"
    r"Use:\s*\"|\bgetenv\(|^\s*def\s+main\b|^\s*(pub\s+)?fn\s+main\b|\"bin\"|\[project\.scripts\]"
)
RELEASE_NAMES = {"changelog.md", "changelog", "history.md", "releases.md"}
FINAL_WORDS = re.compile(
    r"\b(final(ly|ize|ise|ized)?|ship(ping)?|release|ready to (merge|ship|release)|"
    r"wrap(ping)? up|project (is )?(complete|done|finished)|feature[- ]complete|go live)\b",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- git

def git(args: list[str], cwd: str, text: bool = True, inp: str | None = None):
    try:
        p = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=text, input=inp,
            timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def project_root(cwd: str) -> str | None:
    out = git(["rev-parse", "--show-toplevel"], cwd)
    return os.path.normpath(out.strip()) if out and out.strip() else None


def head_sha(root: str) -> str:
    out = git(["rev-parse", "--verify", "-q", "HEAD"], root)
    return out.strip() if out else "NO_HEAD"


# ----------------------------------------------------------------------- config

def read_config(root: str) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    f = Path(root) / ".claude" / f"{PLUGIN}.local.md"
    if not f.is_file():
        return cfg
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return cfg
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return cfg
    for line in m.group(1).splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, _, raw = line.partition(":")
        key, raw = key.strip(), raw.strip().strip("\"'")
        if key not in DEFAULT_CONFIG:
            continue
        default = DEFAULT_CONFIG[key]
        if isinstance(default, bool):
            cfg[key] = raw.lower() in {"true", "yes", "1", "on"}
        elif isinstance(default, list):
            inner = raw.strip("[]")
            cfg[key] = [x.strip().strip("\"'") for x in inner.split(",") if x.strip()]
        else:
            cfg[key] = raw
    if cfg["audit_level"] not in THRESHOLDS:
        cfg["audit_level"] = "balanced"
    return cfg


# ------------------------------------------------------------------------ state

def data_dir() -> Path:
    for var in ("CLAUDE_PLUGIN_DATA",):
        if os.environ.get(var):
            return Path(os.environ[var])
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(base) / "plugin-data" / PLUGIN


def state_path(root: str) -> Path:
    key = hashlib.sha1(os.path.normcase(root).encode("utf-8")).hexdigest()[:16]
    return data_dir() / "state" / f"{key}.json"


def load_state(root: str) -> dict:
    p = state_path(root)
    try:
        s = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(s, dict):
            return s
    except (OSError, ValueError):
        pass
    return {"version": 1, "root": root, "audited": [], "sessions": {}, "prompted": {}}


def save_state(root: str, state: dict) -> None:
    p = state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


# --------------------------------------------------------------------- snapshot

def is_noise(path: str) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in NOISE_GLOBS)


def classify(path: str) -> str:
    low = path.lower()
    name = low.rsplit("/", 1)[-1]
    parts = set(low.split("/")[:-1])
    ext = os.path.splitext(name)[1]
    if "test" in parts or "tests" in parts or "__tests__" in parts or re.search(r"(_test|\.test|\.spec|^test_)", name):
        return "tests"
    if name in SURFACE_NAMES or parts & SURFACE_PARTS or low.startswith(".github/workflows/"):
        return "surface"
    if ext in DOC_EXT or "docs" in parts:
        return "docs"
    if ext in CODE_EXT:
        return "source"
    return "other"


def hash_files(root: str, paths: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    if not paths:
        return out
    inp = "\n".join(paths) + "\n"
    res = git(["hash-object", "--stdin-paths"], root, inp=inp)
    if res is not None:
        shas = res.split()
        if len(shas) == len(paths):
            return dict(zip(paths, shas))
    for p in paths:  # fallback: per-file sha1 of content
        try:
            out[p] = hashlib.sha1((Path(root) / p).read_bytes()).hexdigest()
        except OSError:
            out[p] = "UNREADABLE"
    return out


def take_snapshot(root: str) -> dict:
    """{path: sha} for every non-noise file in the working tree (tracked + untracked)."""
    tracked = git(["ls-files", "-s", "-z"], root) or ""
    idx: dict[str, str] = {}
    for rec in tracked.split("\0"):
        if not rec:
            continue
        meta, _, path = rec.partition("\t")
        idx[path] = meta.split()[1]
    status = git(["status", "--porcelain=v1", "-z", "--untracked-files=all"], root) or ""
    dirty: set[str] = set()
    deleted: set[str] = set()
    toks = status.split("\0")
    i = 0
    while i < len(toks):
        t = toks[i]
        i += 1
        if len(t) < 4:
            continue
        xy, path = t[:2], t[3:]
        if "R" in xy or "C" in xy:
            i += 1  # skip the original-name token
        if "D" in xy.replace(" ", "") and not (Path(root) / path).exists():
            deleted.add(path)
        else:
            dirty.add(path)
    files = {p: s for p, s in idx.items() if p not in deleted and p not in dirty and not is_noise(p)}
    dirty_paths = sorted(p for p in dirty if not is_noise(p))
    files.update(hash_files(root, dirty_paths))
    if len(files) > MAX_SNAPSHOT_FILES:
        keep = sorted(files)[:MAX_SNAPSHOT_FILES]
        files = {p: files[p] for p in keep}
    return {"head": head_sha(root), "files": files, "dirty": dirty_paths}


def fingerprint(snap: dict) -> str:
    """Deterministic: HEAD + the dirty (uncommitted) relevant files and their hashes."""
    h = hashlib.sha256()
    h.update(snap["head"].encode())
    for p in snap["dirty"]:
        h.update(f"\n{p}:{snap['files'].get(p, 'GONE')}".encode())
    return h.hexdigest()[:16]


def read_blob(root: str, state: dict, sha: str) -> list[str] | None:
    stored = state.get("blobs", {}).get(sha)
    if stored is not None:
        return stored
    out = git(["cat-file", "-p", sha], root)
    if out is None or len(out) > MAX_TEXT_BYTES or "\0" in out:
        return None
    return out.splitlines()


def keep_blobs(root: str, state: dict, snap: dict) -> None:
    """Remember the text of dirty files so a later diff can measure real churn."""
    blobs: dict[str, list[str]] = {}
    for p in snap["dirty"][:MAX_STORED_BLOBS]:
        f = Path(root) / p
        try:
            if f.is_file() and f.stat().st_size <= MAX_TEXT_BYTES:
                blobs[snap["files"][p]] = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except (OSError, KeyError):
            continue
    state["blobs"] = blobs


def norm(lines: list[str]) -> list[str]:
    return [re.sub(r"\s+", " ", ln).strip() for ln in lines if ln.strip()]


def churn(old: list[str] | None, new: list[str] | None) -> int:
    if old is None or new is None:
        return 20  # content unavailable: assume a moderate change, never zero
    a, b = norm(old), norm(new)
    if a == b:
        return 0  # whitespace / blank-line only
    n = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            n += max(i2 - i1, j2 - j1)
    return n


def sig_hits(old: list[str] | None, new: list[str] | None) -> int:
    """New lines that define a public entry point (command, flag, route)."""
    seen = set(norm(old or []))
    return sum(1 for ln in norm(new or []) if ln not in seen and SURFACE_SIG.search(ln))


def file_lines(root: str, path: str) -> list[str] | None:
    f = Path(root) / path
    try:
        if f.stat().st_size > MAX_TEXT_BYTES:
            return None
        data = f.read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace").splitlines()


def measure_delta(root: str, state: dict, base: dict, cur: dict) -> dict:
    """Compare two snapshots. Returns churn per category and the notable files."""
    bf, cf = base["files"], cur["files"]
    added = sorted(p for p in cf if p not in bf)
    removed = sorted(p for p in bf if p not in cf)
    modified = sorted(p for p in cf if p in bf and cf[p] != bf[p])
    cats = {"source": 0, "tests": 0, "surface": 0, "docs": 0, "other": 0}
    files = {"source": set(), "surface": set(), "docs": set(), "tests": set()}
    new_code = removed_code = 0
    for p in added:
        c = classify(p)
        n = len(norm(file_lines(root, p) or [])) or 20
        cats[c] += n
        files.get(c, set()).add(p)
        if c in ("source", "surface") and os.path.splitext(p)[1] in CODE_EXT:
            new_code += 1
            cats["surface"] += 3 * sig_hits(None, file_lines(root, p))
    for p in removed:
        c = classify(p)
        old = read_blob(root, state, bf[p])
        cats[c] += len(norm(old or [])) or 20
        files.get(c, set()).add(p)
        if c in ("source", "surface") and os.path.splitext(p)[1] in CODE_EXT:
            removed_code += 1
    for p in modified:
        c = classify(p)
        old, new = read_blob(root, state, bf[p]), file_lines(root, p)
        n = churn(old, new)
        if n and c in ("source", "surface") and os.path.splitext(p)[1] in CODE_EXT:
            hits = sig_hits(old, new)
            if hits:
                cats["surface"] += 3 * hits
                files["surface"].add(p)
        if n:
            cats[c] += n
            files.get(c, set()).add(p)
    head_moved = base["head"] != cur["head"]
    release_touched = any(p.lower().rsplit("/", 1)[-1] in RELEASE_NAMES for p in added + modified)
    return {
        "churn": cats, "added": added, "removed": removed, "modified": modified,
        "new_code_files": new_code, "removed_code_files": removed_code,
        "head_moved": head_moved, "release_touched": release_touched,
        "files": {k: sorted(v) for k, v in files.items()},
    }


def is_meaningful(delta: dict, level: str) -> tuple[bool, str]:
    t_src, t_multi, t_surf, t_docs = THRESHOLDS[level]
    c = delta["churn"]
    src = c["source"] + c["surface"] + c["tests"] // 2
    n_src_files = len(delta["files"]["source"]) + len(delta["files"]["surface"])
    if delta["new_code_files"] and c["source"] + c["surface"] >= t_surf:
        return True, f"{delta['new_code_files']} new code file(s)"
    if delta["removed_code_files"]:
        return True, f"{delta['removed_code_files']} code file(s) removed"
    if c["surface"] >= t_surf and delta["files"]["surface"]:
        return True, "public-surface/manifest/CI file changed"
    if src >= t_src:
        return True, f"{src} lines of code churn"
    if n_src_files >= 2 and src >= t_multi:
        return True, f"{n_src_files} code files, {src} lines of churn"
    if c["docs"] >= t_docs:
        return True, f"{c['docs']} lines of documentation churn"
    return False, "below the significance threshold"


# ----------------------------------------------------------------- transcript

def tail_text(path: str | None, limit: int = 96_000) -> str:
    if not path:
        return ""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - limit))
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def recent_text(transcript: str | None) -> str:
    """Text of the last user prompt and last assistant message (best effort)."""
    out: list[str] = []
    for line in reversed(tail_text(transcript).splitlines()):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        msg = rec.get("message") if isinstance(rec, dict) else None
        if not isinstance(msg, dict) or rec.get("type") not in ("user", "assistant"):
            continue
        content = msg.get("content")
        if isinstance(content, list):
            content = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
        if isinstance(content, str) and content.strip():
            out.append(content[:4000])
        if len(out) >= 2:
            break
    return "\n".join(out)


# ----------------------------------------------------------------------- hooks

def read_hook_input() -> dict:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def session_base(state: dict, sid: str, snap: dict) -> dict:
    b = state["sessions"].get(sid)
    if b:
        return b["snapshot"]
    if state["audited"]:
        return state["audited"][-1]["snapshot"]
    return {"head": snap["head"], "files": {}, "dirty": []}


def cmd_session_start(_a) -> int:
    data = read_hook_input()
    root = project_root(data.get("cwd") or os.getcwd())
    if not root or not read_config(root)["enabled"] or os.environ.get("GUARDIAN_DISABLE"):
        return 0
    state = load_state(root)
    snap = take_snapshot(root)
    sid = str(data.get("session_id") or "default")
    keep_blobs(root, state, snap)
    state["sessions"][sid] = {"snapshot": snap, "started": time.time()}
    for old in sorted(state["sessions"], key=lambda k: state["sessions"][k]["started"])[:-5]:
        del state["sessions"][old]
    save_state(root, state)
    return 0


def satisfied(state: dict, fp: str, required: str) -> bool:
    rank = {"milestone": 1, "final": 2}
    return any(a["fingerprint"] == fp and rank[a["mode"]] >= rank[required] for a in state["audited"])


def cmd_stop(_a) -> int:
    data = read_hook_input()
    if os.environ.get("GUARDIAN_DISABLE"):
        return 0
    root = project_root(data.get("cwd") or os.getcwd())
    if not root:
        return 0
    cfg = read_config(root)
    if not cfg["enabled"]:
        return 0
    state = load_state(root)
    sid = str(data.get("session_id") or "default")
    cur = take_snapshot(root)
    fp = fingerprint(cur)
    base = session_base(state, sid, cur)
    delta = measure_delta(root, state, base, cur)
    meaningful, why = is_meaningful(delta, cfg["audit_level"])
    if not meaningful:
        return 0
    required = "milestone"
    if delta["release_touched"] or FINAL_WORDS.search(recent_text(data.get("transcript_path"))):
        required = "final"
    if satisfied(state, fp, required):
        return 0  # this exact state was already audited
    key = f"{fp}:{required}"
    if key in state["prompted"]:
        return 0  # already asked once for this exact state: never loop
    state["prompted"][key] = time.time()
    for old in sorted(state["prompted"], key=state["prompted"].get)[:-KEEP_FINGERPRINTS]:
        del state["prompted"][old]
    save_state(root, state)
    script = os.path.abspath(sys.argv[0]).replace("\\", "/")
    c = delta["churn"]
    reason = (
        "Meaningful project work appears complete, but the current project state has not passed the "
        f"Project Completeness Audit ({why}; code {c['source'] + c['surface']} lines, tests {c['tests']}, "
        f"docs {c['docs']}; fingerprint {fp}). Run the project-completeness-audit skill in "
        f"{required.upper()} mode: verify implementation, tests, runtime behaviour, packaging, docs and "
        "public surface against the real project, fix drift that is in scope, then mark the resulting "
        f'project fingerprint as audited with: python "{script}" mark --mode {required}\n'
        "Then finish. If the audit is genuinely not applicable, say why and run the mark command anyway."
    )
    json.dump({"decision": "block", "reason": reason}, sys.stdout)
    return 0


# -------------------------------------------------------------- user commands

def resolve_root(path: str | None) -> str:
    root = project_root(path or os.getcwd())
    if not root:
        print("not inside a git repository: the guardian needs Git to fingerprint a project", file=sys.stderr)
        raise SystemExit(2)
    return root


def cmd_mark(a) -> int:
    root = resolve_root(a.project)
    state = load_state(root)
    snap = take_snapshot(root)
    fp = fingerprint(snap)
    keep_blobs(root, state, snap)
    state["audited"].append({
        "fingerprint": fp, "mode": a.mode, "head": snap["head"],
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "snapshot": snap,
    })
    state["audited"] = state["audited"][-KEEP_FINGERPRINTS:]
    for s in state["sessions"].values():
        s["snapshot"] = snap  # later sessions measure change from the audited state
    save_state(root, state)
    print(json.dumps({"marked": True, "mode": a.mode, "fingerprint": fp, "head": snap["head"]}))
    return 0


def cmd_status(a) -> int:
    root = resolve_root(a.project)
    cfg = read_config(root)
    state = load_state(root)
    cur = take_snapshot(root)
    fp = fingerprint(cur)
    last = state["audited"][-1] if state["audited"] else None
    base = last["snapshot"] if last else {"head": cur["head"], "files": {}, "dirty": []}
    delta = measure_delta(root, state, base, cur)
    meaningful, why = is_meaningful(delta, cfg["audit_level"])
    print(json.dumps({
        "project": root, "enabled": cfg["enabled"], "audit_level": cfg["audit_level"],
        "fingerprint": fp, "head": cur["head"],
        "audited_current_state": satisfied(state, fp, "milestone"),
        "audited_final_current_state": satisfied(state, fp, "final"),
        "last_audit": ({k: last[k] for k in ("fingerprint", "mode", "head", "time")} if last else None),
        "change_since_last_audit": {"meaningful": meaningful, "why": why, "churn": delta["churn"],
                                     "added": delta["added"][:20], "removed": delta["removed"][:20],
                                     "modified": delta["modified"][:20]},
        "state_file": str(state_path(root)),
    }, indent=2))
    return 0


def cmd_config(a) -> int:
    print(json.dumps(read_config(resolve_root(a.project)), indent=2))
    return 0


def cmd_reset(a) -> int:
    root = resolve_root(a.project)
    p = state_path(root)
    if p.exists():
        p.unlink()
    print(json.dumps({"reset": True, "state_file": str(p)}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="guardian.py", description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--version", action="version", version=VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("session-start").set_defaults(fn=cmd_session_start)
    sub.add_parser("stop").set_defaults(fn=cmd_stop)
    p = sub.add_parser("mark")
    p.add_argument("--mode", choices=["milestone", "final"], required=True)
    p.set_defaults(fn=cmd_mark)
    for name, fn in (("status", cmd_status), ("config", cmd_config), ("reset", cmd_reset)):
        sp = sub.add_parser(name)
        sp.set_defaults(fn=fn)
    for sp in sub.choices.values():
        sp.add_argument("--project", default=None, help="project directory (default: cwd)")
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except Exception as exc:  # a hook must never wedge a session
        if args.cmd in ("stop", "session-start"):
            print(f"{PLUGIN}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 0
        raise


if __name__ == "__main__":
    sys.exit(main())
