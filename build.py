#!/usr/bin/env python3
"""Build a fully local Craft App Suite site.

1. Reads the app catalog (id, name, tagline, repo) from storytold/craft-launcher at its latest
   release tag, plus each app's icon.
2. Reads every app's latest GitHub release and downloads its `<id>-web-<version>.zip`.
3. Extracts the launcher's own web build at the site root and each app at `/<slug>/`, where the
   slug is the bold part of the name (FigCraft -> fig).
4. Saves the release JSON and screenshots the launcher asks GitHub for, and injects
   `_local/shim.js` into every page, so the launcher works offline and its download buttons open
   the local web app instead.

Usage: python3 build.py [--out site] [--cache cache]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

LAUNCHER_REPO = "storytold/craft-launcher"
ASSETS_REPO = "storytold/craft-launcher-assets"
SHOTS_REV = "v1"
HERE = Path(__file__).resolve().parent
UA = "craft-launcher-web-builder"


def token() -> str | None:
    if os.environ.get("GITHUB_TOKEN"):
        return os.environ["GITHUB_TOKEN"]
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


TOKEN = token()


def get(url: str, api: bool = False) -> bytes:
    headers = {"User-Agent": UA}
    if api:
        headers["Accept"] = "application/vnd.github+json"
        if TOKEN:
            headers["Authorization"] = f"Bearer {TOKEN}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as r:
        return r.read()


def api(path: str):
    return json.loads(get(f"https://api.github.com/{path}", api=True))


def slug_for(name: str) -> str:
    """The bold part of the wordmark: the name without its "Craft" suffix."""
    if name.endswith("Craft"):
        return name[: -len("Craft")].lower()
    return name.replace("Craft", "").strip().lower().replace(" ", "-")


def parse_catalog_toml(src: str) -> list[dict]:
    """crates/core/catalog.toml, the catalog's home since launcher v0.19."""
    apps = [
        {"id": a["id"], "name": a["name"], "tagline": a["tagline"], "repo": a["repo"], "slug": slug_for(a["name"])}
        for a in tomllib.loads(src).get("apps", [])
    ]
    if not apps:
        sys.exit("error: no apps found in catalog.toml (format changed?)")
    return apps


def fetch_catalog(tag: str) -> list[dict]:
    """The app list at `tag`: catalog.toml (v0.19+), else the older SUITE array in catalog.rs."""
    base = f"https://raw.githubusercontent.com/{LAUNCHER_REPO}/{tag}/crates/core"
    try:
        return parse_catalog_toml(get(f"{base}/catalog.toml").decode())
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    return parse_catalog(get(f"{base}/src/catalog.rs").decode())


def parse_catalog(src: str) -> list[dict]:
    start = src.index("pub const SUITE")
    end = src.index("];", start)
    block = src[start:end]
    pattern = re.compile(r'app\(\s*"([^"]+)",\s*"([^"]+)",\s*"([^"]+)",\s*\[\[.*?\]\],\s*"([^"]+)"\s*,?\s*\)', re.S)
    apps = [{"id": i, "name": n, "tagline": t, "repo": r, "slug": slug_for(n)} for i, n, t, r in pattern.findall(block)]
    if not apps:
        sys.exit("error: no apps found in catalog.rs (format changed?)")
    return apps


def download(url: str, dest: Path, size: int) -> None:
    if dest.exists() and dest.stat().st_size == size:
        print(f"  cached   {dest.name}")
        return
    print(f"  download {dest.name} ({size / 1048576:.0f} MB)")
    tmp = dest.with_suffix(".part")
    req = urllib.request.Request(url, headers={"User-Agent": UA})  # no token: it'd break the redirect
    with urllib.request.urlopen(req, timeout=600) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    tmp.replace(dest)


def extract(zip_path: Path, dest: Path) -> None:
    """Extracts the zip's single top folder into `dest` (refusing paths that escape it)."""
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(zip_path) as z:
        root = Path(tmp).resolve()
        for m in z.infolist():
            target = (root / m.filename).resolve()
            if root not in target.parents and target != root:
                sys.exit(f"error: {zip_path.name} has an unsafe path: {m.filename}")
        z.extractall(root)
        entries = [p for p in root.iterdir() if p.name != "__MACOSX"]
        inner = entries[0] if len(entries) == 1 and entries[0].is_dir() else root
        dest.mkdir(parents=True, exist_ok=True)
        for p in inner.iterdir():
            shutil.move(str(p), dest / p.name)


