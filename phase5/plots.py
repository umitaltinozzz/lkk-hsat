"""Publication plots as dependency-free SVG.

The locked solver image carries no plotting library and must not be changed, so
the figures are emitted as plain SVG. Log axes are used for runtime because the
measurements span several orders of magnitude.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterable, Sequence

W, H = 900, 620
L, R, T, B = 90, 30, 50, 70
PALETTE = ("#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf")


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _header(title: str, x_label: str, y_label: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="Helvetica,Arial,sans-serif">',
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
        f'<text x="{W/2}" y="28" text-anchor="middle" font-size="17" '
        f'font-weight="600">{_esc(title)}</text>',
        f'<text x="{W/2}" y="{H-18}" text-anchor="middle" font-size="13">{_esc(x_label)}</text>',
        f'<text x="22" y="{H/2}" text-anchor="middle" font-size="13" '
        f'transform="rotate(-90 22 {H/2})">{_esc(y_label)}</text>',
        f'<rect x="{L}" y="{T}" width="{W-L-R}" height="{H-T-B}" fill="none" stroke="#c8c8c8"/>',
    ]


def _log_ticks(lo: float, hi: float) -> list[float]:
    start, stop = math.floor(math.log10(lo)), math.ceil(math.log10(hi))
    return [10 ** e for e in range(int(start), int(stop) + 1)]


def _fmt_ms(value: float) -> str:
    if value >= 1000:
        return f"{value/1000:g} s"
    return f"{value:g} ms"


def cactus(path: Path, series: dict[str, Sequence[float]], timeout_ms: float,
           title: str = "Cactus plot: instances solved within a time budget") -> None:
    """Runtime of the n-th fastest solved instance, per mode."""
    parts = _header(title, "instances solved", "runtime per instance (log scale)")
    solved = {k: sorted(v) for k, v in series.items() if v}
    if not solved:
        parts.append("</svg>")
        path.write_text("\n".join(parts), encoding="utf-8")
        return
    max_n = max(len(v) for v in solved.values())
    lo = max(0.1, min(min(v) for v in solved.values()))
    hi = max(timeout_ms, max(max(v) for v in solved.values()))

    def sx(n: int) -> float:
        return L + (W - L - R) * (n / max(1, max_n))

    def sy(value: float) -> float:
        span = math.log10(hi) - math.log10(lo)
        frac = (math.log10(max(value, lo)) - math.log10(lo)) / (span or 1)
        return (H - B) - frac * (H - T - B)

    for tick in _log_ticks(lo, hi):
        y = sy(tick)
        if T <= y <= H - B:
            parts.append(f'<line x1="{L}" y1="{y:.1f}" x2="{W-R}" y2="{y:.1f}" '
                         f'stroke="#ededed"/>')
            parts.append(f'<text x="{L-8}" y="{y+4:.1f}" text-anchor="end" '
                         f'font-size="11">{_fmt_ms(tick)}</text>')
    for step in range(0, 6):
        n = round(max_n * step / 5)
        x = sx(n)
        parts.append(f'<text x="{x:.1f}" y="{H-B+18}" text-anchor="middle" '
                     f'font-size="11">{n}</text>')
    for index, (name, values) in enumerate(sorted(solved.items())):
        colour = PALETTE[index % len(PALETTE)]
        points = " ".join(f"{sx(i+1):.1f},{sy(v):.1f}" for i, v in enumerate(values))
        parts.append(f'<polyline points="{points}" fill="none" stroke="{colour}" '
                     f'stroke-width="2"/>')
        y = T + 16 + index * 18
        parts.append(f'<line x1="{W-R-190}" y1="{y-4}" x2="{W-R-170}" y2="{y-4}" '
                     f'stroke="{colour}" stroke-width="3"/>')
        parts.append(f'<text x="{W-R-164}" y="{y}" font-size="12">'
                     f'{_esc(name)} ({len(values)})</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def scatter(path: Path, pairs: Iterable[tuple[float, float, str]], x_name: str,
            y_name: str, timeout_ms: float, title: str | None = None) -> None:
    """Per-instance runtime of one mode against another, with a parity line."""
    pairs = list(pairs)
    title = title or f"{y_name} vs {x_name} (per instance)"
    parts = _header(title, f"{x_name} runtime (log)", f"{y_name} runtime (log)")
    lo, hi = 0.1, max(timeout_ms * 2, 1.0)
    if pairs:
        lo = max(0.05, min(min(x for x, _, _ in pairs), min(y for _, y, _ in pairs)))

    def scale(value: float, axis: str) -> float:
        span = math.log10(hi) - math.log10(lo)
        frac = (math.log10(min(max(value, lo), hi)) - math.log10(lo)) / (span or 1)
        return L + frac * (W - L - R) if axis == "x" else (H - B) - frac * (H - T - B)

    for tick in _log_ticks(lo, hi):
        x, y = scale(tick, "x"), scale(tick, "y")
        if L <= x <= W - R:
            parts.append(f'<line x1="{x:.1f}" y1="{T}" x2="{x:.1f}" y2="{H-B}" stroke="#f0f0f0"/>')
            parts.append(f'<text x="{x:.1f}" y="{H-B+18}" text-anchor="middle" '
                         f'font-size="11">{_fmt_ms(tick)}</text>')
        if T <= y <= H - B:
            parts.append(f'<line x1="{L}" y1="{y:.1f}" x2="{W-R}" y2="{y:.1f}" stroke="#f0f0f0"/>')
            parts.append(f'<text x="{L-8}" y="{y+4:.1f}" text-anchor="end" '
                         f'font-size="11">{_fmt_ms(tick)}</text>')
    parts.append(f'<line x1="{L}" y1="{H-B}" x2="{W-R}" y2="{T}" stroke="#999" '
                 f'stroke-dasharray="5,4"/>')
    to = scale(timeout_ms, "x")
    parts.append(f'<line x1="{to:.1f}" y1="{T}" x2="{to:.1f}" y2="{H-B}" stroke="#d62728" '
                 f'stroke-dasharray="3,3" opacity="0.5"/>')
    for x, y, label in pairs:
        parts.append(f'<circle cx="{scale(x, "x"):.1f}" cy="{scale(y, "y"):.1f}" r="3" '
                     f'fill="#1f77b4" fill-opacity="0.45"><title>{_esc(label)}</title></circle>')
    below = sum(1 for x, y, _ in pairs if y < x)
    parts.append(f'<text x="{L+12}" y="{T+20}" font-size="12">below the diagonal: '
                 f'{below}/{len(pairs)} faster for {_esc(y_name)}</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def histogram(path: Path, values: Sequence[float], title: str, x_label: str,
              bins: int = 40) -> None:
    """Distribution plot, used for speedup and overhead spreads."""
    parts = _header(title, x_label, "instances")
    values = [v for v in values if v is not None and math.isfinite(v)]
    if not values:
        parts.append(f'<text x="{W/2}" y="{H/2}" text-anchor="middle" font-size="13">'
                     f'no data</text></svg>')
        path.write_text("\n".join(parts), encoding="utf-8")
        return
    lo, hi = min(values), max(values)
    if hi <= lo:
        hi = lo + 1
    width = (hi - lo) / bins
    counts = [0] * bins
    for value in values:
        counts[min(bins - 1, int((value - lo) / width))] += 1
    peak = max(counts) or 1
    for index, count in enumerate(counts):
        x = L + (W - L - R) * index / bins
        bar = (W - L - R) / bins
        height = (H - T - B) * count / peak
        parts.append(f'<rect x="{x:.1f}" y="{H-B-height:.1f}" width="{bar-1:.1f}" '
                     f'height="{height:.1f}" fill="#1f77b4" fill-opacity="0.75"/>')
    for step in range(6):
        value = lo + (hi - lo) * step / 5
        x = L + (W - L - R) * step / 5
        parts.append(f'<text x="{x:.1f}" y="{H-B+18}" text-anchor="middle" '
                     f'font-size="11">{value:.2f}</text>')
    parts.append(f'<text x="{L+12}" y="{T+20}" font-size="12">n={len(values)}, '
                 f'median={sorted(values)[len(values)//2]:.3f}</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def write_all(directory: Path, cactus_series: dict[str, Sequence[float]],
              scatters: dict[str, Any], speedups: Sequence[float],
              overheads: Sequence[float], timeout_ms: float) -> list[str]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    cactus(directory / "cactus.svg", cactus_series, timeout_ms)
    written.append("cactus.svg")
    for name, payload in scatters.items():
        scatter(directory / f"scatter_{name}.svg", payload["pairs"],
                payload["x_name"], payload["y_name"], timeout_ms)
        written.append(f"scatter_{name}.svg")
    histogram(directory / "structural_speedup.svg", speedups,
              "Speedup on the structural subset (LKK vs best baseline)",
              "speedup (x, >1 favours LKK)")
    written.append("structural_speedup.svg")
    histogram(directory / "no_structure_overhead.svg", overheads,
              "LKK overhead on instances with no detected structure",
              "overhead (ms)")
    written.append("no_structure_overhead.svg")
    return written
