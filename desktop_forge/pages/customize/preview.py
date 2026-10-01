"""A drawn sketch of a desktop look, so a profile can be judged before applying it.

Pure cairo: `draw(cr, width, height, values, accent, dark)` draws the top
bar, dock, windows (tiled or floating), gaps, corners, borders and shadows
the values describe. Sizes are exaggerated a little so a 2px border or an
8px gap is still visible at thumbnail size.
"""
from __future__ import annotations

import math

import cairo

SCREEN_WIDTH = 1280
EXAGGERATE = 1.8


def _rgb(color: str, fallback=(0.2, 0.5, 0.9)):
    try:
        return tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    except (TypeError, ValueError, IndexError):
        return fallback


def _rounded(cr, x, y, w, h, r):
    r = max(0.0, min(r, w / 2, h / 2))
    if r <= 0.1:
        cr.rectangle(x, y, w, h)
        return
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def tile(layout: str, x, y, w, h, gap, ratio) -> list[tuple]:
    """Window rectangles for a tiling layout inside (x, y, w, h)."""
    half = gap / 2
    if layout == "monocle":
        return [(x, y, w, h)]
    if layout == "columns":
        cw = (w - 2 * gap) / 3
        return [(x + i * (cw + gap), y, cw, h) for i in range(3)]
    if layout == "rows":
        rh = (h - 2 * gap) / 3
        return [(x, y + i * (rh + gap), w, rh) for i in range(3)]
    if layout == "grid":
        cw, rh = (w - gap) / 2, (h - gap) / 2
        return [(x + c * (cw + gap), y + r * (rh + gap), cw, rh) for r in range(2) for c in range(2)]
    if layout == "dwindle":
        left = w / 2 - half
        right_x, right_w = x + left + gap, w - left - gap
        top = h / 2 - half
        bottom_y, bottom_h = y + top + gap, h - top - gap
        small = right_w / 2 - half
        return [(x, y, left, h), (right_x, y, right_w, top), (right_x, bottom_y, small, bottom_h),
                (right_x + small + gap, bottom_y, right_w - small - gap, bottom_h)]
    if layout == "centered":
        main = (w - 2 * gap) * ratio
        side = (w - 2 * gap - main) / 2
        side_h = (h - gap) / 2
        return [(x + side + gap, y, main, h), (x + side + gap + main + gap, y, side, h),
                (x, y, side, side_h), (x, y + side_h + gap, side, side_h)]
    if layout == "scrolling":
        # Two columns on screen and the strip going on past the edge.
        cw = (w - 2 * gap) * 0.46
        return [(x, y, cw, h), (x + cw + gap, y, cw, h), (x + 2 * (cw + gap), y, w - 2 * (cw + gap), h)]
    # "master", and anything unknown: main and stack.
    main = w * ratio - half
    side_x, side_w = x + main + gap, w - main - gap
    side_h = (h - gap) / 2
    return [(x, y, main, h), (side_x, y, side_w, side_h), (side_x, y + side_h + gap, side_w, side_h)]


