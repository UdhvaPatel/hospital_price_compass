"""Sync pipeline state with the private vault repository (hospital-price-vault).

The vault keeps what a fresh GitHub runner needs to continue where the last run stopped:
  data/state/          file fingerprints (address, date, size, sha256) and parse state
  data/extracts/       filtered price rows per hospital (Parquet, a few MB in total)
  data/cache/          embeddings and summaries (so NVIDIA is only called for new text)
  data/history/        hospital price snapshots (change over time)
  data/logs/downloads.jsonl   the download log
Full hospital price files are never stored (too big for git): only their fingerprints.

    python -m hpc.vault pull     # vault -> working tree (no-op if the vault is still empty)
    python -m hpc.vault push     # working tree -> vault, one commit per run

Auth: the VAULT_TOKEN environment variable (a fine-grained token with Contents read/write on the
vault only). It is passed to git through an environment-scoped credential header and is
scrubbed from anything printed.
"""
from __future__ import annotations

import argparse
import base64
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from hpc.config import ROOT

VAULT_DIR = ROOT / ".vault"
DEFAULT_REPO = "UdhvaPatel/hospital-price-vault"
PATHS = ["data/state", "data/extracts", "data/cache/embeddings", "data/cache/summaries.json",
         "data/history", "data/logs/downloads.jsonl"]


class VaultError(RuntimeError):
    pass


def _git(args: list[str], cwd: Path | None = None, token: str = "", check: bool = True) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    cmd = ["git"]
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        # header via config-from-env: never appears on the command line or in the remote URL
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
                   GIT_CONFIG_VALUE_0=f"AUTHORIZATION: basic {basic}")
    res = subprocess.run(cmd + args, cwd=cwd, env=env, capture_output=True, text=True)
    if check and res.returncode != 0:
        msg = (res.stderr or res.stdout).replace(token, "***") if token else (res.stderr or res.stdout)
        raise VaultError(f"git {args[0]} failed: {msg.strip()[:400]}")
    return res


def repo_url(repo: str) -> str:
    return f"https://github.com/{repo}.git"


def clone(repo: str, token: str, dest: Path = VAULT_DIR) -> bool:
    """Clone (or refresh) the vault. Returns False if the vault has no commits yet."""
    if dest.exists():
        shutil.rmtree(dest)
    _git(["clone", "--depth", "1", repo_url(repo), str(dest)], token=token)
    return _git(["rev-parse", "--verify", "HEAD"], cwd=dest, check=False).returncode == 0


def _copy(src: Path, dst: Path) -> int:
    """Mirror src onto dst (file or directory). Returns files copied."""
    if not src.exists():
        return 0
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return 1
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return sum(1 for p in dst.rglob("*") if p.is_file())


def pull(repo: str, token: str, root: Path = ROOT, vault: Path = VAULT_DIR) -> int:
    if not clone(repo, token, vault):
        print("vault is empty: starting fresh")
        return 0
    n = sum(_copy(vault / p, root / p) for p in PATHS)
    print(f"restored {n} files from the vault")
    return n


def push(repo: str, token: str, root: Path = ROOT, vault: Path = VAULT_DIR, message: str | None = None) -> bool:
    if not (vault / ".git").exists():
        clone(repo, token, vault)
    for p in PATHS:
        if (vault / p).exists() and not (root / p).exists():
            continue                         # never delete vault data just because a step didn't produce it
        _copy(root / p, vault / p)
    (vault / "README.md").write_text(
        "# hospital-price-vault (private)\n\nPipeline state for hospital_price_compass: file fingerprints, filtered "
        "extracts, caches and price history. Written by the monthly refresh workflow. Full hospital price files are "
        "not stored here, only their address, date, size and SHA-256.\n", encoding="utf-8")
    _git(["add", "-A"], cwd=vault)
    if _git(["diff", "--cached", "--quiet"], cwd=vault, check=False).returncode == 0:
        print("vault unchanged")
        return False
    _git(["-c", "user.name=hospital-price-compass-bot", "-c", "user.email=actions@users.noreply.github.com",
          "commit", "-m", message or f"refresh {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"], cwd=vault)
    _git(["push", "origin", "HEAD"], cwd=vault, token=token)
    print("vault updated")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["pull", "push"])
    ap.add_argument("--repo", default=os.environ.get("VAULT_REPO", DEFAULT_REPO))
    args = ap.parse_args()
    token = os.environ.get("VAULT_TOKEN", "")
    if not token:
        raise SystemExit("VAULT_TOKEN is not set")
    (pull if args.action == "pull" else push)(args.repo, token)


if __name__ == "__main__":
    main()
