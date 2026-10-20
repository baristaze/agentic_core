"""The recorder: the scene played in headless Chromium, frame by frame,
and joined into dreamcatcher.gif.

    make demo-gif

It plays the tape `make demo` wrote. It runs on Playwright's own
Chromium build, installed once, so nothing is downloaded, and it needs
ffmpeg. Every request the page makes must be a file in the page's
folder: any other is aborted. A page error, a console error, or a
request outside the folder fails the recording, and so does a GIF of 6
MB or more; the GIF is written only when none happened.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

from playwright.sync_api import ConsoleMessage, Request, Route, sync_playwright

HERE = Path(__file__).resolve().parent
SCENE = HERE / "scene"
GIF = HERE / "dreamcatcher.gif"
FPS = 20
VIEW = {"width": 1280, "height": 720}
LIMIT = 6_000_000
"""Bytes the GIF stays under, so the front page loads it at once."""


def inside(url: str) -> bool:
    """A file in the page's folder."""
    parts = urlparse(url)
    return parts.scheme == "file" and Path(unquote(parts.path)).resolve().is_relative_to(SCENE)


def play(frames: Path) -> tuple[float, list[str]]:
    """Each frame of the scene, as a PNG; the scene's length, and what went wrong."""
    faults: list[str] = []

    def guard(route: Route) -> None:
        if inside(route.request.url):
            route.continue_()
        else:
            route.abort()

    def watch(request: Request) -> None:
        if not inside(request.url):
            faults.append(f"a request outside the page's folder, aborted: {request.url}")

    def console(message: ConsoleMessage) -> None:
        if message.type == "error":
            faults.append(f"a console error: {message.text}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEW, device_scale_factor=1)
        page.route("**/*", guard)
        page.on("request", watch)
        page.on("console", console)
        page.on("pageerror", lambda error: faults.append(f"a page error: {error}"))
        page.goto((SCENE / "index.html").as_uri() + "#still")
        duration = float(page.evaluate("window.dreamcatcher.duration"))
        for n in range(round(duration * FPS) + 1):
            page.evaluate(f"window.dreamcatcher.seek({n / FPS})")
            page.screenshot(path=frames / f"{n:05d}.png")
        browser.close()
    return duration, list(dict.fromkeys(faults))


def join(frames: Path, gif: Path) -> None:
    """The frames as one looping GIF, on a palette made for them."""
    graph = (
        "split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];"
        "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle"
    )
    pattern = str(frames / "%05d.png")
    command = ["ffmpeg", "-v", "error", "-y", "-framerate", str(FPS), "-i", pattern, "-vf", graph, "-loop", "0", str(gif)]
    subprocess.run(command, check=True)


def main() -> int:
    with tempfile.TemporaryDirectory() as folder:
        frames = Path(folder)
        duration, faults = play(frames)
        for fault in faults:
            print(f"failed: {fault}")
        if faults:
            return 1
        made = frames / "dreamcatcher.gif"
        join(frames, made)
        size = made.stat().st_size
        print(f"{round(duration * FPS) + 1} frames at {FPS} a second: {duration:.1f} s, {size / 1e6:.2f} MB")
        if size >= LIMIT:
            print(f"failed: the GIF weighs {size / 1e6:.2f} MB, at or above {LIMIT / 1e6:.0f} MB")
            return 1
        shutil.move(made, GIF)
    print(f"wrote {GIF}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
