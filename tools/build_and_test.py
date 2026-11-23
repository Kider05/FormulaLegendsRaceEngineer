"""
build_and_test.py

Builds glyph templates from dataset/train/, then re-tests every training
image against the templates it produced. Catches mislabeled files, bad
crops and broken templates; does not catch overfitting.

Usage: python tools/build_and_test.py
"""

from pathlib import Path

import cv2

from telemetry.glyph_reader import (
    build_template_holes,
    build_templates,
    load_templates,
    match_glyph,
    normalize_glyph,
    parse_label_from_filename,
    segment_glyphs,
    threshold_image,
)

SCRIPT_DIR = Path(__file__).resolve().parent
DATASET_DIR = SCRIPT_DIR.parent / "dataset" / "train"
TEMPLATES_DIR = SCRIPT_DIR.parent / "templates"


def main():
    print(f"Building templates from {DATASET_DIR} ...")
    labels_built = build_templates(DATASET_DIR, TEMPLATES_DIR)
    print(f"\nBuilt {len(labels_built)} template(s): {sorted(labels_built)}\n")

    print("Verifying every training image against its own templates...")
    templates = load_templates(TEMPLATES_DIR)
    template_holes = build_template_holes(templates)

    total, correct = 0, 0
    failures = []

    all_files = list(DATASET_DIR.rglob("*.png")) + list(DATASET_DIR.rglob("*.PNG"))
    seen = set()
    files = []
    for f in sorted(all_files):
        key = str(f.resolve()).lower()
        if key not in seen:
            seen.add(key)
            files.append(f)

    for path in files:
        expected = parse_label_from_filename(path.name)
        if expected is None:
            continue

        img = cv2.imread(str(path))
        binary = threshold_image(img)
        boxes = segment_glyphs(binary, merge_colons=True)
        if len(boxes) != 1:
            failures.append((path.name, expected, f"<{len(boxes)} blobs>"))
            total += 1
            continue

        glyph = normalize_glyph(binary, boxes[0])
        predicted, score = match_glyph(glyph, templates, template_holes=template_holes)

        total += 1
        if predicted == expected:
            correct += 1
        else:
            failures.append((path.name, expected, f"{predicted} (score={score:.3f})"))

    print(f"\n{correct}/{total} correct")
    if failures:
        print("\nFailures (file, expected, got):")
        for f in failures:
            print(f"  {f}")
    else:
        print("All training images correctly classified.")


if __name__ == "__main__":
    main()
