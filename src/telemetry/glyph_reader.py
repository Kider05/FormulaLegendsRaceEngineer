"""
glyph_reader.py

Template matching for Formula Legends HUD text (lap counter, lap times,
fuel/tyre %). Font is fixed and small charset (0-9, |, :, .), so we match
against saved reference glyphs instead of using OCR (OCR misreads this font).

Pipeline: threshold -> segment -> build/load templates -> match -> read_string.
"""

import re
from pathlib import Path

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GLYPH_SIZE = (24, 32)  # (w, h) all glyphs normalized to this before compare
MIN_BLOB_AREA = 4  # blobs smaller than this = noise, not a glyph

# "|" ":" aren't valid Windows filenames -- swap for safe names on disk only.
_LABEL_TO_FILENAME = {"|": "pipe", ":": "colon"}
_FILENAME_TO_LABEL = {v: k for k, v in _LABEL_TO_FILENAME.items()}


def _label_to_filename(label):
    return _LABEL_TO_FILENAME.get(label, label)


def _filename_to_label(stem):
    return _FILENAME_TO_LABEL.get(stem, stem)


# ":" is two separate dot blobs -- merge them into one glyph if close and
# stacked vertically (not side by side).
COLON_MAX_X_OVERLAP_RATIO = 0.6
COLON_MAX_Y_GAP = 30
COLON_DOT_MAX_DIMENSION = 22  # only blobs this small count as dot candidates

# Filters thin decorative bars (wide+flat) that survive thresholding as
# their own blob. Doesn't catch "|" -- that's tall+thin, opposite shape.
NOISE_MIN_WIDTH = 40
NOISE_ASPECT_RATIO = 5.0

# Min area (normalized glyph space) for a hole to count in count_holes().
# Filters anti-aliasing slivers so a closed loop isn't miscounted.
MIN_HOLE_AREA = 3

# resolve_two_three(): lower-middle band (fraction of glyph height) and the
# edge strip width (fraction of glyph width) compared on each side.
TWO_THREE_BAND = (0.56, 0.72)
TWO_THREE_EDGE = 0.25


# ---------------------------------------------------------------------------
# Step 1: Threshold
# ---------------------------------------------------------------------------


