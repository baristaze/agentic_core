"""The screen: one terminal frame of 120 by 36, in ANSI and Braille.

The ring is drawn on a Braille canvas, two dots across and four down per
cell, so a circle comes out round. Each step is a slice, colored by its
kind; the newest sits at the top, so the ring turns one slice per step.
Each response draws a thread to its request, and each request a thread
to the inputs it delivered, curled toward the center: the history's
references weave the web. Each sub-agent is a small ring of its own, tied
to where its report landed.
"""

import itertools
import math
import re

from dreamcatcher.projection import Frame, Ledger, Ring

WIDTH, HEIGHT = 120, 36
RING_COLS, RING_ROWS = 72, 32
PANEL = WIDTH - RING_COLS

COLORS: dict[str, int] = {
    "message": 231,
    "report": 183,
    "model_request": 81,
    "model_response": 33,
    "tool_request": 221,
    "tool_response": 114,
    "recovered": 51,
    "failure": 203,
    "parked": 205,
    "resumed": 177,
    "control": 245,
    "loop_ended": 250,
}
THREADS: dict[str, int] = {"model_response": 25, "tool_response": 101, "recovered": 37, "model_request": 97}
FAINT, DIM, TEXT, BRIGHT, ACCENT = 237, 244, 252, 231, 81

ANSI = re.compile(r"\x1b\[[0-9;]*m")
DOTS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))


def fg(color: int) -> str:
    return f"\x1b[38;5;{color}m"


RESET, BOLD = "\x1b[0m", "\x1b[1m"


def paint(text: str, color: int, bold: bool = False) -> str:
    return f"{BOLD if bold else ''}{fg(color)}{text}{RESET}"


def width(text: str) -> int:
    return len(ANSI.sub("", text))


def fit(text: str, size: int) -> str:
    """`text` cut or padded to `size` visible columns."""
    plain = ANSI.sub("", text)
    if len(plain) <= size:
        return text + " " * (size - len(plain))
    return plain[: size - 1] + "…"


