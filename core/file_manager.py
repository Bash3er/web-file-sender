import mimetypes
import os
from pathlib import Path
from .access_control import allowed_path, canonical, get_share_root, is_within, virtual_path

def list_directory(path, config):
    target = canonical(path)
    if not target.is_dir() or not allowed_path(target, config):
        raise PermissionError("This folder is not shared.")
    items = []
    try:
        children = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as exc:
        raise PermissionError("Could not read this folder.") from exc
    for child in children:
        try:
            resolved = canonical(child)
            if not allowed_path(resolved, config):
                continue
            if not config.get("hidden_files", False) and child.name.startswith("."):
                continue
            item = {
                "name": child.name,
                "type": "directory" if resolved.is_dir() else "file",
                "path": virtual_path(resolved, config),
            }
            if resolved.is_file():
                try:
                    item["size"] = resolved.stat().st_size
                except OSError:
                    continue
                item["mime"] = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
            items.append(item)
        except (OSError, RuntimeError):
            continue
    return items

def resolve_virtual_path(virtual, config):
    virtual = (virtual or "").replace("\\", "/").strip("/")
    if not virtual:
        return None
    parts = [p for p in virtual.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise PermissionError("Invalid path.")
    for share in config.get("shares", []):
        name = share.get("name") or Path(share.get("path", "")).name or "Shared"
        if parts and parts[0] == name:
            base = canonical(share["path"])
            candidate = base.joinpath(*parts[1:]).resolve(strict=True)
            if not is_within(candidate, base) or not allowed_path(candidate, config):
                raise PermissionError("This item is not shared.")
            return candidate
    raise PermissionError("Unknown shared path.")

def root_items(config):
    out = []
    for share in config.get("shares", []):
        try:
            root = canonical(share["path"])
            if not (root.is_dir() or root.is_file()) or not allowed_path(root, config):
                continue
            item = {
                "name": share.get("name") or root.name or "Shared",
                "type": "directory" if root.is_dir() else "file",
                "path": virtual_path(root, config),
            }
            if root.is_file():
                import mimetypes
                item["size"] = root.stat().st_size
                item["mime"] = mimetypes.guess_type(root.name)[0] or "application/octet-stream"
            out.append(item)
        except (KeyError, OSError, RuntimeError):
            continue
    return sorted(out, key=lambda x: x["name"].lower())