def threshold_image(img, invert=None):
    """BGR/gray crop -> binary (0/255), glyph = white on black."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img

    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    if invert is None:
        # glyph ink is assumed to be the minority pixel class
        white_ratio = np.count_nonzero(binary) / binary.size
        if white_ratio > 0.5:
            binary = cv2.bitwise_not(binary)
    elif invert:
        binary = cv2.bitwise_not(binary)

    return binary


# ---------------------------------------------------------------------------
# Step 2: Segment into glyph blobs
# ---------------------------------------------------------------------------


def _merge_colon_periods(boxes):
    """Merge dot-sized, vertically-stacked blob pairs (":" dots) into one box."""
    boxes = sorted(boxes, key=lambda b: b[0])
    merged = []
    used = [False] * len(boxes)

    def is_dot_candidate(box):
        _, _, w, h = box
        return w <= COLON_DOT_MAX_DIMENSION and h <= COLON_DOT_MAX_DIMENSION

    for i in range(len(boxes)):
        if used[i]:
            continue
        x1, y1, w1, h1 = boxes[i]
        if not is_dot_candidate(boxes[i]):
            merged.append(boxes[i])
            used[i] = True
            continue

        best_j = None
        for j in range(i + 1, len(boxes)):
            if used[j]:
                continue
            x2, y2, w2, h2 = boxes[j]
            if not is_dot_candidate(boxes[j]):
                continue

            overlap = max(0, min(x1 + w1, x2 + w2) - max(x1, x2))
            narrower = min(w1, w2)
            overlap_ratio = overlap / narrower if narrower > 0 else 0
            y_gap = max(0, max(y1, y2) - min(y1 + h1, y2 + h2))

            if overlap_ratio >= COLON_MAX_X_OVERLAP_RATIO and y_gap <= COLON_MAX_Y_GAP:
                best_j = j
                break

        if best_j is not None:
            x2, y2, w2, h2 = boxes[best_j]
            nx, ny = min(x1, x2), min(y1, y2)
            nx2, ny2 = max(x1 + w1, x2 + w2), max(y1 + h1, y2 + h2)
            merged.append((nx, ny, nx2 - nx, ny2 - ny))
            used[i] = used[best_j] = True
        else:
            merged.append((x1, y1, w1, h1))
            used[i] = True

    return merged


def segment_glyphs(binary_img, merge_colons=True):
    """Find glyph blobs left to right. Returns [(x, y, w, h), ...], no classifying."""
    contours, _ = cv2.findContours(binary_img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w * h < MIN_BLOB_AREA:
            continue
        if w >= NOISE_MIN_WIDTH and (w / h) >= NOISE_ASPECT_RATIO:
            continue  # decorative bar, not a glyph
        boxes.append((x, y, w, h))

    if merge_colons:
        boxes = _merge_colon_periods(boxes)

    boxes.sort(key=lambda b: b[0])
    return boxes


def normalize_glyph(binary_img, box):
    """Crop + resize to GLYPH_SIZE, padded (not stretched) to keep aspect ratio."""
    x, y, w, h = box
    crop = binary_img[y : y + h, x : x + w]

    target_w, target_h = GLYPH_SIZE
    scale = min(target_w / w, target_h / h)
    new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
    resized = cv2.resize(crop, (new_w, new_h), interpolation=cv2.INTER_AREA)

    canvas = np.zeros((target_h, target_w), dtype=np.uint8)
    x_off = (target_w - new_w) // 2
    y_off = (target_h - new_h) // 2
    canvas[y_off : y_off + new_h, x_off : x_off + new_w] = resized
    return canvas


# ---------------------------------------------------------------------------
# Step 3/4: Build & load templates
# ---------------------------------------------------------------------------

# capture{digit}{index}.PNG, captureI{index} = "|", captureC{index} = ":",
# captureP{index} = ".". Last char in the name = copy index, rest = label.
# Greedy on purpose ("101" -> label "10", index "1"). For 2-digit labels
# just crop the shot to isolate one glyph instead -- read_string() reads
# "10" as separate "1"+"0" blobs anyway. Breaks if one digit hits 10+ copies.
_FILENAME_DIGIT_RE = re.compile(r"capture(\d+)(\d)\.png$", re.IGNORECASE)
_FILENAME_SEPARATOR_RE = re.compile(r"captureI(\d+)\.png$", re.IGNORECASE)
_FILENAME_COLON_RE = re.compile(r"captureC(\d+)\.png$", re.IGNORECASE)
_FILENAME_PERIOD_RE = re.compile(r"captureP(\d+)\.png$", re.IGNORECASE)
_FILENAME_MINUS_RE = re.compile(r"captureM(\d+)\.png$", re.IGNORECASE)
_FILENAME_PLUS_RE = re.compile(r"captureA(\d+)\.png$", re.IGNORECASE)


def parse_label_from_filename(filename):
    name = Path(filename).name

    if _FILENAME_SEPARATOR_RE.match(name):
        return "|"
    if _FILENAME_COLON_RE.match(name):
        return ":"
    if _FILENAME_PERIOD_RE.match(name):
        return "."
    if _FILENAME_MINUS_RE.match(name):
        return "-"
    if _FILENAME_PLUS_RE.match(name):
        return "+"
    m = _FILENAME_DIGIT_RE.match(name)
    if m:
        return m.group(1)
    return None


def build_templates(dataset_dir, templates_dir, label_fn=parse_label_from_filename):
    """
    One-time setup: read labeled screenshots, average each label's glyphs
    into one clean template, save to templates_dir as '<label>.png'.
    Averaging cancels out per-shot background/segmentation noise.
    """
    dataset_dir = Path(dataset_dir)
    templates_dir = Path(templates_dir)
    templates_dir.mkdir(parents=True, exist_ok=True)

    accum = {}
    skipped = []

    all_files = list(dataset_dir.rglob("*.png")) + list(dataset_dir.rglob("*.PNG"))
    seen = set()
    unique_files = []
    for f in sorted(all_files):
        key = str(f.resolve()).lower()
        if key not in seen:
            seen.add(key)
            unique_files.append(f)

    for path in unique_files:
        label = label_fn(path.name)
        if label is None:
            print(f"  [!] {path.name}: no filename match -- skipped")
            skipped.append(path.name)
            continue

        img = cv2.imread(str(path))
        if img is None:
            print(f"  [!] {path.name}: failed to load -- skipped")
            skipped.append(path.name)
            continue

        binary = threshold_image(img)
        boxes = segment_glyphs(binary, merge_colons=True)

        if len(boxes) != 1:
            print(f"  [!] {path.name}: expected 1 glyph, found {len(boxes)} -- skipped")
            skipped.append(path.name)
            continue

        glyph = normalize_glyph(binary, boxes[0])
        accum.setdefault(label, []).append(glyph)

    for label, glyphs in accum.items():
        avg = np.mean(np.stack(glyphs), axis=0)
        _, avg_bin = cv2.threshold(avg.astype(np.uint8), 127, 255, cv2.THRESH_BINARY)
        out_path = templates_dir / f"{_label_to_filename(label)}.png"
        cv2.imwrite(str(out_path), avg_bin)
        print(f"  built '{label}' -> {out_path.name} from {len(glyphs)} sample(s)")

    if skipped:
        print(f"\nSkipped {len(skipped)}: {skipped}")

    return list(accum.keys())


def load_templates(templates_dir):
    """Load templates_dir/*.png into {label: glyph_array}."""
    templates_dir = Path(templates_dir)
    templates = {}
    for path in sorted(templates_dir.glob("*.png")):
        label = _filename_to_label(path.stem)
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if img is not None:
            templates[label] = img
    return templates


def build_template_holes(templates):
    """{label: hole_count} for every template. Compute once, reuse across calls."""
    return {label: count_holes(tmpl) for label, tmpl in templates.items()}


# ---------------------------------------------------------------------------
# Step 5: Match a single glyph
# ---------------------------------------------------------------------------


def count_holes(glyph, min_hole_area=MIN_HOLE_AREA):
    """
    Count enclosed background regions inside the ink (topological holes).
    8 = 2 holes, 9/0/6 = 1, 1/2/3/5/7/:/. = 0.

    IoU alone can't split 8 vs 9 in this font -- same top loop, so overlap
    stays high either way (~0.70 cross-match vs ~0.85+ correct match on real
    captures, thin enough for live noise to flip). Hole count gives a clean
    2-vs-1 split instead, no per-digit special-casing needed.
    """
    contours, hierarchy = cv2.findContours(glyph, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:
        return 0
    holes = 0
    for i, h in enumerate(hierarchy[0]):
        if h[3] != -1 and cv2.contourArea(contours[i]) >= min_hole_area:  # has parent = hole
            holes += 1
    return holes


def resolve_two_three(glyph):
    """
    '2' or '3' from stroke position. Both share top, middle and bottom bars;
    the lower vertical stroke sits on the left edge for 2, right edge for 3.
    Compares ink in those two edge strips across the lower-middle band.
    """
    h, w = glyph.shape
    band = glyph[int(h * TWO_THREE_BAND[0]) : int(h * TWO_THREE_BAND[1])]
    edge = max(1, int(w * TWO_THREE_EDGE))
    left = np.count_nonzero(band[:, :edge])
    right = np.count_nonzero(band[:, -edge:])
    return "2" if left > right else "3"


def match_glyph(glyph, templates, template_holes=None):
    """
    Best (label, score) by IoU on foreground (ink) pixels only -- whole-image
    IoU is misleading since background dominates the pixel count (a blank
    noise glyph scored 0.895 against an unrelated template that way).

    template_holes (from build_template_holes()): if given, only compare
    against templates whose hole count matches the glyph's -- fixes 8-vs-9
    confusion, which plain IoU leaves too close to call. Falls back to all
    templates if none match (handles a noisy capture that breaks a loop).

    A '2' or '3' result is re-decided by resolve_two_three(): a 1px change in
    a leaderboard glyph's box width can tie their IoU exactly.
    """
    glyph_fg = glyph > 0

    candidates = templates
    if template_holes is not None:
        glyph_holes = count_holes(glyph)
        same_hole_count = {label: t for label, t in templates.items() if template_holes.get(label) == glyph_holes}
        if same_hole_count:
            candidates = same_hole_count

    best_label, best_score = None, -1.0
    for label, tmpl in candidates.items():
        tmpl_fg = tmpl > 0
        intersection = np.count_nonzero(glyph_fg & tmpl_fg)
        union = np.count_nonzero(glyph_fg | tmpl_fg)
        iou = intersection / union if union > 0 else 0.0
        if iou > best_score:
            best_label, best_score = label, iou

    if best_label in ("2", "3"):
        best_label = resolve_two_three(glyph)

    return best_label, best_score


# ---------------------------------------------------------------------------
# Recovery: split a touching digit+period blob
# ---------------------------------------------------------------------------

# A trailing "." can touch the digit before it (esp. digits with a tail,
# like "7"), reading as one blob. Erosion breaks the thin bridge without
# wrecking the digit shape. Only tried on low-confidence blobs, never
# blindly (don't want to risk closing an "8" loop).
_SPLIT_EROSION_ITERATIONS = (2, 3, 4)
_SPLIT_DOT_MAX_DIMENSION = 20
_SPLIT_PAD = 3  # pad back out what erosion ate off both pieces' edges


def _attempt_dot_split(binary_crop):
    """
    Try splitting one blob into (main, dot) pieces, left-to-right by pixel
    position (not assumed order -- a period can end up left of a curled
    digit tail). Returns two (x, y, w, h) boxes or None if no clean split.
    """
    h_img, w_img = binary_crop.shape[:2]
    kernel = np.ones((3, 3), np.uint8)

    for iters in _SPLIT_EROSION_ITERATIONS:
        eroded = cv2.erode(binary_crop, kernel, iterations=iters)
        contours, _ = cv2.findContours(eroded, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        pieces = [cv2.boundingRect(c) for c in contours if cv2.contourArea(c) > 0]

        if len(pieces) != 2:
            continue

        by_area = sorted(pieces, key=lambda b: b[2] * b[3])
        dot, main = by_area[0], by_area[1]
        dx, dy, dw, dh = dot

        # dot must be small and sit low (period sits on the baseline)
        is_dot_sized = dw <= _SPLIT_DOT_MAX_DIMENSION and dh <= _SPLIT_DOT_MAX_DIMENSION
        is_low = (dy + dh / 2) > (h_img / 2)
        if not (is_dot_sized and is_low):
            continue

        def pad_box(box):
            x, y, w, h = box
            x0, y0 = max(0, x - _SPLIT_PAD), max(0, y - _SPLIT_PAD)
            x1, y1 = min(w_img, x + w + _SPLIT_PAD), min(h_img, y + h + _SPLIT_PAD)
            return (x0, y0, x1 - x0, y1 - y0)

        padded = sorted([pad_box(main), pad_box(dot)], key=lambda b: b[0])
        return padded[0], padded[1]

    return None


# ---------------------------------------------------------------------------
# Step 6: Full pipeline
# ---------------------------------------------------------------------------


def read_string(img, templates, min_confidence=0.6, debug=False, template_holes=None):
    """
    Raw HUD crop (BGR) -> recognized string, e.g. "17|24" or "00:11.667".

    min_confidence: below this, glyph -> '?' and its index goes in
    low_confidence, so bad reads don't get trusted silently.

    template_holes: from build_template_holes(), build once and reuse.
    Fixes 8-vs-9 confusion -- min_confidence alone won't, both score well
    above 0.6 regardless of which is right.

    0.6 threshold: real same-digit match ~1.0, real mismatch ~0.3, noise
    ~0.0 in testing -- but only on one background/lighting condition.
    Re-tune against real live captures (incl. pause/menu transitions)
    before trusting it fully.

    Low-confidence glyphs get one retry: erosion-split for a fused
    digit+period (see _attempt_dot_split) before giving up as '?'.

    Returns (result_string, low_confidence_indices).
    """
    binary = threshold_image(img)
    boxes = segment_glyphs(binary, merge_colons=True)

    chars = []
    low_confidence = []
    for i, box in enumerate(boxes):
        glyph = normalize_glyph(binary, box)
        label, score = match_glyph(glyph, templates, template_holes=template_holes)

        if debug:
            print(f"  glyph {i}: box={box} -> '{label}' (score={score:.3f})")

        if score < min_confidence:
            x, y, w, h = box
            crop = binary[y : y + h, x : x + w]
            split = _attempt_dot_split(crop)

            if split is not None:
                (x1, y1, w1, h1), (x2, y2, w2, h2) = split
                glyph1 = normalize_glyph(crop, (x1, y1, w1, h1))
                glyph2 = normalize_glyph(crop, (x2, y2, w2, h2))
                label1, score1 = match_glyph(glyph1, templates, template_holes=template_holes)
                label2, score2 = match_glyph(glyph2, templates, template_holes=template_holes)

                if score1 >= min_confidence and score2 >= min_confidence:
                    chars.append(label1)
                    chars.append(label2)
                    continue

            low_confidence.append(len(chars))
            chars.append("?")
        else:
            chars.append(label)

    return "".join(chars), low_confidence