class Canvas:
    def __init__(self, cols: int, rows: int) -> None:
        self.cols, self.rows = cols, rows
        self.bits = [[0] * cols for _ in range(rows)]
        self.colors = [[0] * cols for _ in range(rows)]
        self.text: dict[tuple[int, int], str] = {}

    def dot(self, x: float, y: float, color: int) -> None:
        col, row = int(x) // 2, int(y) // 4
        if 0 <= col < self.cols and 0 <= row < self.rows and x >= 0 and y >= 0:
            self.bits[row][col] |= DOTS[int(y) % 4][int(x) % 2]
            self.colors[row][col] = color

    def write(self, row: int, col: int, text: str, color: int, bold: bool = False) -> None:
        for n, char in enumerate(text):
            if 0 <= col + n < self.cols:
                self.text[(row, col + n)] = paint(char, color, bold)

    def centered(self, row: int, text: str, color: int, bold: bool = False) -> None:
        self.write(row, (self.cols - len(text)) // 2, text, color, bold)

    def lines(self) -> list[str]:
        out = []
        for row in range(self.rows):
            cells = []
            for col in range(self.cols):
                if (row, col) in self.text:
                    cells.append(self.text[(row, col)])
                elif self.bits[row][col]:
                    cells.append(fg(self.colors[row][col]) + chr(0x2800 + self.bits[row][col]))
                else:
                    cells.append(" ")
            out.append("".join(cells) + RESET)
        return out


def polar(cx: float, cy: float, r: float, theta: float) -> tuple[float, float]:
    """A point at angle `theta`, clockwise from the top."""
    return cx + r * math.sin(theta), cy - r * math.cos(theta)


def draw_ring(canvas: Canvas, ring: Ring, cx: float, cy: float, outer: float, inner: float, slots: int, web: bool) -> list[float]:
    """Draws `ring` and returns each slice's angle."""
    n = len(ring.slices)
    total = max(n, slots)
    step = 2 * math.pi / total
    angles = [(i - (n - 1)) * step for i in range(n)]
    if web:
        rim = inner - 2
        for i, piece in enumerate(ring.slices):
            for j in piece.links:
                draw_thread(canvas, cx, cy, rim, angles[j], angles[i], THREADS.get(piece.kind, DIM))
    top = int(cy - outer) - 1
    for y in range(max(top, 0), int(cy + outer) + 2):
        for x in range(int(cx - outer) - 1, int(cx + outer) + 2):
            r = math.hypot(x - cx, y - cy)
            if not inner <= r <= outer:
                continue
            theta = math.atan2(x - cx, cy - y)
            k = round(-theta / step) % total
            i = n - 1 - k
            if 0 <= i < n:
                canvas.dot(x, y, COLORS.get(ring.slices[i].kind, DIM))
            elif r >= outer - 1 and (x + y) % 3 == 0:
                canvas.dot(x, y, FAINT)
    return angles


def draw_thread(canvas: Canvas, cx: float, cy: float, rim: float, a: float, b: float, color: int) -> None:
    """A thread from the rim at `a` to the rim at `b`, curled toward the
    center like the web of a dreamcatcher."""
    x0, y0 = polar(cx, cy, rim, a)
    x2, y2 = polar(cx, cy, rim, b)
    xc, yc = polar(cx, cy, rim * 0.42, (a + b) / 2 + 1.1)
    span = math.hypot(xc - x0, yc - y0) + math.hypot(x2 - xc, y2 - yc)
    samples = max(8, int(span * 1.6))
    for s in range(samples + 1):
        t = s / samples
        u = 1 - t
        canvas.dot(u * u * x0 + 2 * u * t * xc + t * t * x2, u * u * y0 + 2 * u * t * yc + t * t * y2, color)


def draw_line(canvas: Canvas, x0: float, y0: float, x1: float, y1: float, color: int) -> None:
    samples = int(math.hypot(x1 - x0, y1 - y0))
    for s in range(0, samples + 1, 2):
        t = s / max(samples, 1)
        canvas.dot(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, color)


def ring_panel(frame: Frame, slots: int) -> list[str]:
    canvas = Canvas(RING_COLS, RING_ROWS)
    cx, cy = RING_COLS, RING_ROWS * 2
    outer, inner = 58.0, 51.0
    angles = draw_ring(canvas, frame.ring, cx, cy, outer, inner, slots, web=True)
    spots = ((14.0, 14.0), (2 * RING_COLS - 14.0, 14.0))
    for moon, (mx, my) in zip(frame.moons, spots, strict=False):
        for landed in moon.reports:
            if landed < len(angles):
                tx, ty = polar(cx, cy, outer + 2, angles[landed])
                draw_line(canvas, mx + (tx - mx) * 0.2, my + (ty - my) * 0.2, tx, ty, COLORS["report"])
        draw_ring(canvas, moon, mx, my, 10.0, 6.0, 12, web=False)
        row, col = int(my + 14) // 4, int(mx) // 2
        canvas.write(row, col - len(moon.title) // 2, moon.title, TEXT)
        canvas.write(row + 1, col - len(moon.state) // 2, moon.state, DIM)
    middle = RING_ROWS // 2
    state = frame.state
    color = COLORS["parked"] if state.startswith("parked") else (ACCENT if "idle" not in state else TEXT)
    canvas.centered(middle - 1, f" {state} ", color, bold=True)
    led = frame.ledger
    canvas.centered(middle + 1, f" step {led.total_steps} · epoch {led.epoch} ", DIM)
    return canvas.lines()


def money(micros: int | None) -> str:
    return "—" if micros is None else f"${micros / 1_000_000:.4f}"


def bar(parts: list[tuple[float, int, str]], size: int) -> str:
    """A bar of `size` cells: each part a share of the whole, its color,
    and its fill."""
    out, used = [], 0
    for share, color, char in parts:
        cells = min(size - used, max(0, round(share * size)))
        out.append(paint(char * cells, color))
        used += cells
    return "".join(out) + paint("·" * (size - used), FAINT)


def head(text: str, note: str = "") -> str:
    return paint(text, ACCENT, bold=True) + paint(note, DIM)


def row(name: str, *values: str) -> str:
    return " " + paint(f"{name:<11}", DIM) + "".join(values)


def tally(kind: str, count: int) -> str:
    return paint("■ ", COLORS.get(kind, DIM)) + paint(f"{kind:<15}", TEXT) + paint(f"{count:>4}", BRIGHT)


def numbers(led: Ledger) -> list[str]:
    """The panel beside the ring: each line one figure of the ledger."""
    lines = [head("STEPS") + paint(f"{led.total_steps:>32}", BRIGHT, bold=True)]
    kinds = list(led.steps)
    for left, right in itertools.zip_longest(kinds[0::2], kinds[1::2]):
        lines.append(" " + fit(tally(*left), 22) + "  " + (tally(*right) if right else ""))
    parks = " · ".join(f"{reason} {n}" for reason, n in led.parks) or "none"
    cap, hold = led.cap_micros or 1, led.hold_micros or 0
    spent = min(led.cost_micros / cap, 1.0)
    held = min(hold / cap, 1.0 - spent)
    over = led.cost_micros + hold > cap
    return [
        *lines,
        "",
        head("LOOPS"),
        row("loops", paint(f"{led.loops} ended" + (" · 1 open" if led.open_loop else ""), TEXT)),
        row("step guard", paint(f"{led.guard} of {led.guard_limit} calls this loop", TEXT)),
        row("parks", paint(parks, COLORS["parked"] if led.parks else TEXT)),
        row("steers", paint(str(led.steers), TEXT)),
        row("recovered", paint(f"{led.recovered} by effect", TEXT), paint(f" · writer epoch {led.epoch}", DIM)),
        "",
        head("MODEL"),
        row("calls", paint(f"{led.model_calls}", BRIGHT), paint(f"   tool calls {led.tool_calls}", TEXT)),
        row("tokens", paint(f"in {led.tokens_in:,} · out {led.tokens_out:,}", TEXT)),
        row("cache read", paint(f"{led.tokens_cache:,}", TEXT)),
        row("window", bar([(led.window * 10, ACCENT, "■")], 10), paint(f" {led.window:.1%} full", TEXT)),
        row("latency", paint(f"{led.last_latency_ms / 1000:.1f} s last · {led.latency_ms / 1000:.1f} s all", TEXT)),
        row("wall", paint(f"{led.wall_s:.1f} s", TEXT)),
        "",
        head("SPEND", "   the gate holds the worst case first"),
        row("spent", paint(money(led.cost_micros), BRIGHT, bold=True), paint(f" of {money(led.cap_micros)}", TEXT)),
        row("hold", paint(money(led.hold_micros), COLORS["parked"]), paint(f" before call {led.hold_call}", TEXT)),
        " " + bar([(spent, BRIGHT, "█"), (held, COLORS["parked"] if over else DIM, "▒")], 32) + paint("│ cap", DIM),
    ]


def compose(frame: Frame, slots: int, header: str, caption: str, beat: str) -> str:
    """The whole screen, as one string that redraws it in place."""
    ring = ring_panel(frame, slots)
    panel = numbers(frame.ledger)
    rows = [fit(header, WIDTH)]
    for n in range(RING_ROWS):
        right = panel[n - 1] if 1 <= n <= len(panel) else ""
        rows.append(ring[n] + fit(right, PANEL))
    rows.append(fit(caption, WIDTH))
    rows.append(fit(beat, WIDTH))
    rows.append(fit(paint("model ▸ ", DIM) + paint(frame.said, TEXT), WIDTH))
    return "\x1b[H" + "\n".join(row + "\x1b[K" for row in rows)
