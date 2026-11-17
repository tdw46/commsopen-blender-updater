"""Vendored Blender extension updater. Pass the owning extension's root __package__."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

from .package import inspect_package, installed_matches


def _owner(package):
    import bpy
    names = package.split(".")
    if len(names) != 3 or names[0] != "bl_ext" or package not in sys.modules:
        raise ValueError("Pass the installed extension's root __package__.")
    _, repo_name, addon = names
    repo = next((r for r in bpy.context.preferences.extensions.repos if r.module == repo_name), None)
    if repo is None or not repo.enabled:
        raise ValueError("The installed Blender repository is unavailable.")
    location = Path(sys.modules[package].__file__).parent
    if location.is_symlink() or location.resolve() != Path(repo.directory).resolve() / addon:
        raise ValueError("The running extension is outside its configured repository.")
    info = tomllib.loads((location / "blender_manifest.toml").read_text(encoding="utf-8"))
    if info["id"] != addon:
        raise ValueError("The installed manifest belongs to another extension.")
    return repo, location, info


def install_verified(package, archive, result):
    """Run on Blender's main thread. Blender handles dependency refresh and add-on reload."""
    import bpy
    repo, location, info = _owner(package)
    if repo.use_remote_url or getattr(repo, "source", "USER") == "SYSTEM":
        raise ValueError("Install this extension in a writable local Blender repository first.")
    if any((p / ".git").exists() for p in (location, *location.parents)):
        raise ValueError("This is a development checkout. Update the checkout through Git.")
    from hashlib import file_digest
    with Path(archive).open("rb") as stream:
        if file_digest(stream, "sha256").hexdigest() != result["sha256"]:
            raise ValueError("The downloaded ZIP changed before installation.")
    prefix = inspect_package(archive, info["id"], result["version"])
    if _version(result["version"]) < _version(info["version"]):
        raise ValueError("The offered package is older than the installed extension.")
    outcome = bpy.ops.extensions.package_install_files('EXEC_DEFAULT', filepath=str(archive), repo=repo.module, enable_on_install=True)
    if 'FINISHED' not in outcome or not installed_matches(archive, prefix, location):
        raise RuntimeError("Blender did not finish installing this update. Check its extension report.")


def _version(text):
    parts = str(text).split(".")
    if len(parts) != 3 or any(not p.isdecimal() for p in parts):
        raise ValueError("Expected a three-part release version.")
    return tuple(map(int, parts))


def update_available(installed_version, installed_revision, remote):
    """An unknown baseline alone never reports a same-version rebuild."""
    if not remote.get("version"):
        return False
    local, latest = _version(installed_version), _version(remote["version"])
    return latest > local or (latest == local and bool(installed_revision) and remote["revision"] != installed_revision)


class Updater:
    """One controller and two scoped operator classes per owning add-on."""
    def __init__(self, package, product, edition=None):
        self.package = package
        self.product, self.edition = int(product), int(edition or product)
        if min(self.product, self.edition) < 1:
            raise ValueError("Supply the edition and product IDs from CommsOpen.")
        self.job = self.temp = self.timer = None
        self._message = ""
        self._classes = ()

    @property
    def message(self):
        # The calling module can be reloaded by Blender's own install operator.
        import bpy
        return bpy.app.driver_namespace.get("commsopen.update." + self.package, self._message)

    @message.setter
    def message(self, value):
        import bpy
        self._message = value
        bpy.app.driver_namespace["commsopen.update." + self.package] = value

    def _state(self):
        import bpy
        name = f"commsopen-{self.product}-{self.edition}.json"
        return Path(bpy.utils.extension_path_user(self.package, path="", create=True)) / name

    def _finish(self):
        if self.job.poll() is None and time.monotonic() < self.deadline:
            return 0.4
        try:
            if self.job.poll() is None:
                raise TimeoutError("Update timed out. Try again.")
            data = json.loads((self.temp / "result.json").read_text(encoding="utf-8"))
            if "error" in data:
                raise ValueError(data["error"])
            _, _, info = _owner(self.package)
            if self.action == "check":
                state = self._state()
                saved = json.loads(state.read_text()) if state.is_file() else {}
                revision = saved.get("revision") if saved.get("version") == info["version"] else None
                if update_available(info["version"], revision, data):
                    self.message = f"Update available · {data['version']}"
                elif data.get("version"):
                    self.message = f"Installed version · {info['version']}"
                else:
                    self.message = "This edition has no versioned release."
            else:
                # Blender can unregister and re-register this very module during its install operator.
                # Detach ownership of the ZIP first so unregister cannot delete it mid-install.
                folder, self.temp = self.temp, None
                self.job = self.timer = None
                try:
                    install_verified(self.package, folder / "update.zip", data)
                    state = self._state()
                    temp = state.with_suffix(".tmp")
                    temp.write_text(json.dumps({"version": data["version"], "revision": data["revision"]}))
                    os.replace(temp, state)
                    self.message = f"Installed {data['version']}. Restart Blender if one is requested."
                finally:
                    shutil.rmtree(folder)
        except Exception as error:
            self.message = str(error) or "The update could not be completed."
        finally:
            self._clear()
            self._redraw()
        return None

    def _clear(self):
        if self.job and self.job.poll() is None:
            self.job.kill()
            self.job.wait(timeout=5)
        self.job = self.timer = None
        if self.temp:
            shutil.rmtree(self.temp)
            self.temp = None

    @staticmethod
    def _redraw():
        import bpy
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()

    def start(self, action):
        import bpy
        if action not in ("check", "install"):
            raise ValueError("Unknown update action.")
        if self.job:
            return
        if not getattr(bpy.app, "online_access", False):
            raise ValueError("Enable Allow Online Access in Blender Preferences to check for updates.")
        _owner(self.package)
        self.temp = Path(tempfile.mkdtemp(prefix="commsopen-update-"))
        self.action = action
        self.deadline = time.monotonic() + (510 if action == "install" else 45)
        try:
            self.job = subprocess.Popen([sys.executable, "-I", str(Path(__file__).with_name("transport.py")),
                                         action, str(self.product), str(self.edition), str(self.temp)],
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.message = "Checking for updates…" if action == "check" else "Approve the download in your browser…"
            self.timer = self._finish
            bpy.app.timers.register(self.timer, first_interval=0.2)
        except Exception:
            self._clear()
            raise

    def register(self):
        import bpy
        import hashlib
        if self._classes:
            return
        owner = self
        prefix = "co_" + hashlib.sha256(self.package.encode()).hexdigest()[:16]
        def operator(action, label):
            def execute(op, context):
                try:
                    owner.start(action)
                except Exception as error:
                    op.report({'ERROR'}, str(error))
                    return {'CANCELLED'}
                return {'FINISHED'}
            return type(f"{prefix.upper()}_OT_{action}", (bpy.types.Operator,), {
                "bl_idname": prefix + "." + action, "bl_label": label, "execute": execute,
            })
        for cls in (operator("check", "Check for updates"), operator("install", "Update from CommsOpen")):
            bpy.utils.register_class(cls)
            self._classes += (cls,)

    def draw(self, layout):
        row = layout.row(align=True)
        row.enabled = not self.job
        for cls, icon in zip(self._classes, ("FILE_REFRESH", "IMPORT")):
            row.operator(cls.bl_idname, icon=icon)
        if self.message:
            layout.label(text=self.message)

    def unregister(self):
        import bpy
        if self.timer and bpy.app.timers.is_registered(self.timer):
            bpy.app.timers.unregister(self.timer)
        self._clear()
        for cls in reversed(self._classes):
            bpy.utils.unregister_class(cls)
        self._classes = ()