def draw(cr, width: int, height: int, values: dict, accent: str = "#3584e4", dark: bool = False) -> None:
    v = values.get
    scale = width / SCREEN_WIDTH
    px = lambda value: value * scale * EXAGGERATE  # noqa: E731
    ar, ag, ab = _rgb(accent)

    # Wallpaper: a soft gradient in the accent colour, dimmed as set.
    gradient = cairo.LinearGradient(0, 0, width, height)
    gradient.add_color_stop_rgb(0, ar * 0.55 + 0.25, ag * 0.55 + 0.25, ab * 0.55 + 0.3)
    gradient.add_color_stop_rgb(1, ar * 0.3, ag * 0.3, ab * 0.35 + 0.1)
    cr.set_source(gradient)
    cr.paint()
    if v("wallpaper.dim", 0):
        cr.set_source_rgba(0, 0, 0, v("wallpaper.dim", 0))
        cr.paint()

    area = [0.0, 0.0, float(width), float(height)]

    # Top bar.
    bar_h = max(4.0, px(v("chrome.top_bar.height", 32)) * 0.7)
    bar_bottom = v("chrome.top_bar.position", "top") == "bottom"
    floating = v("top_bar.floating", False)
    margin = px(v("top_bar.margin", 6)) if floating else 0
    bar_color = v("chrome.top_bar.background") or ("#18181b" if dark else "#fafafb")
    br, bg, bb = _rgb(bar_color)
    shown = v("chrome.top_bar.visibility", "always") == "always"
    bar_alpha = v("chrome.top_bar.opacity", 0.96) * (1 if shown else 0.35)
    bar_y = height - bar_h - margin if bar_bottom else margin
    _rounded(cr, margin, bar_y, width - 2 * margin, bar_h, px(v("top_bar.radius", 12)) if floating else 0)
    cr.set_source_rgba(br, bg, bb, bar_alpha)
    cr.fill()
    fg = 0.1 if sum((br, bg, bb)) > 1.5 else 0.95
    cr.set_source_rgba(fg, fg, fg, 0.8 * (1 if shown else 0.4))
    clock_w = bar_h * 2.2
    clock_x = {"left": margin + bar_h * 2.2, "right": width - margin - clock_w - bar_h * 2.5}.get(
        v("top_bar.clock_position", "center"), (width - clock_w) / 2)
    _rounded(cr, clock_x, bar_y + bar_h * 0.35, clock_w, bar_h * 0.3, bar_h * 0.15)
    cr.fill()
    if v("top_bar.show_activities", True):
        for i in range(3):
            cr.arc(margin + bar_h * (0.7 + 0.45 * i), bar_y + bar_h / 2, bar_h * (0.16 if i else 0.2), 0,
                   2 * math.pi)
            cr.fill()
    if shown:
        if bar_bottom:
            area[3] -= bar_h + margin
        else:
            area[1] += bar_h + margin
            area[3] -= bar_h + margin

    # Dock.
    position = v("dock.position", "BOTTOM")
    extend = v("dock.extend", False)
    fixed = v("dock.visibility", "intelligent") == "always"
    icon = px(48) * (0.55 if v("dock.shrink", False) else 0.7)
    thickness = icon * 1.35
    icons = 6
    along = width if position in ("BOTTOM", "TOP") else height
    length = along * (v("dock.length", 0.9) if extend else 0) or icons * icon * 1.25 + icon * 0.5
    dock_color = v("chrome.dock.background") or ("#18202c" if dark else "#f8fbff")
    dr, dg, db = _rgb(dock_color)
    dock_alpha = v("chrome.dock.opacity", 0.92) * (1 if fixed else 0.55)
    inset = 0 if extend else icon * 0.25
    if position in ("BOTTOM", "TOP"):
        dx, dw, dh = (width - length) / 2, length, thickness
        dy = height - dh - inset if position == "BOTTOM" else area[1] + inset
    else:
        dw, dh = thickness, min(length, area[3])
        dy = area[1] + (area[3] - dh) / 2
        dx = inset if position == "LEFT" else width - dw - inset
    _rounded(cr, dx, dy, dw, dh, 0 if extend else thickness * 0.3)
    cr.set_source_rgba(dr, dg, db, dock_alpha)
    cr.fill()
    for i in range(icons):
        if position in ("BOTTOM", "TOP"):
            ix, iy = dx + icon * 0.35 + i * icon * 1.2, dy + (dh - icon) / 2
        else:
            ix, iy = dx + (dw - icon) / 2, dy + icon * 0.35 + i * icon * 1.2
        if ix + icon > dx + dw or iy + icon > dy + dh:
            break
        hue = (i * 0.17) % 1
        cr.set_source_rgba(0.35 + 0.5 * hue, 0.45 + 0.3 * (1 - hue), 0.85 - 0.4 * hue, 0.95)
        _rounded(cr, ix, iy, icon, icon, icon * 0.25)
        cr.fill()
    if fixed:
        if position == "BOTTOM":
            area[3] = min(area[3], dy - area[1])
        elif position == "LEFT":
            area[0], area[2] = dx + dw, area[2] - dw - dx
        elif position == "RIGHT":
            area[2] = dx - area[0]

    # Windows.
    radius = px(v("windows.corner_radius", 0)) or px(4)
    border = px(v("windows.border_width", 0))
    inactive_border = px(v("windows.inactive_border_width", 0))
    shadow = v("windows.shadow", "default")
    x, y, w, h = area
    if v("tiling.enabled", False):
        outer = px(v("tiling.gaps_outer", 8))
        inner = px(v("tiling.gaps_inner", 8))
        rects = tile(v("tiling.layout", "master"), x + outer, y + outer, w - 2 * outer, h - 2 * outer, inner,
                     v("tiling.master_ratio", 0.55))
    else:
        rects = [(x + w * 0.08, y + h * 0.12, w * 0.52, h * 0.62), (x + w * 0.36, y + h * 0.26, w * 0.52, h * 0.6)]
    focused = 0 if v("tiling.enabled", False) else len(rects) - 1
    window_bg = (0.16, 0.16, 0.18) if dark else (0.98, 0.98, 0.98)
    header_bg = (0.22, 0.22, 0.25) if dark else (0.92, 0.92, 0.93)
    for index, (rx, ry, rw, rh) in enumerate(rects):
        active = index == focused
        alpha = v("windows.active_opacity", 1.0) if active else v("windows.inactive_opacity", 1.0)
        if shadow != "none":
            strength = {"strong": 0.35, "subtle": 0.14}.get(shadow, 0.22)
            spread = {"strong": px(8), "subtle": px(2)}.get(shadow, px(4))
            _rounded(cr, rx - spread / 2, ry + spread / 3, rw + spread, rh + spread, radius + spread / 2)
            cr.set_source_rgba(0, 0, 0, strength * alpha * (1 if active else 0.6))
            cr.fill()
        _rounded(cr, rx, ry, rw, rh, radius)
        cr.set_source_rgba(*window_bg, alpha)
        cr.fill_preserve()
        cr.save()
        cr.clip()
        cr.set_source_rgba(*header_bg, alpha)
        cr.rectangle(rx, ry, rw, max(3.0, rh * 0.12))
        cr.fill()
        dim = v("windows.dim_inactive", 0.0)
        if dim and not active:
            cr.set_source_rgba(0, 0, 0, dim)
            cr.rectangle(rx, ry, rw, rh)
            cr.fill()
        cr.restore()
        width_ = border if active else inactive_border
        if width_:
            if active:
                color = accent if v("windows.border_accent", True) else v("windows.border_color", accent)
            else:
                color = v("windows.inactive_border_color", "#77767b")
            if active and v("windows.border_gradient", False):
                blend = cairo.LinearGradient(rx, ry, rx + rw, ry)
                blend.add_color_stop_rgb(0, *_rgb(color))
                blend.add_color_stop_rgb(1, *_rgb(v("windows.border_color2", "#00ff99")))
                cr.set_source(blend)
            else:
                cr.set_source_rgba(*_rgb(color), 1)
            cr.set_line_width(max(1.0, width_))
            _rounded(cr, rx + width_ / 2, ry + width_ / 2, rw - width_, rh - width_, max(0, radius - width_ / 2))
            cr.stroke()
