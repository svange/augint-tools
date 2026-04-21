"""Interactive Textual health dashboard for GitHub repositories.

Widget-per-card architecture with registry-driven layouts and CSS-driven
themes. Connects to GitHub (repo + CI state, PRs, issues, teams).
"""

from .cmd import dashboard_command

__all__ = ["dashboard_command"]
