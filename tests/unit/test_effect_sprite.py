"""Tests for the EffectSprite animation system."""

from __future__ import annotations

import pytest
from rich.text import Text

from augint_tools.dashboard.widgets.effect_sprite import (
    _FRAME_INTERVAL_SECONDS,
    SPRITE_DEFS,
    SPRITE_HEIGHT,
    SPRITE_WIDTH,
    EffectKind,
    EffectSprite,
)

# ---------------------------------------------------------------------------
# SPRITE_DEFS structure
# ---------------------------------------------------------------------------

ALL_KINDS: list[EffectKind] = ["sparkle", "shockwave", "warning", "shimmer"]


def test_all_kinds_present_in_sprite_defs() -> None:
    for kind in ALL_KINDS:
        assert kind in SPRITE_DEFS, f"Missing kind '{kind}' in SPRITE_DEFS"


def test_each_kind_has_frames_and_colors() -> None:
    for kind in ALL_KINDS:
        defn = SPRITE_DEFS[kind]
        assert "frames" in defn, f"'{kind}' missing 'frames'"
        assert "colors" in defn, f"'{kind}' missing 'colors'"


def test_frames_and_colors_same_length_per_kind() -> None:
    for kind in ALL_KINDS:
        defn = SPRITE_DEFS[kind]
        assert len(defn["frames"]) == len(defn["colors"]), (
            f"'{kind}' frames length {len(defn['frames'])} != colors length {len(defn['colors'])}"
        )


def test_sparkle_has_8_frames() -> None:
    assert len(SPRITE_DEFS["sparkle"]["frames"]) == 8


def test_shockwave_has_8_frames() -> None:
    assert len(SPRITE_DEFS["shockwave"]["frames"]) == 8


def test_warning_has_4_frames() -> None:
    assert len(SPRITE_DEFS["warning"]["frames"]) == 4


def test_shimmer_has_7_frames() -> None:
    assert len(SPRITE_DEFS["shimmer"]["frames"]) == 7


# ---------------------------------------------------------------------------
# Frame geometry
# ---------------------------------------------------------------------------


def _check_frame_geometry(kind: str, frame: str) -> None:
    lines = frame.split("\n")
    assert len(lines) == SPRITE_HEIGHT, (
        f"'{kind}' frame has {len(lines)} lines, expected {SPRITE_HEIGHT}"
    )
    for i, line in enumerate(lines):
        assert len(line) == SPRITE_WIDTH, (
            f"'{kind}' frame line {i} is {len(line)} chars wide, expected {SPRITE_WIDTH}"
        )


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_all_frames_correct_geometry(kind: EffectKind) -> None:
    for _idx, frame in enumerate(SPRITE_DEFS[kind]["frames"]):
        _check_frame_geometry(kind, frame)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_sprite_width_is_7() -> None:
    assert SPRITE_WIDTH == 7


def test_sprite_height_is_5() -> None:
    assert SPRITE_HEIGHT == 5


def test_frame_interval_is_0_12() -> None:
    assert _FRAME_INTERVAL_SECONDS == pytest.approx(0.12)


# ---------------------------------------------------------------------------
# EffectSprite class
# ---------------------------------------------------------------------------


def test_auto_dismiss_is_true() -> None:
    assert EffectSprite.auto_dismiss is True


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_kind_property(kind: EffectKind) -> None:
    sprite = EffectSprite(kind=kind)
    assert sprite.kind == kind


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_render_returns_text_for_each_frame(kind: EffectKind) -> None:
    sprite = EffectSprite(kind=kind)
    frames = SPRITE_DEFS[kind]["frames"]
    for idx in range(len(frames)):
        sprite.frame_idx = idx
        result = sprite.render()
        assert isinstance(result, Text), (
            f"render() for kind='{kind}' frame {idx} returned {type(result)}, expected Text"
        )


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_render_frame_content(kind: EffectKind) -> None:
    sprite = EffectSprite(kind=kind)
    frames = SPRITE_DEFS[kind]["frames"]
    for idx, expected_glyphs in enumerate(frames):
        sprite.frame_idx = idx
        result = sprite.render()
        assert result.plain == expected_glyphs, (
            f"kind='{kind}' frame {idx}: got {result.plain!r}, expected {expected_glyphs!r}"
        )


def test_advance_removes_after_last_frame() -> None:
    """_advance() should call self.remove() after the last frame, not loop."""
    removed: list[bool] = []

    sprite = EffectSprite(kind="warning")
    sprite.remove = lambda: removed.append(True)  # type: ignore[method-assign]

    frames = SPRITE_DEFS["warning"]["frames"]
    total = len(frames)

    # Advance through all frames; remove should not be called until after the last frame
    for step in range(total - 1):
        sprite._advance()
        assert len(removed) == 0, f"remove() called too early at step {step}"

    # One more advance finishes the cycle
    sprite._advance()
    assert len(removed) == 1, "remove() should be called exactly once after the last frame"


def test_advance_increments_frame_idx() -> None:
    sprite = EffectSprite(kind="shimmer")
    removed: list[bool] = []
    sprite.remove = lambda: removed.append(True)  # type: ignore[method-assign]

    assert sprite.frame_idx == 0
    sprite._advance()
    assert sprite.frame_idx == 1
    sprite._advance()
    assert sprite.frame_idx == 2