def web_asset(release: dict) -> dict | None:
    return next((a for a in release.get("assets", []) if "-web-" in a["name"] and a["name"].endswith(".zip")), None)


def inject_shim(index: Path, rel: str) -> None:
    html = index.read_text(encoding="utf-8")
    tag = f'<script src="{rel}_local/shim.js"></script>'
    if tag in html:
        return
    html, n = re.subn(r"(<head[^>]*>)", lambda m: m.group(1) + "\n  " + tag, html, count=1)
    if n == 0:
        html = tag + html
    index.write_text(html, encoding="utf-8")


def build_patched_launcher(tag: str, cache: Path) -> Path | None:
    """Builds the launcher's web app at `tag` with patches/launcher-web.patch (the download button
    says Open; the menu drops Choose version, Release notes and the per-format downloads).
    Returns the built site, or None (with the reason printed) to fall back to the stock zip."""
    patch = HERE / "patches" / "launcher-web.patch"
    trunk = shutil.which("trunk")
    cargo = shutil.which("cargo")
    if not cargo:
        try:
            cargo = subprocess.run(["rustup", "which", "cargo"], capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            cargo = None
    if not (patch.exists() and trunk and cargo):
        print("  patched launcher skipped: needs patches/launcher-web.patch, trunk and cargo (brew install trunk; rustup target add wasm32-unknown-unknown)")
        return None
    src = cache / "src" / f"craft-launcher-{tag}"
    if not src.exists():
        print(f"  clone {LAUNCHER_REPO}@{tag}")
        subprocess.run(["git", "clone", "-q", "--depth", "1", "-c", "advice.detachedHead=false", "--branch", tag,
                        f"https://github.com/{LAUNCHER_REPO}", str(src)], check=True)
    subprocess.run(["git", "-C", str(src), "checkout", "-q", "--", "."], check=True)
    if subprocess.run(["git", "-C", str(src), "apply", str(patch)]).returncode != 0:
        print(f"  patched launcher skipped: the patch doesn't apply to {tag}; update patches/launcher-web.patch")
        return None
    env = dict(os.environ, PATH=str(Path(cargo).parent) + os.pathsep + os.environ.get("PATH", ""))
    print("  trunk build (first run takes a few minutes)")
    app = src / "apps" / "artcraft-launcher-web"
    if subprocess.run([trunk, "build", "--release", "--cargo-profile", "wasm-release"], cwd=app, env=env).returncode != 0:
        print("  patched launcher skipped: trunk build failed")
        return None
    dist = src / "dist" / "web"
    return dist if (dist / "index.html").exists() else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "site"))
    ap.add_argument("--cache", default=str(HERE / "cache"))
    ap.add_argument("--stock-launcher", action="store_true", help="use the release's web zip as is (no Open button patch)")
    ap.add_argument("--require-patch", action="store_true", help="fail instead of falling back to the stock launcher")
    ap.add_argument("--launcher-tag", help="build for this launcher release (default: the latest), e.g. v0.14.1")
    args = ap.parse_args()
    out, cache = Path(args.out).resolve(), Path(args.cache).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    if not TOKEN:
        print("note: no GitHub token (gh auth login or GITHUB_TOKEN); the API allows 60 requests/hour")

    # 1. Launcher release and catalog at that tag.
    which = f"tags/{args.launcher_tag}" if args.launcher_tag else "latest"
    launcher = api(f"repos/{LAUNCHER_REPO}/releases/{which}")
    tag = launcher["tag_name"]
    print(f"launcher {tag}")
    apps = fetch_catalog(tag)
    print(f"catalog: {len(apps)} apps")

    build = out.with_name(out.name + ".building")
    if build.exists():
        shutil.rmtree(build)
    local = build / "_local"
    for d in ("releases", "icons", "screenshots"):
        (local / d).mkdir(parents=True, exist_ok=True)

    # 2. Launcher at the root: built with the patch, or the release's web zip.
    patched = None if args.stock_launcher else build_patched_launcher(tag, cache)
    if not patched and args.require_patch:
        sys.exit("error: the patched launcher couldn't be built (--require-patch)")
    if patched:
        print("  using the patched launcher")
        shutil.copytree(patched, build, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".trunk-lock"))
        # The licences and notices the stock zip ships, from the same source tree.
        for name in ("LICENSE-MIT", "LICENSE-APACHE", "NOTICE", "ATTRIBUTION.md"):
            if (patched.parent.parent / name).exists():
                shutil.copy2(patched.parent.parent / name, build / name)
    else:
        asset = web_asset(launcher)
        if not asset:
            sys.exit("error: the launcher release has no web zip")
        print("  using the stock launcher")
        download(asset["browser_download_url"], cache / asset["name"], asset["size"])
        extract(cache / asset["name"], build)
    releases = {LAUNCHER_REPO: launcher}

    # 3. Each app at /<slug>/.
    for app in apps:
        print(f"{app['name']} -> /{app['slug']}/")
        icon = f"https://raw.githubusercontent.com/{LAUNCHER_REPO}/{tag}/assets/app-icons/{app['id']}.png"
        try:
            (local / "icons" / f"{app['id']}.png").write_bytes(get(icon))
            app["icon"] = f"_local/icons/{app['id']}.png"
        except Exception as e:  # noqa: BLE001 - an icon is optional
            print(f"  no icon ({e})")
        try:
            rel = api(f"repos/{app['repo']}/releases/latest")
        except Exception as e:  # noqa: BLE001
            print(f"  no release ({e})")
            app["web"] = False
            continue
        releases[app["repo"]] = rel
        app["version"] = rel["tag_name"]
        asset = web_asset(rel)
        app["web"] = bool(asset)
        if not asset:
            print("  no web version in the latest release")
            continue
        download(asset["browser_download_url"], cache / asset["name"], asset["size"])
        extract(cache / asset["name"], build / app["slug"])
        index = build / app["slug"] / "index.html"
        if index.exists():
            inject_shim(index, "../")
        else:
            print("  warning: no index.html in the zip")
            app["web"] = False

    # 4. What the launcher asks GitHub for, saved locally.
    for repo, rel in releases.items():
        name = repo.replace("/", "__")
        (local / "releases" / f"{name}.json").write_text(json.dumps(rel), encoding="utf-8")
        (local / "releases" / f"{name}.list.json").write_text(json.dumps([rel]), encoding="utf-8")
    try:
        tree = api(f"repos/{ASSETS_REPO}/git/trees/{SHOTS_REV}?recursive=1")
        shots = [t["path"] for t in tree["tree"] if t["type"] == "blob" and t["path"].endswith(".jpg")]
        print(f"screenshots: {len(shots)}")
        for p in shots:
            dest = local / "screenshots" / SHOTS_REV / p
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(get(f"https://raw.githubusercontent.com/{ASSETS_REPO}/{SHOTS_REV}/{p}"))
    except Exception as e:  # noqa: BLE001 - details pages still work without pictures
        print(f"screenshots skipped ({e})")

    manifest = {
        "launcher": {"repo": LAUNCHER_REPO, "version": tag},
        "shotsBase": f"https://raw.githubusercontent.com/{ASSETS_REPO}/",
        "apps": apps,
    }
    (local / "apps.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    # The version of this site is the launcher's: read by release.sh and the release workflow.
    (build / "VERSION").write_text(tag.removeprefix("v") + "\n", encoding="utf-8")
    shim = (HERE / "shim.js").read_text(encoding="utf-8")
    (local / "shim.js").write_text(f"window.__CRAFT_LOCAL__ = {json.dumps(manifest, ensure_ascii=False)};\n{shim}", encoding="utf-8")
    inject_shim(build / "index.html", "./")

    if out.exists():
        shutil.rmtree(out)
    build.rename(out)

    print("\ndone:", out)
    for a in apps:
        mark = f"/{a['slug']}/" if a.get("web") else "(no web version)"
        print(f"  {a['name']:<16} {mark}")
    print("\nrun: python3 serve.py   ->  http://localhost:8080/")


if __name__ == "__main__":
    main()
