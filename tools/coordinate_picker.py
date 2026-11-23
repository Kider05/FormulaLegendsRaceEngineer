"""
coordinate_picker.py -- click two corners on a screenshot to get a crop region for hud_layout.py.
Prints the region as (top, bottom, left, right), the same order as the crops in hud_layout.py.
R clears the selection, Esc quits. Run from the project root:
    python tools/coordinate_picker.py screenshots/test/leaderboardtest2.png
"""

import argparse
import sys

import cv2

WINDOW = "Coordinate Picker"
ESC_KEY = 27


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image", help="path to a full-resolution game screenshot")
    image = cv2.imread(parser.parse_args().image)
    if image is None:
        sys.exit("Could not load that image.")

    points = []
    view = {"frame": image.copy()}

    def on_mouse(event, x, y, _flags, _param):
        frame = image.copy()
        cv2.putText(frame, f"({x}, {y})", (10, 35), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        for point in points:
            cv2.circle(frame, point, 5, (0, 0, 255), -1)
        if len(points) == 2:
            cv2.rectangle(frame, points[0], points[1], (0, 255, 0), 2)
        view["frame"] = frame
        if event == cv2.EVENT_LBUTTONDOWN and len(points) < 2:
            points.append((x, y))
            print(f"Point {len(points)}: ({x}, {y})")
            if len(points) == 2:
                (x1, y1), (x2, y2) = points
                top, bottom, left, right = min(y1, y2), max(y1, y2), min(x1, x2), max(x1, x2)
                print(f"Crop: ({top}, {bottom}, {left}, {right})   size {right - left}x{bottom - top}\n")

    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(WINDOW, on_mouse)
    while True:
        cv2.imshow(WINDOW, view["frame"])
        key = cv2.waitKey(15)
        if key == ord("r"):
            points.clear()
            print("Selection cleared.")
        elif key == ESC_KEY:
            break
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
