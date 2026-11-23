"""
ui_state_detector.py

Detects named, recognizable UI states (pause overlay, pit-stop tyre
selection screen, main menu, etc.) by matching a whole screen region
against a reference image of what that state looks like.

This is a DIFFERENT technique from glyph_reader.py's digit reading:
  - glyph_reader segments a crop into individual characters and matches
    each one independently, because the text inside changes (a lap
    number, a laptime).
  - This module matches a crop as ONE WHOLE IMAGE against a reference,
    because the thing you're detecting (the word "PAUSE", the "Choose a
    tyre type" pit-stop prompt, a menu's layout) is static -- it always
    looks the same when it's showing, so there's nothing to segment.

cv2.matchTemplate does normalized cross-correlation between the crop and
the reference, giving a score close to 1.0 for a strong match.
"""

import cv2
import numpy as np


def _resize_preserving_aspect(template, target_shape):
    """
    Resize template to FIT WITHIN target_shape (h, w) while preserving
    its own aspect ratio, padding any leftover space with black rather
    than stretching to fill exactly.

    This matters a lot in practice: a template saved with even a
    moderately different aspect ratio than the live crop region (e.g.
    cropped by eye with a different tool than the exact pixel slice used
    elsewhere) gets badly distorted by a plain stretch-resize, which can
    tank the correlation score even when the actual content is a genuine
    match -- confirmed in testing, where a template with a 0.135 h/w
    ratio matched against a 0.085 h/w ratio crop scored -0.05 (should
    have been a strong positive match) purely from stretch distortion.
    Padding preserves the template's true proportions -- the only cost
    is that the padded border area won't match the crop's real content
    there, which is a far smaller effect than warping the whole shape.
    """
    th, tw = template.shape[:2]
    target_h, target_w = target_shape

    scale = min(target_w / tw, target_h / th)
    new_w, new_h = max(1, int(tw * scale)), max(1, int(th * scale))
    resized = cv2.resize(template, (new_w, new_h))

    if template.ndim == 3:
        canvas = np.zeros((target_h, target_w, template.shape[2]), dtype=template.dtype)
    else:
        canvas = np.zeros((target_h, target_w), dtype=template.dtype)

    y_off = (target_h - new_h) // 2
    x_off = (target_w - new_w) // 2
    canvas[y_off : y_off + new_h, x_off : x_off + new_w] = resized
    return canvas


def detect_ui_state(crop, state_templates, threshold=0.8):
    best_state, best_score = None, -1.0

    for name, template in state_templates.items():
        if template.shape[:2] != crop.shape[:2]:
            template_resized = _resize_preserving_aspect(template, crop.shape[:2])
        else:
            template_resized = template

        result = cv2.matchTemplate(crop, template_resized, cv2.TM_CCOEFF_NORMED)
        score = float(result.max())

        if score > best_score:
            best_state, best_score = name, score

    if best_score < threshold:
        return None, best_score
    return best_state, best_score


def check_named_states(frame, state_configs, threshold=0.8):
    scores = {}
    for name, (region, template) in state_configs.items():
        y1, y2, x1, x2 = region
        crop = frame[y1:y2, x1:x2]
        _, score = detect_ui_state(crop, {name: template}, threshold=threshold)
        scores[name] = score

    for name, score in scores.items():
        if score >= threshold:
            return name, scores
    return None, scores


class NamedStateTracker:
    """
    Wraps check_named_states() with hysteresis, so a detected state
    doesn't instantly disappear the moment a single frame's template
    match score dips below threshold. Real footage isn't always clean --
    rain streaking across the camera, motion blur, a brief animation
    frame -- any of these can transiently drop a genuine match's score
    below threshold for an isolated frame even while the underlying
    screen state hasn't actually changed. Mirrors the same consecutive-
    miss pattern already used by
    MultiFieldActivityMonitor in telemetry_state.py for exactly this
    kind of stability, rather than reacting to every single frame.
    """

    def __init__(self, consecutive_misses_to_clear=3):
        self._misses_needed = consecutive_misses_to_clear
        self._current_state = None
        self._consecutive_misses = 0

    def update(self, frame, state_configs, threshold=0.8):
        """
        Call once per cycle with the current frame. Returns
        (current_state, scores) -- current_state is the smoothed state
        (may still be the previously-detected state even if THIS frame's
        raw match failed, as long as we haven't seen enough consecutive
        misses yet), and scores is this frame's raw per-state scores
        from check_named_states(), useful for debugging.
        """
        matched, scores = check_named_states(frame, state_configs, threshold=threshold)

        if matched is not None:
            self._current_state = matched
            self._consecutive_misses = 0
        elif self._current_state is not None:
            self._consecutive_misses += 1
            if self._consecutive_misses >= self._misses_needed:
                self._current_state = None
                self._consecutive_misses = 0

        return self._current_state, scores
