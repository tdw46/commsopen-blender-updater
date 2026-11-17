# CommsOpen Blender Updater

Give buyers **Check for updates** and **Update from CommsOpen** buttons inside your Blender extension. Checking uses public edition metadata. Installing opens CommsOpen in the buyer’s default browser, asks them to approve one purchased ZIP, and installs it into the repository where their extension is running.

## Connect your product

1. In CommsOpen, connect your GitHub content account. Open your shop product’s **Buyer downloads → Manage edition files → GitHub release**.
2. Import the built extension ZIP from a stable release such as `v1.2.3`. Its manifest must identify your add-on and version `1.2.3`. Include vendored dependencies in the uploaded release asset.
3. Select the imported ZIP for the appropriate edition and save. Under **Blender add-on setup**, find the product and edition IDs. If several current release ZIPs are selected, the buyer chooses one on the approval screen.

## Vendor and register

From your extension’s Git checkout:

```sh
git submodule add https://github.com/tdw46/commsopen-blender-updater.git vendor/commsopen_updater
git submodule update --init --recursive
```

Commit your submodule pin. Include `vendor/__init__.py` and the updater’s Python files **and MIT license** in the ZIP you distribute. Check the built ZIP because GitHub's automatic source archives omit submodules.

In your add-on’s root `__init__.py`, instantiate one controller with your public IDs. Register it alongside your own classes and draw its controls wherever buyers manage the add-on, for example in your existing `AddonPreferences.draw`. The registration and draw functions below show the corresponding calls in your extension.

```python
from .vendor.commsopen_updater import Updater

updates = Updater(__package__, product=123, edition=456)

def register():
    updates.register()

def unregister():
    updates.unregister()

# Inside your existing Panel or AddonPreferences class:
def draw(self, context):
    updates.draw(self.layout)
```

For a single-edition product, omit `edition`. Pass the extension root package even when the panel lives in a submodule. Declare the feature in your existing `blender_manifest.toml` permissions table:

```toml
[permissions]
network = "Check for updates and download approved purchases from CommsOpen"
```

## Buyer experience

- **Check for updates** fetches the selected edition once. The controller compares the release version to the installed manifest. After its first successful installation it also detects a rebuilt release with the same version and a different revision.
- **Update from CommsOpen** opens the normal CommsOpen sign-in page when needed. Sign in with the account that owns the download and choose **Allow update**. A recently linked guest purchase needs an account download grant before it can be used here.
- Blender’s installer refreshes the same local repository and reloads the add-on. Keep user preferences and persistent data in Blender’s extension-user storage. An add-on carrying an already-loaded native library can require a restart.
- Updates require a writable, locally managed Blender extension repository, a manifest-based add-on ZIP, and Blender’s **Allow Online Access**. Git development checkouts and system or remotely managed repositories are protected. Classic single-file scripts do not carry the manifest identity used for in-place updates.
- The helper runs on demand and exits. It never stores a CommsOpen password or reusable account token. Local approval listens only on a random loopback port and times out after three minutes. Failed or interrupted downloads can be requested again from the add-on. A successful transfer counts against any existing purchase download limit.

## API and embedding

`Updater(package, product, edition=None)` provides `register()`, `unregister()`, `draw(layout)` and `start("check" | "install")`. Run these from Blender’s main thread. Repeated clicks share one in-flight action per controller. To design custom controls, call `start` in your operator and read `message` for the result. A separate helper handles browser approval and network I/O. No background update polling is scheduled.

For a custom client, the public version check is `GET /wp-json/commsopen/v1/products/{product}/updates?edition={edition}`. It returns version, revision and an ETag. An unchanged validator can return 304. Do not prompt for an update based only on a first-seen revision.

Buyer authorization uses `GET /blender/authorize/` with product and edition IDs, a loopback port, a random 32-byte base64url state and an S256 challenge. The browser returns state and a single-use code to `http://127.0.0.1:{port}/callback`. Exchange it over HTTPS with `POST /wp-json/commsopen/v1/blender/package` using JSON fields `code` and `verifier`. The server rechecks the browser session, exact purchase permission and selected release at redemption. Its response is the protected ZIP with `X-CommsOpen-SHA256`, `X-CommsOpen-Version` and `X-CommsOpen-Revision`. Verify these and the extension manifest before installing. It can return 403 after expiry, sign-out, revocation or replay.

## Development

Run `python3 -m unittest discover -s tests` from the repository root with Python 3.11+. Blender runtime verification uses disposable user repositories. Isolated native install and reload checks passed on Blender 5.1.2 and 5.2.2 on macOS. Developer reference: [Blender extensions](https://docs.blender.org/manual/en/5.1/editors/preferences/extensions.html).
