"""Validate an extension archive before handing it to Blender's own installer."""
import hashlib
import re
import stat
import tomllib
import zipfile
from pathlib import PurePosixPath


def inspect_package(path, owner_id, expected_version):
    with zipfile.ZipFile(path) as archive:
        files = archive.infolist()
        if len(files) > 20000 or sum(f.file_size for f in files) > 2 * 1024**3:
            raise ValueError("Extension exceeds the installed size or file count limit.")
        seen = set()
        for entry in files:
            name = entry.orig_filename.rstrip("/")
            parts = name.split("/")
            mode = stat.S_IFMT(entry.external_attr >> 16)
            if (not name or len(name) > 1024 or any(ord(c) < 32 or c in '\\:*?"<>|' for c in name)
                or any(p in ("", ".", "..") or p.lower() == ".git" or p.endswith((" ", "."))
                       or re.fullmatch(r"(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?", p, re.I) for p in parts)
                or name.casefold() in seen or mode not in (0, stat.S_IFDIR, stat.S_IFREG)
                or entry.flag_bits & 1):
                raise ValueError("The ZIP contains an unsafe or ambiguous path.")
            seen.add(name.casefold())
        candidates = [f for f in files if f.filename == "blender_manifest.toml"]
        if not candidates:
            candidates = [f for f in files if f.filename.count("/") == 1 and f.filename.endswith("/blender_manifest.toml")]
        if len(candidates) != 1 or candidates[0].file_size > 65536:
            raise ValueError("Choose a Blender extension ZIP with one top-level manifest.")
        manifest = candidates[0].filename
        prefix = manifest.removesuffix("blender_manifest.toml")
        if prefix and any(not entry.filename.startswith(prefix) for entry in files):
            raise ValueError("The ZIP contains files outside the extension package.")
        info = tomllib.loads(archive.read(manifest).decode())
        if info.get("type") != "add-on" or info.get("id") != owner_id or info.get("version") != expected_version:
            raise ValueError("The ZIP's extension ID or version does not match this update.")
        if prefix + "__init__.py" not in archive.namelist():
            raise ValueError("Extension entry point is missing.")
        return prefix


def installed_matches(path, prefix, location):
    """Verify persisted installer output, including same-version replacements."""
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            target = location.joinpath(*PurePosixPath(entry.filename[len(prefix):]).parts)
            if not target.is_file() or target.stat().st_size != entry.file_size:
                return False
            with target.open("rb") as installed, archive.open(entry) as packaged:
                if hashlib.file_digest(installed, "sha256").digest() != hashlib.file_digest(packaged, "sha256").digest():
                    return False
    return True
