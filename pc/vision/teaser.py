"""Teaser tip detection by HSV color threshold.

Replace detect_teaser() with the existing teaser code if you have it;
keep the signature: frame (BGR) -> (x, y) or (None, None).
"""
import cv2
import numpy as np


def parse_hsv(s: str):
    """'h1,s1,v1,h2,s2,v2' -> (lower, upper) arrays for cv2.inRange."""
    vals = [int(v) for v in s.split(",")]
    if len(vals) != 6:
        raise ValueError("--teaser-hsv needs 6 numbers: h1,s1,v1,h2,s2,v2")
    return np.array(vals[:3], np.uint8), np.array(vals[3:], np.uint8)


def detect_teaser(frame, lower, upper, min_area: int = 30):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, lower, upper)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = [c for c in contours if cv2.contourArea(c) >= min_area]
    if not contours:
        return None, None
    m = cv2.moments(max(contours, key=cv2.contourArea))
    if m["m00"] == 0:
        return None, None
    return m["m10"] / m["m00"], m["m01"] / m["m00"]
