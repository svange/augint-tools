"""Validation phases and preset definitions."""

from enum import StrEnum


class Phase(StrEnum):
    """Named validation phases."""

    QUALITY = "quality"
    SECURITY = "security"
    LICENSES = "licenses"
    TESTS = "tests"
    BUILD = "build"


PRESETS: dict[str, list[Phase]] = {
    "quick": [Phase.QUALITY],
    "default": [Phase.QUALITY, Phase.TESTS],
    "full": [Phase.QUALITY, Phase.SECURITY, Phase.LICENSES, Phase.TESTS, Phase.BUILD],
    "ci": [],  # resolved from CI config at plan time
}
