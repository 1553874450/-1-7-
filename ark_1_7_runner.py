#!/usr/bin/env python3
"""
ADB runner for Arknights 1-7 on MuMu.

This script follows the route taught by the screenshots:
home -> Terminal -> main story -> EP01 Part 2 -> chapter -> 1-7.

If the emulator is sitting on the Android launcher, the script can launch
Arknights first, tap through the title/login screens, and then continue.

It then loops 1-7 using delegated command. When sanity is insufficient and
the recovery dialog appears, it only uses the selected sanity medicine if its
time tag looks like an expiring red/pink item. Green long-duration medicine is
closed and left unused.
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image
import numpy as np


BASE_W = 1920
BASE_H = 1080
DEFAULT_DELEGATE_RUNS = 10
DEFAULT_SANITY_COST = 60

TEMPLATE_DIR = Path(__file__).resolve().parent


POINTS = {
    # Title/login screens.
    "title_start": (960, 1025),
    "title_awaken": (960, 825),
    "notice_close": (1820, 148),
    "popup_dismiss": (360, 540),
    "top_left_back": (70, 60),
    "global_menu": (410, 60),
    "global_menu_home": (170, 405),
    "delegate_counter": (1535, 890),
    "delegate_picker_selected": (1510, 635),
    "delegate_picker_close_area": (1280, 635),
    "delegate_picker_swipe_upper": (1510, 555),
    "delegate_picker_swipe_lower": (1510, 760),
    # Screenshot #7 -> #6
    "home_terminal": (1500, 285),
    # Screenshot #6 -> #5
    "terminal_main_story": (490, 1005),
    # Screenshot #5 -> #4
    "ep01_part2": (1345, 640),
    # Screenshot #4 -> #3
    "go_to_chapter": (1645, 925),
    # Screenshot #3 -> #2
    "stage_1_7": (960, 340),
    # Horizontal swipe bands (finger moves right = reveal earlier items;
    # finger moves left = reveal later items).
    "ep_row_swipe_right_start": (1350, 620),
    "ep_row_swipe_right_end": (450, 620),
    "ep_row_swipe_left_start": (450, 620),
    "ep_row_swipe_left_end": (1350, 620),
    "stage_swipe_right_start": (1350, 520),
    "stage_swipe_right_end": (450, 520),
    "stage_swipe_left_start": (450, 520),
    "stage_swipe_left_end": (1350, 520),
    # Screenshot #2 loop action
    "start_action": (1725, 985),
    # Squad confirmation page.
    "squad_start": (1660, 760),
    # Screenshot #1 recovery dialog
    "potion_confirm": (1635, 865),
    "potion_cancel": (1170, 865),
    # Generic result-screen taps.
    "result_continue": (1660, 1000),
    "result_center": (960, 540),
}


REGIONS = {
    "home_top_icons": (20, 20, 520, 130),
    "home_friend_archive": (430, 830, 700, 1030),
    "home_terminal_card": (1070, 125, 1600, 430),
    "stage_top_nav": (20, 20, 650, 125),
    "stage_right_panel": (1450, 130, 1910, 520),
    "stage_bottom_controls": (1420, 830, 1900, 1060),
    "stage_blue_button": (1570, 890, 1885, 1010),
    "delegate_count_digits": (1510, 850, 1605, 920),
    "delegate_picker_panel": (1380, 330, 1640, 860),
    "delegate_picker_selected": (1380, 560, 1640, 675),
    "delegate_picker_selected_digits": (1460, 560, 1590, 665),
    "title_awaken_button": (760, 660, 1160, 850),
    "title_start_diamond": (880, 940, 1040, 1070),
    "notice_close_region": (1750, 90, 1900, 210),
    "notice_panel": (90, 90, 1840, 995),
    "global_menu_band": (30, 110, 1900, 520),
    "global_menu_home_item": (80, 320, 250, 460),
    "recruit_bottom_buttons": (1180, 830, 1810, 1025),
    "loading_bottom_text": (500, 930, 1420, 1060),
    "start_button": (1540, 920, 1880, 1045),
    "squad_start_button": (1550, 550, 1760, 970),
    "result_left_text": (80, 250, 430, 430),
    "result_rank_icons": (100, 430, 410, 540),
    "recovery_dialog": (910, 110, 1910, 930),
    "first_potion_card": (960, 290, 1225, 570),
    "first_potion_time_tag": (1010, 515, 1225, 570),
}


@dataclass
class Config:
    mode: str
    mumu_cli: Path
    adb: Path
    vm_name: str
    vmindex: str | None
    serial: str | None
    arknights_package: str
    auto_launch: bool
    hide_window: bool
    dry_run: bool
    debug_dir: Path
    max_runs: int | None
    delegate_runs: int
    sanity_cost: int
    launch_timeout: int
    battle_timeout: int
    poll_seconds: int
    boot_timeout: int


def decode_process_output(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gbk", "mbcs"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def repair_powershell_mojibake(value: str) -> str:
    try:
        repaired = value.encode("gbk").decode("utf-8")
    except UnicodeError:
        return value
    return repaired if repaired else value


def run(args: list[str], *, text: bool = True, timeout: int = 30) -> str | bytes:
    completed = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        timeout=timeout,
    )
    if completed.returncode != 0:
        stderr = decode_process_output(completed.stderr).strip()
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(args)}\n{stderr}")
    if text:
        return decode_process_output(completed.stdout)
    return completed.stdout


def log(message: str) -> None:
    print(time.strftime("[%H:%M:%S]"), message, flush=True)


def discover_vmindex(config: Config) -> str:
    if config.vmindex:
        return config.vmindex

    raw = run([str(config.mumu_cli), "info", "--vmindex", "all"], timeout=15)
    data = json.loads(raw)
    vm_names = {config.vm_name, repair_powershell_mojibake(config.vm_name)}
    for index, item in data.items():
        if item.get("name") in vm_names:
            log(f"Found MuMu vm '{item.get('name')}' at vmindex={index}.")
            return str(index)

    names = ", ".join(f"{idx}:{item.get('name')}" for idx, item in data.items())
    raise RuntimeError(f"Could not find MuMu vm named {config.vm_name!r}. Seen: {names}")


def serial_for_vm(config: Config, vmindex: str) -> str:
    if config.serial:
        return config.serial

    raw = run([str(config.mumu_cli), "info", "--vmindex", vmindex], timeout=15)
    info = json.loads(raw)
    host = info.get("adb_host_ip", "127.0.0.1")
    port = info.get("adb_port")
    if not port:
        raise RuntimeError(f"MuMu vmindex={vmindex} did not report an adb_port.")
    serial = f"{host}:{port}"
    run([str(config.adb), "connect", serial], timeout=15)
    log(f"Using adb serial {serial}.")
    return serial


def mumu_control(config: Config, vmindex: str, command: str) -> None:
    if config.dry_run:
        log(f"DRY RUN: mumu control {command}")
        return
    run([str(config.mumu_cli), "control", "--vmindex", vmindex, command], timeout=20)


def mumu_main_launch(config: Config) -> None:
    if config.dry_run:
        log("DRY RUN: mumu main launch")
        return
    run([str(config.mumu_cli), "main", "launch"], timeout=30)


def vm_info(config: Config, vmindex: str) -> dict:
    raw = run([str(config.mumu_cli), "info", "--vmindex", vmindex], timeout=15)
    return json.loads(raw)


def ensure_emulator_running(config: Config, vmindex: str) -> None:
    """Start MuMu main app + the named VM if they are not already up, then wait
    for Android to finish booting. Safe to call when everything is already running."""
    try:
        info = vm_info(config, vmindex)
    except Exception as exc:
        log(f"Could not query VM state ({exc}); assuming it needs launching.")
        info = {}

    started = bool(info.get("is_process_started")) and bool(info.get("is_android_started"))
    if started and info.get("player_state") == "start_finished":
        log("MuMu VM already running and booted.")
        return

    if not started:
        log("MuMu VM not running; launching MuMu main application.")
        mumu_main_launch(config)
        time.sleep(3)

    log(f"Starting MuMu VM index={vmindex}.")
    if not config.dry_run:
        run([str(config.mumu_cli), "control", "--vmindex", vmindex, "launch"], timeout=30)

    deadline = time.monotonic() + config.boot_timeout
    last = ""
    while time.monotonic() < deadline:
        time.sleep(3)
        try:
            info = vm_info(config, vmindex)
        except Exception:
            continue
        state = info.get("player_state")
        if info.get("is_android_started") and state == "start_finished":
            log(f"MuMu VM booted: player_state={state}.")
            return
        if state != last:
            log(f"Waiting for VM to boot... player_state={state}.")
            last = state
    raise RuntimeError("Timed out waiting for MuMu VM to boot.")


def adb(config: Config, serial: str, args: list[str], *, text: bool = True, timeout: int = 30) -> str | bytes:
    return run([str(config.adb), "-s", serial, *args], text=text, timeout=timeout)


def current_focus(config: Config, serial: str) -> str:
    output = adb(config, serial, ["shell", "dumpsys", "window"], timeout=20)
    assert isinstance(output, str)
    for line in output.splitlines():
        if "mCurrentFocus" in line or "mFocusedApp" in line:
            if config.arknights_package in line:
                return config.arknights_package
    return ""


def launch_arknights(config: Config, serial: str) -> None:
    log(f"Launching Arknights package {config.arknights_package}.")
    if config.dry_run:
        log("DRY RUN: adb shell monkey launch")
        return
    adb(
        config,
        serial,
        [
            "shell",
            "monkey",
            "-p",
            config.arknights_package,
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
        ],
        timeout=20,
    )


def screenshot(config: Config, serial: str) -> Image.Image:
    data = adb(config, serial, ["exec-out", "screencap", "-p"], text=False, timeout=20)
    assert isinstance(data, bytes)
    return Image.open(io.BytesIO(data)).convert("RGB")


def save_debug(config: Config, image: Image.Image, name: str) -> None:
    config.debug_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    image.save(config.debug_dir / f"{stamp}_{name}.png")


def scale_point(image: Image.Image, point: tuple[int, int]) -> tuple[int, int]:
    x, y = point
    return round(x * image.width / BASE_W), round(y * image.height / BASE_H)


def scale_region(image: Image.Image, region: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = region
    return (
        round(x1 * image.width / BASE_W),
        round(y1 * image.height / BASE_H),
        round(x2 * image.width / BASE_W),
        round(y2 * image.height / BASE_H),
    )


def tap(config: Config, serial: str, image: Image.Image, name: str, delay: float = 1.0) -> None:
    x, y = scale_point(image, POINTS[name])
    if config.dry_run:
        log(f"DRY RUN: tap {name} at {x},{y}")
    else:
        adb(config, serial, ["shell", "input", "tap", str(x), str(y)], timeout=10)
    time.sleep(delay)


def swipe(
    config: Config,
    serial: str,
    image: Image.Image,
    start_name: str,
    end_name: str,
    duration_ms: int = 350,
    delay: float = 0.9,
) -> None:
    x1, y1 = scale_point(image, POINTS[start_name])
    x2, y2 = scale_point(image, POINTS[end_name])
    if config.dry_run:
        log(f"DRY RUN: swipe {start_name}->{end_name} at {x1},{y1} to {x2},{y2}")
    else:
        adb(
            config,
            serial,
            ["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)],
            timeout=10,
        )
    time.sleep(delay)


def tap_xy(config: Config, serial: str, x: int, y: int, delay: float = 1.0) -> None:
    if config.dry_run:
        log(f"DRY RUN: tap dynamic {x},{y}")
    else:
        adb(config, serial, ["shell", "input", "tap", str(x), str(y)], timeout=10)
    time.sleep(delay)


def swipe_xy(
    config: Config,
    serial: str,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    duration_ms: int = 350,
    delay: float = 1.0,
) -> None:
    if config.dry_run:
        log(f"DRY RUN: swipe {x1},{y1} -> {x2},{y2}")
    else:
        adb(
            config,
            serial,
            ["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)],
            timeout=10,
        )
    time.sleep(delay)


def _gray_np(image: Image.Image, scale: float) -> np.ndarray:
    im = image.convert("L")
    if scale != 1.0:
        im = im.resize(
            (max(1, int(im.width * scale)), max(1, int(im.height * scale))),
            Image.BILINEAR,
        )
    return np.asarray(im, dtype=np.float32)


def match_template(
    image: Image.Image,
    template: Image.Image,
    threshold: float = 0.80,
    scale: float = 0.5,
    step: int = 2,
) -> tuple[int, int, float] | None:
    """Normalized cross-correlation search. Returns (center_x, center_y, score)
    in original 1920x1080 coordinates, or None when nothing exceeds threshold."""
    g = _gray_np(image, scale)
    t = _gray_np(template, scale)
    th, tw = t.shape
    gh, gw = g.shape
    if gh < th or gw < tw:
        return None
    t = t - t.mean()
    tnorm = float(np.sqrt((t * t).sum())) + 1e-6
    best_score, best_x, best_y = -1.0, 0, 0
    for y in range(0, gh - th + 1, step):
        for x in range(0, gw - tw + 1, step):
            win = g[y : y + th, x : x + tw]
            w = win - win.mean()
            wnorm = float(np.sqrt((w * w).sum())) + 1e-6
            s = float((w * t).sum() / (wnorm * tnorm))
            if s > best_score:
                best_score, best_x, best_y = s, x, y
    r = step * 2
    for yy in range(max(0, best_y - r), min(gh - th, best_y + r) + 1):
        for xx in range(max(0, best_x - r), min(gw - tw, best_x + r) + 1):
            win = g[yy : yy + th, xx : xx + tw]
            w = win - win.mean()
            wnorm = float(np.sqrt((w * w).sum())) + 1e-6
            s = float((w * t).sum() / (wnorm * tnorm))
            if s > best_score:
                best_score, best_x, best_y = s, xx, yy
    if best_score < threshold:
        return None
    cx = int((best_x + tw / 2) / scale)
    cy = int((best_y + th / 2) / scale)
    return cx, cy, best_score


def load_template(name: str) -> Image.Image:
    return Image.open(TEMPLATE_DIR / name).convert("RGB")


def crop_pixels(image: Image.Image, region_name: str) -> list[tuple[int, int, int]]:
    crop = image.crop(scale_region(image, REGIONS[region_name]))
    return list(crop.getdata())


def fraction(pixels: Iterable[tuple[int, int, int]], predicate) -> float:
    pixels = list(pixels)
    if not pixels:
        return 0.0
    return sum(1 for pixel in pixels if predicate(pixel)) / len(pixels)


def bright_components(image: Image.Image, region_name: str) -> list[tuple[int, int, int, int, int]]:
    crop = image.crop(scale_region(image, REGIONS[region_name]))
    pixels = crop.load()
    width, height = crop.size
    mask = [[min(pixels[x, y]) > 165 for x in range(width)] for y in range(height)]
    seen = [[False] * width for _ in range(height)]
    components: list[tuple[int, int, int, int, int]] = []

    for y in range(height):
        for x in range(width):
            if not mask[y][x] or seen[y][x]:
                continue

            stack = [(x, y)]
            seen[y][x] = True
            xs: list[int] = []
            ys: list[int] = []
            while stack:
                cx, cy = stack.pop()
                xs.append(cx)
                ys.append(cy)
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if 0 <= nx < width and 0 <= ny < height and mask[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        stack.append((nx, ny))

            components.append((len(xs), min(xs), min(ys), max(xs) + 1, max(ys) + 1))

    return components


def delegate_digit_component_count(image: Image.Image) -> int:
    count = 0
    for area, x1, y1, x2, y2 in bright_components(image, "delegate_count_digits"):
        width = x2 - x1
        height = y2 - y1
        touches_right_edge = x2 >= 93
        if area >= 30 and 4 <= width <= 36 and 18 <= height <= 60 and x1 < 75 and not touches_right_edge:
            count += 1
    return count


def delegate_picker_digit_component_count(image: Image.Image) -> int:
    count = 0
    for area, x1, y1, x2, y2 in bright_components(image, "delegate_picker_selected_digits"):
        width = x2 - x1
        height = y2 - y1
        touches_edge = x2 >= 120 or y2 >= 100
        if area >= 30 and 4 <= width <= 36 and 18 <= height <= 70 and x1 < 100 and not touches_edge:
            count += 1
    return count


def delegate_count_looks_like_target(image: Image.Image, target: int) -> bool:
    digit_count = delegate_digit_component_count(image)
    if target >= 10:
        return digit_count >= 2
    return digit_count == 1


def delegate_picker_looks_like_target(image: Image.Image, target: int) -> bool:
    digit_count = delegate_picker_digit_component_count(image)
    if target >= 10:
        return digit_count >= 2
    return digit_count == 1


def looks_like_delegate_picker_open(image: Image.Image) -> bool:
    panel = crop_pixels(image, "delegate_picker_panel")
    selected = crop_pixels(image, "delegate_picker_selected")
    panel_dark = fraction(panel, lambda p: max(p) < 80)
    selected_blue = fraction(selected, lambda p: p[2] > 55 and p[1] > 45 and p[0] < 55)
    return panel_dark > 0.88 and selected_blue > 0.35


def looks_like_stage_page(image: Image.Image) -> bool:
    if looks_like_home_page(image):
        return False

    top = crop_pixels(image, "stage_top_nav")
    right = crop_pixels(image, "stage_right_panel")
    bottom = crop_pixels(image, "stage_bottom_controls")
    button = crop_pixels(image, "stage_blue_button")

    top_dark = fraction(top, lambda p: max(p) < 80)
    top_white = fraction(top, lambda p: min(p) > 190)
    right_dark = fraction(right, lambda p: max(p) < 80)
    right_white = fraction(right, lambda p: min(p) > 190)
    bottom_cyan = fraction(bottom, lambda p: p[2] > 110 and p[1] > 100 and p[0] < 170)
    button_cyan = fraction(button, lambda p: p[2] > 110 and p[1] > 100 and p[0] < 170)
    button_white = fraction(button, lambda p: min(p) > 190)

    return (
        top_dark > 0.45
        and top_white < 0.08
        and right_dark > 0.55
        and right_white < 0.04
        and bottom_cyan > 0.18
        and button_cyan > 0.25
        and button_white > 0.02
    )


def looks_like_home_page(image: Image.Image) -> bool:
    top = crop_pixels(image, "home_top_icons")
    archive = crop_pixels(image, "home_friend_archive")
    terminal = crop_pixels(image, "home_terminal_card")

    top_white = fraction(top, lambda p: min(p) > 190)
    archive_white = fraction(archive, lambda p: min(p) > 190)
    terminal_white = fraction(terminal, lambda p: min(p) > 190)
    terminal_dark = fraction(terminal, lambda p: max(p) < 80)

    terminal_card = terminal_dark > 0.45 and terminal_white > 0.04
    return top_white > 0.10 and (archive_white > 0.15 or terminal_card)


def looks_like_title_awaken_page(image: Image.Image) -> bool:
    pixels = crop_pixels(image, "title_awaken_button")
    gray = fraction(pixels, lambda p: 70 < p[0] < 190 and 70 < p[1] < 190 and 70 < p[2] < 190)
    white = fraction(pixels, lambda p: min(p) > 190)
    dark = fraction(pixels, lambda p: max(p) < 80)
    return gray > 0.22 and white > 0.035 and dark < 0.45


def looks_like_title_start_page(image: Image.Image) -> bool:
    pixels = crop_pixels(image, "title_start_diamond")
    yellow = fraction(pixels, lambda p: p[0] > 160 and p[1] > 130 and p[2] < 90)
    dark = fraction(pixels, lambda p: max(p) < 80)
    return yellow > 0.035 and dark > 0.45


def looks_like_loading_page(image: Image.Image) -> bool:
    pixels = crop_pixels(image, "loading_bottom_text")
    white = fraction(pixels, lambda p: min(p) > 190)
    dark = fraction(pixels, lambda p: max(p) < 80)
    return white > 0.035 and dark > 0.70 and not looks_like_title_start_page(image)


def looks_like_notice_page(image: Image.Image) -> bool:
    close = crop_pixels(image, "notice_close_region")
    panel = crop_pixels(image, "notice_panel")
    close_gray = fraction(close, lambda p: 80 < p[0] < 190 and 80 < p[1] < 190 and 80 < p[2] < 190)
    close_dark = fraction(close, lambda p: max(p) < 80)
    panel_dark = fraction(panel, lambda p: max(p) < 80)
    panel_bright = fraction(panel, lambda p: max(p) > 180)
    return close_gray > 0.25 and close_dark > 0.25 and panel_dark > 0.35 and panel_bright > 0.08


def looks_like_activity_popup(image: Image.Image) -> bool:
    """Generic full-screen activity announcement after daily server reset.
    These stack one after another; the loop closes them with the top-right X."""
    if looks_like_home_page(image) or looks_like_stage_page(image):
        return False
    if looks_like_title_start_page(image) or looks_like_title_awaken_page(image):
        return False
    close = crop_pixels(image, "notice_close_region")
    panel = crop_pixels(image, "notice_panel")
    close_dark = fraction(close, lambda p: max(p) < 115)
    close_gray = fraction(close, lambda p: 85 < p[0] < 205 and 85 < p[1] < 205 and 85 < p[2] < 205)
    panel_colorful = fraction(panel, lambda p: (max(p) - min(p)) > 25 and max(p) > 120)
    panel_bright = fraction(panel, lambda p: max(p) > 150)
    return (close_dark > 0.22 or close_gray > 0.22) and (panel_colorful > 0.18 or panel_bright > 0.20)


def looks_like_recruit_page(image: Image.Image) -> bool:
    if looks_like_home_page(image) or looks_like_stage_page(image):
        return False

    top = crop_pixels(image, "home_top_icons")
    buttons = crop_pixels(image, "recruit_bottom_buttons")
    top_dark = fraction(top, lambda p: max(p) < 80)
    yellow = fraction(buttons, lambda p: p[0] > 170 and p[1] > 145 and p[2] < 95)
    bright = fraction(buttons, lambda p: max(p) > 180)
    return top_dark > 0.50 and yellow > 0.12 and bright > 0.25


def looks_like_global_menu_open(image: Image.Image) -> bool:
    band = crop_pixels(image, "global_menu_band")
    home_item = crop_pixels(image, "global_menu_home_item")
    band_dark = fraction(band, lambda p: max(p) < 80)
    home_white = fraction(home_item, lambda p: min(p) > 190)
    home_dark = fraction(home_item, lambda p: max(p) < 80)
    return band_dark > 0.70 and home_dark > 0.65 and home_white > 0.075


def looks_like_blocking_popup(image: Image.Image) -> bool:
    if looks_like_loading_page(image) or looks_like_title_start_page(image) or looks_like_title_awaken_page(image):
        return False
    if looks_like_home_page(image) or looks_like_stage_page(image):
        return False

    panel = crop_pixels(image, "notice_panel")
    panel_bright = fraction(panel, lambda p: max(p) > 175)
    panel_gray = fraction(panel, lambda p: 80 < p[0] < 195 and 80 < p[1] < 195 and 80 < p[2] < 195)
    panel_dark = fraction(panel, lambda p: max(p) < 85)
    return panel_bright > 0.10 and panel_gray > 0.08 and panel_dark > 0.18


def looks_like_squad_page(image: Image.Image) -> bool:
    pixels = crop_pixels(image, "squad_start_button")
    orange = fraction(pixels, lambda p: p[0] > 150 and 40 < p[1] < 140 and p[2] < 70)
    white = fraction(pixels, lambda p: p[0] > 210 and p[1] > 210 and p[2] > 210)
    return orange > 0.18 and white > 0.05


def looks_like_result_page(image: Image.Image) -> bool:
    left = crop_pixels(image, "result_left_text")
    rank = crop_pixels(image, "result_rank_icons")
    left_white = fraction(left, lambda p: p[0] > 210 and p[1] > 210 and p[2] > 210)
    left_dark = fraction(left, lambda p: p[0] < 70 and p[1] < 70 and p[2] < 70)
    rank_cyan = fraction(rank, lambda p: p[1] > 150 and p[2] > 160 and p[0] < 120)
    return left_white > 0.12 and left_dark > 0.45 and rank_cyan > 0.12


def looks_like_recovery_dialog(image: Image.Image) -> bool:
    pixels = crop_pixels(image, "recovery_dialog")
    whiteish = fraction(pixels, lambda p: p[0] > 205 and p[1] > 205 and p[2] > 205)
    return whiteish > 0.28


def try_return_home_via_global_menu(config: Config, serial: str, image: Image.Image) -> bool:
    log("Trying global menu recovery to return home.")
    tap(config, serial, image, "global_menu", 1.0)
    image = screenshot(config, serial)
    if not looks_like_global_menu_open(image):
        log("Global menu did not open; leaving screen unchanged.")
        return False

    log("Global menu opened; tapping Home.")
    tap(config, serial, image, "global_menu_home", 2.0)
    return True


def first_potion_expires_soon(image: Image.Image) -> bool:
    tag = crop_pixels(image, "first_potion_time_tag")
    red_pink = fraction(tag, lambda p: p[0] > 130 and 35 < p[1] < 155 and 55 < p[2] < 190)
    green = fraction(tag, lambda p: p[1] > 120 and p[0] < 190 and p[2] < 170)
    white_text = fraction(tag, lambda p: p[0] > 200 and p[1] > 200 and p[2] > 200)
    log(
        "Potion expiry check: "
        f"red_pink={red_pink:.3f}, green={green:.3f}, white_text={white_text:.3f}"
    )
    return red_pink > 0.25 and white_text > 0.02 and red_pink > green * 1.4


def ensure_game_home(config: Config, serial: str) -> None:
    image = screenshot(config, serial)
    if looks_like_home_page(image):
        log("Arknights home page detected.")
        return
    if looks_like_stage_page(image):
        log("Already on the 1-7 page.")
        return

    if config.auto_launch and current_focus(config, serial) != config.arknights_package:
        launch_arknights(config, serial)
        time.sleep(5)

    deadline = time.monotonic() + config.launch_timeout
    next_status = 0.0
    next_menu_recovery = time.monotonic() + 30
    while time.monotonic() < deadline:
        image = screenshot(config, serial)
        if looks_like_home_page(image):
            log("Arknights home page detected.")
            return
        if looks_like_stage_page(image):
            log("Already on the 1-7 page.")
            return

        now = time.monotonic()
        if looks_like_title_awaken_page(image):
            log("Title awaken button detected; tapping.")
            tap(config, serial, image, "title_awaken", 2.0)
            continue

        if looks_like_title_start_page(image):
            log("START prompt detected; tapping.")
            tap(config, serial, image, "title_start", 0.7)
            continue

        if looks_like_notice_page(image):
            log("Daily notice page detected; closing it.")
            tap(config, serial, image, "notice_close", 2.0)
            continue

        if looks_like_activity_popup(image):
            log("Activity announcement popup detected; closing top-right X.")
            tap(config, serial, image, "notice_close", 1.6)
            continue

        if looks_like_recruit_page(image):
            log("Recruit/headhunting page detected during launch.")
            if try_return_home_via_global_menu(config, serial, image):
                next_menu_recovery = time.monotonic() + 30
                continue
            tap(config, serial, image, "top_left_back", 2.0)
            continue

        if looks_like_global_menu_open(image):
            log("Global menu is already open; tapping Home.")
            tap(config, serial, image, "global_menu_home", 2.0)
            continue

        if looks_like_blocking_popup(image):
            log("Blocking login popup detected; dismissing it.")
            tap(config, serial, image, "popup_dismiss", 2.0)
            continue

        if now >= next_menu_recovery and not looks_like_loading_page(image):
            if try_return_home_via_global_menu(config, serial, image):
                next_menu_recovery = time.monotonic() + 30
                continue
            next_menu_recovery = now + 30

        if now >= next_status:
            log("Waiting for Arknights loading/login screen to reach a clickable prompt.")
            next_status = now + 10

        time.sleep(2)

    save_debug(config, screenshot(config, serial), "launch_timeout")
    raise RuntimeError("Timed out waiting for Arknights home page.")


def locate_ep01_and_tap(config: Config, serial: str, template: Image.Image) -> None:
    """On the 曲谱 episode row, swipe horizontally until the EP01 card
    (黑暗时代下 / EVIL TIME PART.2) is visible, then tap it."""
    # First try the current screen, then swipe right (reveal earlier episodes),
    # then swipe left (reveal later episodes).
    directions = [
        (None, None, None, None),
        ("ep_row_swipe_right_start", "ep_row_swipe_right_end", "right", "earlier"),
        ("ep_row_swipe_left_start", "ep_row_swipe_left_end", "left", "later"),
    ]
    for start_n, end_n, _dir, label in directions:
        for attempt in range(6):
            image = screenshot(config, serial)
            hit = match_template(image, template, threshold=0.85)
            if hit is not None:
                cx, cy, score = hit
                log(f"EP01 card matched score={score:.2f} at ({cx},{cy}); tapping.")
                tap_xy(config, serial, cx, cy, delay=2.0)
                return
            if start_n is None:
                break
            log(f"EP01 not visible yet; swiping {label} (attempt {attempt + 1}).")
            swipe(config, serial, image, start_n, end_n, duration_ms=400, delay=1.0)
    save_debug(config, screenshot(config, serial), "ep01_not_found")
    raise RuntimeError("Could not locate the EP01 card on the episode row. Check debug screenshots.")


def locate_stage17_and_tap(config: Config, serial: str, template: Image.Image) -> None:
    """On the EP01 stage map, swipe horizontally until the 1-7 node is visible,
    then tap it."""
    directions = [
        (None, None, None, None),
        ("stage_swipe_right_start", "stage_swipe_right_end", "right", "earlier"),
        ("stage_swipe_left_start", "stage_swipe_left_end", "left", "later"),
    ]
    for start_n, end_n, _dir, label in directions:
        for attempt in range(6):
            image = screenshot(config, serial)
            hit = match_template(image, template, threshold=0.92)
            if hit is not None:
                cx, cy, score = hit
                # Only trust a match when the node is near the screen centre
                # (edge matches are the same-looking "OPERATION 1-x" bars on
                # adjacent stages). Keep swiping until 1-7 centres itself.
                if 650 <= cx <= 1250 and 250 <= cy <= 500:
                    log(f"1-7 node matched score={score:.2f} at ({cx},{cy}); tapping.")
                    tap_xy(config, serial, cx, cy, delay=2.5)
                    return
                log(f"1-7 match at edge ({cx},{cy}) score={score:.2f}; ignoring and continuing to swipe.")
            if start_n is None:
                break
            log(f"1-7 not visible yet; swiping {label} (attempt {attempt + 1}).")
            swipe(config, serial, image, start_n, end_n, duration_ms=400, delay=1.0)
    save_debug(config, screenshot(config, serial), "stage17_not_found")
    raise RuntimeError("Could not locate the 1-7 node on the stage map. Check debug screenshots.")


def find_1_7(config: Config, serial: str) -> None:
    image = screenshot(config, serial)
    if looks_like_stage_page(image):
        log("1-7 page already detected.")
        return

    ep01_tpl = load_template("ref_ep01_card.png")
    stage17_tpl = load_template("ref_stage17_bar.png")

    log("Navigating to 1-7: home -> Terminal -> 曲谱.")
    tap(config, serial, image, "home_terminal", 2.5)

    image = screenshot(config, serial)
    tap(config, serial, image, "terminal_main_story", 2.0)

    # Episode row: swipe to find EP01, then tap it.
    locate_ep01_and_tap(config, serial, ep01_tpl)

    # Episode detail page: tap 前往章节.
    image = screenshot(config, serial)
    tap(config, serial, image, "go_to_chapter", 2.5)

    # Stage map: swipe to find 1-7, then tap it.
    locate_stage17_and_tap(config, serial, stage17_tpl)

    image = screenshot(config, serial)
    save_debug(config, image, "after_find_1_7")
    if looks_like_stage_page(image):
        log("1-7 page detected.")
        return

    log("1-7 page was not confidently detected; tapping the fixed 1-7 point once more.")
    tap(config, serial, image, "stage_1_7", 2.0)
    image = screenshot(config, serial)
    save_debug(config, image, "after_find_1_7_retry")
    if not looks_like_stage_page(image):
        raise RuntimeError("Could not verify the 1-7 start page. Check debug screenshots.")


def wait_for_squad_or_recovery(config: Config, serial: str, timeout_seconds: int = 18) -> str:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        image = screenshot(config, serial)
        if looks_like_squad_page(image):
            return "squad"
        if looks_like_recovery_dialog(image):
            return "recovery"
        time.sleep(1)
    save_debug(config, screenshot(config, serial), "start_wait_timeout")
    return "unknown"


def wait_for_stage_page_after_battle(config: Config, serial: str) -> bool:
    deadline = time.monotonic() + config.battle_timeout
    next_fallback_tap = time.monotonic() + 90
    while time.monotonic() < deadline:
        time.sleep(config.poll_seconds)
        image = screenshot(config, serial)
        if looks_like_stage_page(image):
            log("Returned to 1-7 page.")
            return True

        if looks_like_result_page(image):
            log("Mission result page detected; tapping to return.")
            tap(config, serial, image, "result_center", 3.0)
            continue

        if time.monotonic() >= next_fallback_tap:
            log("Tapping once in case a result or reward screen is waiting.")
            tap(config, serial, image, "result_center", 0.8)
            next_fallback_tap = time.monotonic() + 20

    save_debug(config, screenshot(config, serial), "battle_timeout")
    return False


def close_delegate_picker_and_verify(config: Config, serial: str, image: Image.Image) -> Image.Image | None:
    log(f"Delegate picker selected x{config.delegate_runs}; closing picker outside the box.")
    tap(config, serial, image, "delegate_picker_close_area", 0.8)
    image = screenshot(config, serial)
    if delegate_count_looks_like_target(image, config.delegate_runs):
        log(f"Delegate multiplier looks like x{config.delegate_runs}.")
        return image
    return None


def ensure_delegate_runs(config: Config, serial: str, image: Image.Image) -> Image.Image:
    if delegate_count_looks_like_target(image, config.delegate_runs):
        log(f"Delegate multiplier looks like x{config.delegate_runs}.")
        return image

    log(f"Opening delegate multiplier picker to select x{config.delegate_runs}.")
    tap(config, serial, image, "delegate_counter", 0.8)
    image = screenshot(config, serial)
    if not looks_like_delegate_picker_open(image):
        save_debug(config, image, "delegate_picker_not_open")
        raise RuntimeError("Could not open delegate multiplier picker.")

    swipe_attempts = [
        ("delegate_picker_swipe_upper", "delegate_picker_swipe_lower"),
        ("delegate_picker_swipe_upper", "delegate_picker_swipe_lower"),
        ("delegate_picker_swipe_upper", "delegate_picker_swipe_lower"),
        ("delegate_picker_swipe_upper", "delegate_picker_swipe_lower"),
        ("delegate_picker_swipe_upper", "delegate_picker_swipe_lower"),
        ("delegate_picker_swipe_lower", "delegate_picker_swipe_upper"),
        ("delegate_picker_swipe_lower", "delegate_picker_swipe_upper"),
        ("delegate_picker_swipe_lower", "delegate_picker_swipe_upper"),
        ("delegate_picker_swipe_lower", "delegate_picker_swipe_upper"),
        ("delegate_picker_swipe_lower", "delegate_picker_swipe_upper"),
        ("delegate_picker_swipe_lower", "delegate_picker_swipe_upper"),
        ("delegate_picker_swipe_lower", "delegate_picker_swipe_upper"),
    ]

    for start_name, end_name in swipe_attempts:
        if delegate_picker_looks_like_target(image, config.delegate_runs):
            verified_image = close_delegate_picker_and_verify(config, serial, image)
            if verified_image is not None:
                return verified_image

        digits = delegate_picker_digit_component_count(image)
        log(
            f"Delegate picker is not confirmed as x{config.delegate_runs} "
            f"(selected_digit_components={digits}); swiping picker."
        )
        swipe(config, serial, image, start_name, end_name, duration_ms=450, delay=0.8)
        image = screenshot(config, serial)

    if delegate_picker_looks_like_target(image, config.delegate_runs):
        verified_image = close_delegate_picker_and_verify(config, serial, image)
        if verified_image is not None:
            return verified_image

    save_debug(config, image, "delegate_multiplier_not_confirmed")
    raise RuntimeError(f"Could not confirm delegate multiplier x{config.delegate_runs}.")


def run_one(config: Config, serial: str) -> bool:
    image = screenshot(config, serial)
    if not looks_like_stage_page(image):
        save_debug(config, image, "not_stage_page")
        raise RuntimeError("Current screen does not look like the 1-7 start page.")

    image = ensure_delegate_runs(config, serial, image)

    for attempt in range(3):
        log("Opening squad confirmation from 1-7.")
        tap(config, serial, image, "start_action", 2.0)
        state = wait_for_squad_or_recovery(config, serial)

        if state == "squad":
            image = screenshot(config, serial)
            log("Squad confirmation page detected; starting operation.")
            tap(config, serial, image, "squad_start", 3.0)
            break

        if state == "recovery":
            image = screenshot(config, serial)
            log(f"Sanity recovery dialog detected; current sanity is below {config.sanity_cost}.")
            if first_potion_expires_soon(image):
                log("Selected sanity medicine expires soon; confirming recovery.")
                tap(config, serial, image, "potion_confirm", 3.0)
                image = screenshot(config, serial)
                if looks_like_recovery_dialog(image):
                    save_debug(config, image, "recovery_still_open")
                    raise RuntimeError("Recovery dialog stayed open after confirmation.")
                time.sleep(1.5)
                image = screenshot(config, serial)
                if not looks_like_stage_page(image):
                    save_debug(config, image, "after_recovery_not_stage")
                    raise RuntimeError("After recovery, did not return to the 1-7 start page.")
                continue

            log("Selected sanity medicine is not <=2 days; closing dialog and stopping.")
            tap(config, serial, image, "potion_cancel", 1.5)
            return False

        raise RuntimeError("After tapping start, neither squad page nor recovery dialog was detected.")
    else:
        raise RuntimeError("Could not enter squad confirmation after using sanity medicine.")

    if not wait_for_stage_page_after_battle(config, serial):
        raise RuntimeError("Timed out waiting for the battle to finish.")
    return True


def loop_1_7(config: Config, serial: str) -> None:
    runs = 0
    while config.max_runs is None or runs < config.max_runs:
        should_continue = run_one(config, serial)
        if not should_continue:
            break
        runs += 1
        log(f"Completed run #{runs}.")
    log(f"Loop stopped after {runs} completed run(s).")


def parse_args() -> Config:
    parser = argparse.ArgumentParser(description="Run Arknights 1-7 on MuMu via ADB.")
    parser.add_argument("--mode", choices=["find", "loop", "find-and-loop"], default="find-and-loop")
    parser.add_argument("--vm-name", default="\u660e\u65e5\u65b9\u821f")
    parser.add_argument("--vmindex")
    parser.add_argument("--serial", help="ADB serial, e.g. 127.0.0.1:16416")
    parser.add_argument("--arknights-package", default="com.hypergryph.arknights")
    parser.add_argument("--no-auto-launch", action="store_true", help="Do not launch Arknights before finding 1-7.")
    parser.add_argument("--mumu-cli", default=r"D:\MuMu\MuMuPlayer\nx_main\mumu-cli.exe")
    parser.add_argument("--adb", default=r"D:\MuMu\MuMuPlayer\nx_device\12.0\shell\adb.exe")
    parser.add_argument("--hide-window", action="store_true", help="Hide MuMu window before running.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--debug-dir", default="ark_1_7_debug")
    parser.add_argument("--max-runs", type=int, help="Stop after N completed battles.")
    parser.add_argument("--delegate-runs", type=int, default=DEFAULT_DELEGATE_RUNS, help="Expected delegated run multiplier.")
    parser.add_argument("--sanity-cost", type=int, default=DEFAULT_SANITY_COST, help="Sanity cost for one delegated start.")
    parser.add_argument("--launch-timeout", type=int, default=180)
    parser.add_argument("--battle-timeout", type=int, default=180)
    parser.add_argument("--poll-seconds", type=int, default=8)
    parser.add_argument("--boot-timeout", type=int, default=240, help="Max seconds to wait for MuMu VM boot.")
    args = parser.parse_args()

    return Config(
        mode=args.mode,
        mumu_cli=Path(args.mumu_cli),
        adb=Path(args.adb),
        vm_name=args.vm_name,
        vmindex=args.vmindex,
        serial=args.serial,
        arknights_package=args.arknights_package,
        auto_launch=not args.no_auto_launch,
        hide_window=args.hide_window,
        dry_run=args.dry_run,
        debug_dir=Path(args.debug_dir),
        max_runs=args.max_runs,
        delegate_runs=args.delegate_runs,
        sanity_cost=args.sanity_cost,
        launch_timeout=args.launch_timeout,
        battle_timeout=args.battle_timeout,
        poll_seconds=args.poll_seconds,
        boot_timeout=args.boot_timeout,
    )


def main() -> int:
    config = parse_args()
    try:
        vmindex = discover_vmindex(config)
        ensure_emulator_running(config, vmindex)
        serial = serial_for_vm(config, vmindex)
        if config.hide_window:
            mumu_control(config, vmindex, "hide_window")
        if config.mode in {"find", "find-and-loop"}:
            ensure_game_home(config, serial)
            find_1_7(config, serial)
        if config.mode in {"loop", "find-and-loop"}:
            loop_1_7(config, serial)
        return 0
    except KeyboardInterrupt:
        log("Interrupted by user.")
        return 130
    except Exception as exc:
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
