"""File status tracking for SQA Agent.

Manages git blob hashes to track which files have changed since their
last review, avoiding unnecessary re-reviews of unchanged files.
"""

import fnmatch
import json
import logging
import subprocess
from pathlib import Path

from git import Repo
from git.exc import GitCommandError

from sqa_agent.config import Config

logger = logging.getLogger("sqa-agent")

FILE_STATUS_NAME = "file_status.json"


def get_git_hash(repo: Repo, filepath: Path) -> str | None:
    """Compute the git blob hash of a file's current content on disk."""
    if not filepath.exists():
        return None
    try:
        return repo.git.hash_object(str(filepath))
    except GitCommandError:
        return None


def resolve_candidate_files(
    config: Config,
    project_root: Path,
    repo: Repo,
) -> list[str]:
    """Resolve include/exclude globs to a sorted list of relative file paths.

    Only files tracked by git are included.
    """
    raw_tracked = repo.git.ls_files().splitlines()
    repo_root = Path(repo.working_dir).resolve()
    resolved_project_root = project_root.resolve()
    try:
        prefix = str(resolved_project_root.relative_to(repo_root))
    except ValueError:
        prefix = ""
    if prefix and prefix != ".":
        # ls-files paths are relative to the repo root; strip the prefix so
        # they become relative to project_root.
        prefix_slash = prefix + "/"
        tracked = {
            p[len(prefix_slash) :] for p in raw_tracked if p.startswith(prefix_slash)
        }
    else:
        tracked = set(raw_tracked)
    logger.debug(f"resolve_candidate_files: {len(tracked)} tracked file(s)")

    if not config.include:
        logger.debug("resolve_candidate_files: no include patterns configured")
        return []

    included = set()
    for pattern in config.include:
        matches = list(project_root.glob(pattern))
        files = [p for p in matches if p.is_file()]
        logger.debug(
            f"resolve_candidate_files: pattern '{pattern}' matched {len(files)} file(s)"
        )
        for path in files:
            try:
                rel = str(path.relative_to(project_root))
            except ValueError:
                continue
            if rel in tracked:
                included.add(rel)
            else:
                logger.debug(
                    f"resolve_candidate_files: '{rel}' matched but not tracked by git"
                )

    logger.debug(f"resolve_candidate_files: {len(included)} file(s) after include")

    excluded = set()
    for rel_path in included:
        for pattern in config.exclude:
            if fnmatch.fnmatch(rel_path, pattern):
                excluded.add(rel_path)
                break

    if excluded:
        logger.debug(f"resolve_candidate_files: {len(excluded)} file(s) excluded")

    return sorted(included - excluded)


def load_file_status(sqa_dir: Path) -> dict[str, str]:
    """Load the file_status.json mapping of {path: hash}."""
    path = sqa_dir / FILE_STATUS_NAME
    if not path.exists():
        return {}
    with open(path) as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as exc:
            raise SystemExit(
                f"Error: {path} is corrupted ({exc}). "
                f"Delete it or run 'sqa-agent reset' to rebuild it."
            ) from None


def save_file_status(sqa_dir: Path, file_status: dict[str, str]) -> None:
    """Write the file_status.json mapping to disk."""
    path = sqa_dir / FILE_STATUS_NAME
    path.write_text(json.dumps(file_status, indent=2) + "\n")


def reconcile(
    file_status: dict[str, str],
    candidates: list[str],
    current_hashes: dict[str, str],
) -> list[str]:
    """Reconcile file_status in-place against current candidates. Returns files needing review.

    Steps:
    1. Rename detection: if a candidate's hash matches a file_status entry
       under a different path, and the match is unambiguous, update the path.
    2. Prune: remove file_status entries whose paths are no longer in the
       candidate list.
    3. Filter: return candidates whose current hash differs from (or is
       absent in) file_status.
    """
    # Step 1: Rename detection (unambiguous matches only).
    # Build a reverse map from hash -> list of paths in file_status.
    hash_to_old_paths: dict[str, list[str]] = {}
    for path, h in file_status.items():
        hash_to_old_paths.setdefault(h, []).append(path)

    candidate_set = set(candidates)

    for candidate in candidates:
        if candidate in file_status:
            continue  # Already tracked under this path.
        h = current_hashes.get(candidate)
        if h is None:
            continue
        old_paths = hash_to_old_paths.get(h, [])
        # Only remap if exactly one old path has this hash and that old
        # path is no longer a candidate (i.e. it was likely renamed).
        stale_old = [p for p in old_paths if p not in candidate_set]
        if len(stale_old) == 1:
            old_path = stale_old[0]
            logger.debug(f"Detected rename: {old_path} -> {candidate}")
            file_status[candidate] = file_status.pop(old_path)
            # Update the reverse map to stay consistent.
            hash_to_old_paths[h].remove(old_path)
            hash_to_old_paths[h].append(candidate)

    # Step 2: Prune entries not in the candidate list.
    stale_keys = [p for p in file_status if p not in candidate_set]
    for key in stale_keys:
        logger.debug(f"Pruning stale entry: {key}")
        del file_status[key]

    # Step 3: Identify files needing review.
    needs_review = []
    for candidate in candidates:
        stored_hash = file_status.get(candidate)
        if stored_hash != current_hashes.get(candidate):
            needs_review.append(candidate)

    return needs_review


def compute_hashes(repo: Repo, project_root: Path, files: list[str]) -> dict[str, str]:
    """Compute git blob hashes for a list of relative file paths.

    Uses a single ``git hash-object --stdin-paths`` invocation to avoid
    spawning one subprocess per file.
    """
    if not files:
        return {}

    # Filter to files that actually exist on disk.
    existing = [(rel, project_root / rel) for rel in files]
    existing = [(rel, abs_path) for rel, abs_path in existing if abs_path.exists()]
    if not existing:
        return {}

    stdin_text = "\n".join(str(abs_path) for _, abs_path in existing)
    try:
        result = subprocess.run(
            ["git", "hash-object", "--stdin-paths"],
            input=stdin_text,
            capture_output=True,
            text=True,
            cwd=project_root,
            check=True,
        )
        output = result.stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        # Fall back to per-file hashing on unexpected errors.
        hashes = {}
        for rel_path in files:
            h = get_git_hash(repo, project_root / rel_path)
            if h is not None:
                hashes[rel_path] = h
        return hashes

    hash_lines = output.splitlines()
    hashes = {}
    for (rel_path, _), h in zip(existing, hash_lines):
        if h:
            hashes[rel_path] = h
    return hashes


def mark_reviewed(
    sqa_dir: Path, file_status: dict[str, str], rel_path: str, git_hash: str
) -> None:
    """Record that a file has been reviewed by storing its current hash."""
    file_status[rel_path] = git_hash
    save_file_status(sqa_dir, file_status)
