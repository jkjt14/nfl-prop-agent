"""Helpers for formatting scan diagnostics and summaries."""

from __future__ import annotations

from typing import Dict, List, Optional


def format_scan_diagnostics(diag: Optional[Dict], reason_limit: int = 10) -> List[str]:
    """Return formatted diagnostic lines from a scan result."""

    if not diag:
        return []

    lines: List[str] = []

    events = diag.get("events")
    events_used = diag.get("events_used")
    if events is not None and events_used is not None:
        lines.append(f"Events processed: {events_used}/{events}")

    est = diag.get("estimated_credits")
    if est is not None:
        lines.append(f"Estimated credits: {est}")

    markets_trimmed = diag.get("markets_trimmed")
    if markets_trimmed:
        lines.append("Markets trimmed for budget: " + ", ".join(markets_trimmed))

    markets_effective = diag.get("markets_effective")
    if markets_effective:
        lines.append("Markets used: " + ", ".join(markets_effective))

    target_books = diag.get("target_books")
    if target_books:
        lines.append("Target books: " + ", ".join(target_books))

    seen = diag.get("bookmakers_encountered") or []
    if seen:
        lines.append("Bookmakers encountered: " + ", ".join(seen))

    offers_by_book = diag.get("offers_by_book") or {}
    if offers_by_book:
        parts = [
            f"{book}={count}"
            for book, count in sorted(offers_by_book.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        lines.append("Offers by target book: " + ", ".join(parts))

    fallback_counts = diag.get("fallback_counts") or {}
    if fallback_counts:
        parts = [
            f"{book}={count}"
            for book, count in sorted(fallback_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        lines.append("Fallback outside target books: " + ", ".join(parts))

    missing_events = diag.get("events_missing_bookmakers") or []
    if missing_events:
        sample = ", ".join(str(ev.get("event_id")) for ev in missing_events[:5])
        lines.append(f"Events missing bookmakers: {len(missing_events)} (sample {sample})")

    reasons = diag.get("reasons") or {}
    if reasons:
        lines.append("Top skip reasons:")
        for reason, count in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))[:reason_limit]:
            lines.append(f"  {reason}: {count}")

    missing_proj = diag.get("missing_projection_values") or {}
    if missing_proj:
        lines.append("Missing projection counts:")
        for market, count in sorted(missing_proj.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"  {market}: {count}")

    return lines


def format_no_edge_summary(
    diag: Optional[Dict], *, reason_limit: int = 5, heading: Optional[str] = "Diagnostics:"
) -> str:
    """Return a short multi-line summary explaining why no edges surfaced."""

    diag_lines = format_scan_diagnostics(diag, reason_limit=reason_limit)
    if not diag_lines:
        return ""

    lines: List[str] = []
    if heading:
        lines.append(str(heading))
    lines.extend(diag_lines)
    return "\n".join(lines)

