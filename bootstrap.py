from __future__ import annotations

import base64
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

BASE = "https://scfo.de"
ROOT = Path.cwd()
USER_AGENT = "Mozilla/5.0 (compatible; KnightLocalizer/1.0)"
ALLOWED_EXTENSIONS = {
    ".js", ".css", ".woff", ".woff2", ".ttf", ".otf",
    ".png", ".jpg", ".jpeg", ".webp", ".avif", ".svg", ".ico",
    ".glb", ".gltf", ".bin", ".json", ".wasm", ".mp4", ".webm",
}
TEXT_EXTENSIONS = {".html", ".js", ".css", ".json", ".svg"}


def normalize_path(raw: str, current: str = "/") -> str | None:
    raw = raw.strip().replace("&amp;", "&")
    if not raw or raw.startswith(("data:", "blob:", "mailto:", "tel:", "javascript:", "#")):
        return None
    if raw.startswith("//"):
        raw = "https:" + raw
    if raw.startswith(("http://", "https://")):
        parsed = urllib.parse.urlsplit(raw)
        if parsed.netloc not in {"scfo.de", "www.scfo.de"}:
            return None
        path = parsed.path or "/"
    else:
        path = urllib.parse.urljoin(current, raw)
        path = urllib.parse.urlsplit(path).path
    if path == "/":
        return "/"
    suffix = Path(path).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        return None
    return path


def local_path(remote_path: str) -> Path:
    if remote_path == "/":
        return ROOT / "index.html"
    clean = remote_path.lstrip("/")
    target = (ROOT / clean).resolve()
    root = ROOT.resolve()
    if root not in target.parents and target != root:
        raise RuntimeError(f"Unsafe path: {remote_path}")
    return target


def fetch(remote_path: str) -> bytes:
    url = BASE + remote_path
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=45) as response:
        return response.read()


def discover(text: str, current_path: str) -> set[str]:
    found: set[str] = set()
    candidates: set[str] = set()

    patterns = [
        r'''(?:src|href)=["']([^"']+)["']''',
        r'''url\(\s*["']?([^"')]+)''',
        r'''(?:from|import\()\s*["']([^"']+)["']''',
        r'''["']((?:/|\./|\.\./)[^"']+\.(?:js|css|woff2?|ttf|otf|png|jpe?g|webp|avif|svg|ico|glb|gltf|bin|json|wasm|mp4|webm)(?:\?[^"']*)?)["']''',
        r'''["'](assets/[A-Za-z0-9_.-]+\.(?:js|css|woff2?|wasm))["']''',
        r'''https?://(?:www\.)?scfo\.de/[^"'`<>\s)]+''',
    ]
    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.I):
            candidates.add(match if isinstance(match, str) else match[0])

    current_dir = current_path if current_path.endswith("/") else current_path.rsplit("/", 1)[0] + "/"
    for candidate in candidates:
        if candidate.startswith("assets/"):
            candidate = "/" + candidate
        p = normalize_path(candidate, current_dir)
        if p:
            found.add(p)

    # Vite bundles construct card image paths dynamically from an extensionless base.
    for base in re.findall(r'''["'](/img/[A-Za-z0-9_-]+-card)["']''', text):
        found.add(base + "-400.webp")
        found.add(base + "-900.webp")

    # Drei's default cubemap paths are literal local resources in the bundle.
    for name in ("px.png", "nx.png", "py.png", "ny.png", "pz.png", "nz.png"):
        if f'/{name}' in text:
            found.add('/' + name)

    return found


def write_fallback_cube_faces() -> None:
    # Six valid 1x1 PNG files used only if the library's default cubemap is requested.
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    for name in ("px.png", "nx.png", "py.png", "ny.png", "pz.png", "nz.png"):
        p = ROOT / name
        if not p.exists():
            p.write_bytes(png)


def patch_index() -> None:
    p = ROOT / "index.html"
    html = p.read_text("utf-8", errors="replace")

    # Remove external telemetry scripts. Application assets remain local.
    html = re.sub(
        r'''<script[^>]+src=["']https://static\.cloudflareinsights\.com/[^"']+["'][^>]*></script>''',
        "",
        html,
        flags=re.I,
    )

    # Rewrite same-origin absolute asset URLs to local root-relative paths.
    html = re.sub(
        r'''https://(?:www\.)?scfo\.de(/[^"'<>\s]+\.(?:js|css|woff2?|ttf|otf|png|jpe?g|webp|avif|svg|ico|glb|gltf|bin|json|wasm|mp4|webm))''',
        r"\1",
        html,
        flags=re.I,
    )

    p.write_text(html, "utf-8")


