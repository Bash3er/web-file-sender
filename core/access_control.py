"""Centralized path policy. Only configured shares are visible; deny rules override allows."""
import os
from pathlib import Path

def canonical(path):
    return Path(path).expanduser().resolve(strict=True)

def is_within(path, root):
    try:
        Path(path).relative_to(Path(root))
        return True
    except ValueError:
        return False

def is_hidden(path):
    return any(part.startswith(".") for part in Path(path).parts)

def allowed_path(path, config):
    try:
        target = canonical(path)
    except (OSError, RuntimeError):
        return False
    shares = []
    for item in config.get("shares", []):
        try:
            p = canonical(item["path"])
            shares.append((p, item))
        except (KeyError, OSError, RuntimeError):
            continue
    matching_roots = [(root, share) for root, share in shares if is_within(target, root)]
    if not matching_roots:
        return False
    if not config.get("hidden_files", False):
        # Hide dot-prefixed descendants relative to the selected share, not unrelated
        # ancestors such as a user's home directory.
        for root, _share in matching_roots:
            try:
                rel_parts = target.relative_to(root).parts
            except ValueError:
                continue
            if any(part.startswith(".") for part in rel_parts):
                return False
    # Explicit deny rules override all share grants.
    for rule in config.get("rules", []):
        if rule.get("action") != "deny":
            continue
        try:
            deny = Path(rule["path"]).expanduser().resolve(strict=False)
            if is_within(target, deny):
                return False
        except (KeyError, OSError, RuntimeError):
            continue
    return target.is_file() or target.is_dir()

def get_share_root(path, config):
    target = canonical(path)
    roots = []
    for item in config.get("shares", []):
        try:
            root = canonical(item["path"])
            if is_within(target, root):
                roots.append((len(str(root)), root, item))
        except (KeyError, OSError, RuntimeError):
            pass
    return max(roots, default=(0, None, None), key=lambda x: x[0])[1:]

def virtual_path(path, config):
    target = canonical(path)
    root, share = get_share_root(target, config)
    if root is None:
        return None
    rel = target.relative_to(root).as_posix()
    prefix = share.get("name") or root.name or "Shared"
    return prefix if rel == "." else prefix + "/" + rel
