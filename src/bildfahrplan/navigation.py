"""Testbare Zeitbereichsregeln der Bildfahrplanansicht."""

TIME_MIN = 5 * 3600
TIME_MAX = 21 * 3600
ROUTE_AXIS_POSITION = "top"
X_INTERACTION_ENABLED = False
Y_INTERACTION_ENABLED = True


def time_bounds(reference: float = 0) -> tuple[float, float]:
    day = int(reference // 86400) * 86400
    return day + TIME_MIN, day + TIME_MAX


def clamp_time_range(start: float, end: float, reference: float = 0) -> tuple[float, float]:
    minimum, maximum = time_bounds(reference)
    span = min(max(1.0, end - start), maximum - minimum)
    start = max(minimum, min(start, maximum - span))
    return start, start + span


def centered_time_range(now: float, current: tuple[float, float]) -> tuple[float, float]:
    span = current[1] - current[0]
    return clamp_time_range(now - span / 2, now + span / 2, now)