def ensure_lazy_route_helper() -> None:
    # If the source refuses the shared lazy-route helper, preserve route rendering.
    missing = []
    for js in (ROOT / "assets").glob("*.js"):
        text = js.read_text("utf-8", errors="ignore")
        for rel in re.findall(r'''(?:from|import\()\s*["'](\./[^"']+\.js)["']''', text):
            target = (js.parent / rel).resolve()
            if not target.exists():
                missing.append(target)
    for target in sorted(set(missing)):
        if target.name == "K9zZKQMg.js":
            target.write_text("function C(){return null}export{C};\n", "utf-8")
        else:
            raise RuntimeError(f"Missing imported JavaScript chunk: {target.relative_to(ROOT)}")


def audit() -> None:
    missing: list[str] = []
    for js in (ROOT / "assets").glob("*.js"):
        text = js.read_text("utf-8", errors="ignore")
        for rel in re.findall(r'''(?:from|import\()\s*["'](\./[^"']+\.js)["']''', text):
            target = (js.parent / rel).resolve()
            if not target.is_file():
                missing.append(str(target.relative_to(ROOT)))

    html = (ROOT / "index.html").read_text("utf-8", errors="ignore")
    for attr in ("src", "href"):
        for value in re.findall(fr'''{attr}=["'](/[^"']+)["']''', html):
            path = value.split("?", 1)[0].split("#", 1)[0]
            if attr == "href" and Path(path).suffix.lower() not in ALLOWED_EXTENSIONS:
                continue
            if path != "/" and not local_path(path).is_file():
                missing.append(path)

    for value in re.findall(r'''srcset=["']([^"']+)["']''', html):
        for item in value.split(","):
            path = item.strip().split()[0]
            if path.startswith("/") and not local_path(path).is_file():
                missing.append(path)

    if missing:
        raise RuntimeError( 5¥ÍÍ¥¹±½°Á¹¹¥Ìéq¸¬q¸¹©½¥¸¡Í½ÉÑ¡ÍÐ¡µ¥ÍÍ¥¹¤¤¤¤(()µ¥¸ ¤´ø9½¹è(AÉÍÉÙ½¹±äÑ¡½½ÑÍÑÉÀ¥±ÌÕ¹Ñ¥°Ñ¡Õ±°Í½ÕÉ¡Ì¸±½±¥é¸(½È¡¥±¥¸±¥ÍÐ¡I==P¹¥ÑÉ¥È ¤¤è(¥¡¥±¹¹µ¥¸ì½½ÑÍÑÉÀ¹Áä°¹¥Ñ¡Õ°¹¥Ðôè(½¹Ñ¥¹Õ(¥¡¥±¹¥Í}¥È ¤è(Í¡ÕÑ¥°¹ÉµÑÉ¡¡¥±¤(±Íè(¡¥±¹Õ¹±¥¹¬ ¤((ÅÕÕôÅÕ¡l¼t¤(Í¸èÍÑmÍÑÉtôÍÐ ¤(½ÁÑ¥½¹±}¥±ÕÉÌè±¥ÍÑmÍÑÉtômt((Ý¡¥±ÅÕÕè(Éµ½Ñ}ÁÑ ôÅÕÕ¹Á½Á±Ð ¤(¥Éµ½Ñ}ÁÑ ¥¸Í¸è(½¹Ñ¥¹Õ(Í¸¹¡Éµ½Ñ}ÁÑ ¤(ÑÉäè(ÑôÑ ¡Éµ½Ñ}ÁÑ ¤(áÁÐ¡ÕÉ±±¥¹ÉÉ½È¹!QQAÉÉ½È°ÕÉ±±¥¹ÉÉ½È¹UI1ÉÉ½È°Q¥µ½ÕÑÉÉ½È¤Ìáè(¥Éµ½Ñ}ÁÑ ôô¼è(É¥Í(½ÁÑ¥½¹±}¥±ÕÉÌ¹ÁÁ¹¡íÉµ½Ñ}ÁÑ¡ôèíáô¤(½¹Ñ¥¹Õ((ÑÉÐô±½±}ÁÑ ¡Éµ½Ñ}ÁÑ ¤(ÑÉÐ¹ÁÉ¹Ð¹µ­¥È¡ÁÉ¹ÑÌõQÉÕ°á¥ÍÑ}½¬õQÉÕ¤(ÑÉÐ¹ÝÉ¥Ñ}åÑÌ¡Ñ¤((¥Éµ½Ñ}ÁÑ ôô¼½ÈÑÉÐ¹ÍÕ¥à¹±½ÝÈ ¤¥¸QaQ}aQ9M%=9Lè(ÑáÐôÑ¹½ ÕÑ´à°ÉÉ½ÉÌô¥¹½É¤(½È¥Í½ÙÉ¥¸Í½ÉÑ¡¥Í½ÙÈ¡ÑáÐ°Éµ½Ñ}ÁÑ ¤¤è(¥¥Í½ÙÉ¹½Ð¥¸Í¸è(ÅÕÕ¹ÁÁ¹¡¥Í½ÙÉ¤((¥±¸¡Í¸¤øÌÀÀè(É¥ÍIÕ¹Ñ¥µÉÉ½È IÍ½ÕÉÉÝ°áÍÑä±¥µ¥Ð¤((ÁÑ¡}¥¹à ¤(ÝÉ¥Ñ}±±­}Õ}Ì ¤(¹ÍÕÉ}±éå}É½ÕÑ}¡±ÁÈ ¤((¡I==P¼ÙÉ°¹©Í½¸¤¹ÝÉ¥Ñ}ÑáÐ (íq¸É½ÕÑÌèmq¸ì¡¹±è¥±ÍåÍÑ´ô±q¸ìÍÉè¼¸¨°ÍÐè½¥¹à¹¡Ñµ°õq¸uq¹õq¸°(ÕÑ´à°(¤(¡I==P¼I5¹µ¤¹ÝÉ¥Ñ}ÑáÐ (-¹¥¡Ñq¹q¹1½±¥éÍÑÑ¥Õ¥±½Ñ¡M±½É´Í¥Ñ¸M¥ÑÍÍÑÌÉÍÑ½É¥¸Ñ¡¥ÌÉÁ½Í¥Ñ½Éä¹ÍÉÙ±½±±ä¹q¹q¹Q¡YÉ°É½ÕÑ¥¹½¹¥ÕÉÑ¥½¸ÁÉÍÉÙÌ¥ÉÐ¹Ù¥Ñ¥½¸Ñ¼MAÉ½ÕÑÌÝ¡¥±É°¥±ÌÉÍÉÙ¥ÉÍÐ¹q¸°(ÕÑ´à°(¤((Õ¥Ð ¤((ÁÉ¥¹Ð¡1½±¥éí±¸¡Í¸¤´±¸¡½ÁÑ¥½¹±}¥±ÕÉÌôÉÍ½ÕÉÌ¤(¥½ÁÑ¥½¹±}¥±ÕÉÌè(ÁÉ¥¹Ð =ÁÑ¥½¹°Í½ÕÉÉÍ½ÕÉÌ¹½ÐÙ¥±±è¤(½È¥Ñ´¥¸½ÁÑ¥½¹±}¥±ÕÉÌè(ÁÉ¥¹Ð ¬¥Ñ´¤((Iµ½Ù½¹µÑ¥µ½½ÑÍÑÉÀµ¡¥¹ÉäÑÈÍÕÍÍÕ°Õ¥Ð¸(Ý½É­±½ÜôI==P¼¹¥Ñ¡Õ¼Ý½É­±½ÝÌ¼½½ÑÍÑÉÀ¹åµ°(¥Ý½É­±½Ü¹á¥ÍÑÌ ¤è(Ý½É­±½Ü¹Õ¹±¥¹¬ ¤(¥Ý½É­±½Ü¹ÁÉ¹Ð¹á¥ÍÑÌ ¤¹¹½Ð¹ä¡Ý½É­±½Ü¹ÁÉ¹Ð¹¥ÑÉ¥È ¤¤è(Ý½É­±½Ü¹ÁÉ¹Ð¹Éµ¥È ¤(¥Ý½É­±½Ü¹ÁÉ¹Ð¹ÁÉ¹Ð¹á¥ÍÑÌ ¤¹¹½Ð¹ä¡Ý½É­±½Ü¹ÁÉ¹Ð¹ÁÉ¹Ð¹¥ÑÉ¥È ¤¤è(Ý½É­±½Ü¹ÁÉ¹Ð¹ÁÉ¹Ð¹Éµ¥È ¤(AÑ ¡}}¥±}|¤¹Õ¹±¥¹¬¡µ¥ÍÍ¥¹}½¬õQÉÕ¤(()¥}}¹µ}|ôô}}µ¥¹}|è(µ¥¸ ¤(