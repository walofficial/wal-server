import math
from datetime import datetime, timedelta, timezone
from typing import Final

# --- Configuration for Logarithmic Validity ---
# Define the minimum and maximum validity periods in hours.
MIN_VALIDITY_HOURS: Final[float] = 5 / 60  # 5 minutes for score of 0
MAX_VALIDITY_HOURS: Final[float] = 14.0  # 48 hours (2 days) for score of 100

# Score range constants
MIN_SCORE: Final[float] = 0.0
MAX_SCORE: Final[float] = 100.0
SCORE_OFFSET: Final[float] = 1.0  # Added to handle log(0)

# --- Pre-calculate constants for efficiency ---
LOG_MAX_SCORE_PLUS_1: Final[float] = math.log(MAX_SCORE + SCORE_OFFSET)
DURATION_RANGE_HOURS: Final[float] = MAX_VALIDITY_HOURS - MIN_VALIDITY_HOURS


def calculate_valid_until_logarithmic(score: float) -> datetime:
    clamped_score = max(MIN_SCORE, min(score, MAX_SCORE))

    # Calculate log(score + 1) to handle score 0 correctly
    log_score = math.log(clamped_score + SCORE_OFFSET)

    # Normalize the logarithmic score to [0, 1] range
    normalized_score = log_score / LOG_MAX_SCORE_PLUS_1

    # Map to validity duration between min and max hours
    validity_hours = MIN_VALIDITY_HOURS + (normalized_score * DURATION_RANGE_HOURS)

    # Calculate expiration time from current UTC time
    current_time = datetime.now(timezone.utc)
    valid_until = current_time + timedelta(hours=validity_hours)

    return valid_until
