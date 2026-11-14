#!/usr/bin/env python3
"""Render a flow-diagram explainer from a storyboard JSON.

Generic: the picture comes only from the storyboard. Writes under --out,
which must sit outside this skill repository.

  python render_film.py storyboard.json --out D:\\personspace\\skill-runs\\some-skill --ffmpeg ffmpeg
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

def protected_roots() -> list[Path]:
    skill = Path(__file__).resolve().parents[1]
    roots = [skill]
    if skill.parent.name == "skills":
        repo = skill.parent.parent
        if (repo / ".git").is_dir() or (repo / ".git").is_file():
            roots.append(repo)
    return roots


W, H = 1920, 1080
FPS = 30

BG = (7, 10, 18, 255)
INK = (242, 245, 251, 255)
MUTED = (154, 168, 196, 255)
BLUE = (110, 168, 255, 255)
NODE = (14, 28, 58, 255)
NODE_ON = (36, 92, 220, 255)
PAPER = (244, 231, 200, 255)
PAPER_INK = (32, 36, 44, 255)
AMBER = (240, 180, 70, 255)
SAFE = (125, 206, 160, 255)
FONT = r"C:\Windows\Fonts\msyh.ttc"
FONT_B = r"C:\Windows\Fonts\msyhbd.ttc"

STORY: dict = {}
OUT = Path()


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_B if bold else FONT, size)


F64 = font(64, True)
F40 = font(40)
F36 = font(36, True)
F32 = font(32)
F32B = font(32, True)


def smooth(a: float, b: float, t: float) -> float:
    if b <= a:
        return 1.0 if t >= a else 0.0
    if t <= a:
        return 0.0
    if t >= b:
        return 1.0
    x = (t - a) / (b - a)
    return x * x * (3 - 2 * x)


def lerp(a: float, b: float, u: float) -> float:
    return a + (b - a) * u


def duration() -> float:
    return float(STORY["duration"])


def nodes() -> list[dict]:
    return STORY["nodes"]


def sections() -> list[dict]:
    return STORY["sections"]


def panels() -> list[dict]:
    return STORY.get("panels", [])


def xs() -> list[float]:
    n = len(nodes())
    left, right = 400, 1680
    if n <= 1:
        return [1040.0]
    return [left + i * (right - left) / (n - 1) for i in range(n)]


def text_size(draw, text, fnt):
    box = draw.textbbox((0, 0), text, font=fnt)
    return box[2] - box[0], box[3] - box[1]


def paste_text(img, xy, text, fnt, fill, alpha: float = 1.0) -> None:
    if alpha <= 0.02 or not text:
        return
    probe = ImageDraw.Draw(img)
    box = probe.textbbox((0, 0), text, font=fnt)
    tw, th = box[2] - box[0], box[3] - box[1]
    plate = Image.new("RGBA", (max(1, tw + 4), max(1, th + 4)), (0, 0, 0, 0))
    ImageDraw.Draw(plate).text((-box[0], -box[1]), text, font=fnt, fill=fill[:3] + (int(255 * alpha),))
    img.alpha_composite(plate, (int(xy[0]), int(xy[1])))


def center_text(img, cx, y, text, fnt, fill, alpha: float = 1.0) -> None:
    probe = ImageDraw.Draw(img)
    tw, _ = text_size(probe, text, fnt)
    paste_text(img, (cx - tw / 2, y), text, fnt, fill, alpha)


def wrap_chars(draw, text: str, fnt, max_w: int) -> list[str]:
    lines, buf = [], ""
    for ch in text:
        if buf and text_size(draw, buf + ch, fnt)[0] > max_w:
            lines.append(buf)
            buf = ch
        else:
            buf += ch
    if buf:
        lines.append(buf)
    return lines or [""]


def base_y(t: float) -> float:
    lift = 0.0
    for beat in STORY.get("beats", []):
        panel = beat.get("panel") or {}
        if panel.get("kind") != "evidence":
            continue
        start = beat.get("start", 0)
        end = beat.get("end", start + 1)
        lift = max(lift, smooth(start, start + 0.4, t) * (1 - smooth(end - 0.3, end, t)))
    return lerp(500, 310, lift)


def node_xy(i: int, t: float, base: float):
    spec = nodes()[i]
    u = smooth(spec["arrive"], spec["arrive"] + 1.5, t)
    wave = [0, -36, 24, -16, 28, -20][i % 6]
    rest = (xs()[i], base + wave)
    origin = (250, base)
    return (lerp(origin[0], rest[0], u), lerp(origin[1], rest[1], u)), u


def curve(a, b, n=24):
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2 - 26
    pts = []
    for i in range(n + 1):
        u = i / n
        o = 1 - u
        pts.append((o * o * a[0] + 2 * o * u * mx + u * u * b[0], o * o * a[1] + 2 * o * u * my + u * u * b[1]))
    return pts


def along(coords, u: float):
    u = max(0.0, min(0.999, u))
    seg = u * (len(coords) - 1)
    i = min(len(coords) - 2, int(seg))
    pts = curve(coords[i], coords[i + 1], 18)
    k = min(len(pts) - 1, int((seg - i) * (len(pts) - 1)))
    return pts[k]


def bead_u(t: float):
    walk = STORY["walk"]
    n = len(nodes())
    if n < 2 or t < walk["start"]:
        return None
    if t < walk["end"]:
        return (t - walk["start"]) / (walk["end"] - walk["start"])
    prev_u = 1.0
    for panel in panels():
        target = panel.get("focus", 0) / (n - 1)
        if t < panel["start"]:
            return prev_u
        glide = smooth(panel["start"], panel["start"] + 1.6, t)
        if t < panel["end"]:
            return lerp(prev_u, target, glide)
        prev_u = target
    return prev_u


def active_index(t: float):
    u = bead_u(t)
    if u is None:
        return None
    n = len(nodes())
    return min(n - 1, int(u * (n - 1) + 0.12))


def current_section(t: float) -> dict:
    for section in sections():
        if section["start"] <= t < section["end"]:
            return section
    return sections()[-1]


def current_panel(t: float):
    for panel in panels():
        if panel["start"] <= t < panel["end"]:
            return panel
    return None


def frame(t: float) -> Image.Image:
    t = min(max(t, 0.0), duration() - 1e-4)
    img = Image.new("RGBA", (W, H), BG)
    draw = ImageDraw.Draw(img)
    beat = beat_at(t)
    show_diagram = any(isinstance(b.get("node"), int) and t >= b.get("start", 1e9) for b in STORY.get("beats", []))
    base = base_y(t)
    coords, arrived = [], []
    for i in range(len(nodes())):
        p, u = node_xy(i, t, base)
        coords.append(p)
        arrived.append(u)
    origin = (250, base)
    folder_a = smooth(0.3, 1.2, t) if show_diagram else 0
    if folder_a > 0.02:
        draw.rounded_rectangle((96, base - 70, 250, base + 78), radius=18, fill=PAPER[:3] + (int(255 * folder_a),))
        paste_text(img, (112, base - 48), "Skill", F32B, PAPER_INK, folder_a)
        paste_text(img, (112, base + 4), "文件夹", F32, PAPER_INK, folder_a)
    paste_text(img, (96, 36), beat.get("title", ""), F64, INK, 1)

    for i, (p, u) in enumerate(zip(coords, arrived)):
        if u <= 0.02:
            continue
        start = origin if i == 0 else coords[i - 1]
        seg = curve(start, p)
        cut = max(2, int(u * (len(seg) - 1)))
        draw.line(seg[: cut + 1], fill=(150, 190, 255, 230), width=4)

    focus = beat.get("node")
    walk_on = focus is not None
    for i, (p, u) in enumerate(zip(coords, arrived)):
        if u <= 0.02:
            continue
        on = focus == i and walk_on
        r = 32 if on else 24
        draw.ellipse((p[0] - r, p[1] - r, p[0] + r, p[1] + r), fill=NODE_ON if on else NODE)
        draw.ellipse((p[0] - r, p[1] - r, p[0] + r, p[1] + r), outline=BLUE[:3] + (230,), width=3)
        label = nodes()[i]["label"]
        tw, _ = text_size(draw, label, F32B)
        paste_text(img, (p[0] - tw / 2, p[1] - 74), label, F32B, INK, u)
        tag = nodes()[i].get("tag") or ""
        if tag and u > 0.85:
            tw, _ = text_size(draw, tag, F32)
            paste_text(img, (p[0] - tw / 2, p[1] + 42), tag, F32, PAPER, u)

    if isinstance(focus, int) and 0 <= focus < len(coords) and arrived[focus] > 0.5:
        q = coords[focus]
        draw.ellipse((q[0] - 16, q[1] - 16, q[0] + 16, q[1] + 16), fill=BLUE[:3] + (90,))
        draw.ellipse((q[0] - 7, q[1] - 7, q[0] + 7, q[1] + 7), fill=(255, 255, 255, 240))

    if beat.get("panel"):
        draw_panel(img, draw, t, coords, beat["panel"])
    lines = wrap_chars(draw, beat.get("narration", ""), F40, W - 220)
    line_h = 48
    band_top = 1060 - line_h * len(lines)
    draw.rounded_rectangle((80, band_top - 18, W - 80, 1070), radius=14, fill=(10, 14, 24, 230))
    for i, line in enumerate(lines):
        center_text(img, W / 2, band_top + i * line_h, line, F40, INK, 1)
    return img.convert("RGB")


def beat_at(t: float) -> dict:
    beats = STORY.get("beats") or []
    for beat in beats:
        if beat.get("start", 0) <= t < beat.get("end", 1e9):
            return beat
    return beats[-1] if beats else {"title": "", "narration": ""}


def draw_panel(img, draw, t, coords, panel) -> None:
    if not panel:
        return
    kind = panel["kind"]
    a = 1.0
    if kind == "chips":
        center_text(img, 1040, 640, panel["heading"], F36, INK, a)
        chips = panel["chips"]
        gap = 40
        width = 420
        total = len(chips) * width + (len(chips) - 1) * gap
        x0 = (W - total) / 2
        for i, (head, body) in enumerate(chips):
            u = smooth(panel["start"] + 0.3 + i * 0.4, panel["start"] + 1.2 + i * 0.4, t)
            x = x0 + i * (width + gap)
            draw.rounded_rectangle((x, 720, x + width, 840), radius=16, fill=(48, 36, 16, int(230 * u)), outline=AMBER[:3] + (int(255 * u),), width=2)
            paste_text(img, (x + 28, 744), head, F32B, AMBER, u)
            paste_text(img, (x + 28, 788), body, F32, INK, u)
    elif kind == "evidence":
        if coords:
            idx = min(panel.get("focus", 0), len(coords) - 1)
            x, y = coords[idx]
            draw.line([(x, y + 34), (x, 520), (420, 520)], fill=AMBER[:3] + (int(200 * a),), width=2)
        paste_text(img, (420, 528), panel["heading"], F36, AMBER, a)
        for i, (head, body) in enumerate(panel["rows"]):
            u = smooth(panel["start"] + 0.3 + i * 0.25, panel["start"] + 1.0 + i * 0.25, t) * a
            y = 584 + i * 68
            draw.line((420, y + 6, 420, y + 52), fill=AMBER[:3] + (int(255 * u),), width=3)
            paste_text(img, (444, y), head, F32B, AMBER, u)
            paste_text(img, (444, y + 32), body, F32, MUTED, u)
        paste_text(img, (420, 584 + len(panel["rows"]) * 68 + 8), panel.get("footer", ""), F32B, SAFE, smooth(panel["start"] + 2, panel["start"] + 3, t))
    elif kind == "lines":
        for i, line in enumerate(panel["lines"]):
            u = smooth(panel["start"] + i * 0.45, panel["start"] + 1.0 + i * 0.45, t)
            y = 640 + i * 84
            draw.ellipse((430, y + 8, 460, y + 38), fill=BLUE[:3] + (int(255 * u),))
            paste_text(img, (484, y), line, F36, INK, u)


def refuse_inside_repo(path: Path) -> None:
    resolved = path.resolve()
    for root in protected_roots():
        if resolved == root or root in resolved.parents:
            raise SystemExit(f"输出不能写进 skill 目录或其所在源仓库：{resolved}")


def write_stills(times: list[float]) -> None:
    dest = OUT / "stills"
    dest.mkdir(parents=True, exist_ok=True)
    for stamp in times:
        path = dest / f"t{int(stamp):03d}.png"
        frame(stamp).save(path)
        print(path)


def synth_narration() -> None:
    audio = OUT / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    voice = STORY.get("voice", "zh-CN-XiaoxiaoNeural")
    for i, beat in enumerate(STORY["beats"]):
        mp3 = audio / f"line_{i}.mp3"
        marker = audio / f"line_{i}.txt"
        if mp3.exists() and marker.exists() and marker.read_text(encoding="utf-8") == beat["narration"]:
            print("reuse", mp3)
            continue
        subprocess.run(
            [sys.executable, "-m", "edge_tts", "--voice", voice, "--rate=-8%",
             "--text", beat["narration"], "--write-media", str(mp3)],
            check=True,
        )
        marker.write_text(beat["narration"], encoding="utf-8")
        print(mp3)


def media_duration(path: Path, ffmpeg: str) -> float:
    probe = str(Path(ffmpeg).with_name("ffprobe.exe" if ffmpeg.lower().endswith(".exe") else "ffprobe"))
    out = subprocess.check_output(
        [probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        text=True,
    )
    return float(out.strip())


def layout_beats(ffmpeg: str) -> None:
    clock = 0.3
    for i, beat in enumerate(STORY["beats"]):
        dur = media_duration(OUT / "audio" / f"line_{i}.mp3", ffmpeg)
        beat["start"] = clock
        beat["end"] = clock + dur + 0.35
        clock = beat["end"]
        if beat.get("panel"):
            beat["panel"]["start"] = beat["start"]
            beat["panel"]["end"] = beat["end"]
        if isinstance(beat.get("node"), int):
            nodes()[beat["node"]]["arrive"] = beat["start"]
        print(f"beat {i}: {beat['start']:.2f}-{beat['end']:.2f}s voice {dur:.2f}s")
    STORY["duration"] = clock + 0.4


def encode(ffmpeg: str) -> None:
    silent = OUT / "silent.mp4"
    cmd = [
        ffmpeg, "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
        "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-crf", "18", "-preset", "medium", "-movflags", "+faststart", str(silent),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    nframes = round(duration() * FPS)
    for i in range(nframes):
        proc.stdin.write(frame(i / FPS).tobytes())
        if i % 150 == 0:
            print(f"frame {i}/{nframes}", flush=True)
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit("ffmpeg failed")
    audio = OUT / "audio"
    filters, labels = [], []
    beats = STORY["beats"]
    for i, beat in enumerate(beats):
        delay = int(beat["start"] * 1000)
        filters.append(f"[{i+1}:a]adelay={delay}|{delay},volume=1.2[a{i}]")
        labels.append(f"[a{i}]")
    mix = (
        "".join(labels)
        + f"amix=inputs={len(beats)}:normalize=0:duration=longest,atrim=0:{duration()},"
        + "aformat=sample_rates=48000:channel_layouts=stereo,loudnorm=I=-16:TP=-1.5[a]"
    )
    final = OUT / "final.mp4"
    subprocess.run([
        ffmpeg, "-y", "-i", str(silent),
        *sum((["-i", str(audio / f"line_{i}.mp3")] for i in range(len(beats))), []),
        "-filter_complex", ";".join(filters + [mix]),
        "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(final),
    ], check=True)
    print(final)


def main() -> None:
    global STORY, OUT
    parser = argparse.ArgumentParser(description="Render a skill explainer from a storyboard")
    parser.add_argument("storyboard", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--stills", action="store_true")
    args = parser.parse_args()
    refuse_inside_repo(args.out)
    STORY = json.loads(args.storyboard.read_text(encoding="utf-8"))
    OUT = args.out
    OUT.mkdir(parents=True, exist_ok=True)
    synth_narration()
    layout_beats(args.ffmpeg)
    args.storyboard.write_text(json.dumps(STORY, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.stills:
        mids = [(b["start"] + b["end"]) / 2 for b in STORY["beats"]]
        write_stills(mids)
        return
    encode(args.ffmpeg)


if __name__ == "__main__":
    main()
