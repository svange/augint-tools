"""Section checkers for standardization audit."""

from augint_tools.standardize.sections.dotfiles import check_dotfiles
from augint_tools.standardize.sections.github import check_github
from augint_tools.standardize.sections.pipeline import check_pipeline
from augint_tools.standardize.sections.quality import check_quality
from augint_tools.standardize.sections.release import check_release
from augint_tools.standardize.sections.renovate import check_renovate

SECTION_CHECKERS = {
    "dotfiles": check_dotfiles,
    "quality": check_quality,
    "github": check_github,
    "pipeline": check_pipeline,
    "renovate": check_renovate,
    "release": check_release,
}

__all__ = [
    "SECTION_CHECKERS",
    "check_dotfiles",
    "check_github",
    "check_pipeline",
    "check_quality",
    "check_release",
    "check_renovate",
]
