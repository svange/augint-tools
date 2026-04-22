"""ManageDeployments -- modal to edit deployment URLs for a single repo.

Layout modeled after AWS Security Groups: dedicated rows for production
and staging, then an add-row for supplementals followed by a list of
existing supplemental entries each with an inline Remove button.

Every mutation writes ``~/.augint-tools/deployments.yaml`` immediately.
"""

from __future__ import annotations

from textual import events
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from .. import deployments as dep


class ManageDeployments(ModalScreen[None]):
    """Modal for CRUD on the deployment-links yaml for a single repo."""

    DEFAULT_CSS = """
    ManageDeployments {
        align: center middle;
    }
    ManageDeployments > Container {
        width: 80;
        max-height: 85%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    ManageDeployments #md-title {
        text-style: bold;
        height: auto;
        margin: 0 0 1 0;
    }
    ManageDeployments .md-env-row {
        height: auto;
        margin: 0 0 1 0;
    }
    ManageDeployments .md-env-row Static {
        width: 18;
        height: 3;
        content-align-vertical: middle;
    }
    ManageDeployments .md-env-row Input {
        width: 1fr;
        margin: 0 1 0 0;
    }
    ManageDeployments .md-env-row Button {
        min-width: 8;
        margin: 0 0 0 1;
    }
    ManageDeployments #md-section-label {
        text-style: bold;
        height: auto;
        margin: 1 0 0 0;
    }
    ManageDeployments .md-add-row {
        height: auto;
        margin: 0 0 1 0;
    }
    ManageDeployments .md-add-row Input {
        margin: 0 1 0 0;
    }
    ManageDeployments .md-add-row #md-sup-label {
        width: 18;
    }
    ManageDeployments .md-add-row #md-sup-url {
        width: 1fr;
    }
    ManageDeployments .md-add-row Button {
        min-width: 8;
        margin: 0 0 0 1;
    }
    ManageDeployments #md-sup-list {
        height: auto;
        max-height: 12;
        margin: 0 0 1 0;
    }
    ManageDeployments .md-sup-row {
        height: 1;
        margin: 0 0 0 0;
    }
    ManageDeployments .md-sup-row .md-sup-lbl {
        width: 18;
        color: cyan;
    }
    ManageDeployments .md-sup-row .md-sup-url {
        width: 1fr;
    }
    ManageDeployments .md-sup-row Button {
        min-width: 10;
        margin: 0 0 0 1;
    }
    ManageDeployments #md-footer {
        color: $text-muted;
        height: auto;
        margin: 1 0 0 0;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_panel", "Close"),
    ]

    def __init__(self, full_name: str) -> None:
        super().__init__()
        self._full_name = full_name

    def compose(self):
        by_repo = dep.load_deployments()
        entries = by_repo.get(self._full_name, [])
        prod_url = next((e.url for e in entries if e.label == "main"), "")
        dev_url = next((e.url for e in entries if e.label == "dev"), "")

        yield Container(
            Static(f"Deployment links: {self._full_name}", id="md-title"),
            # Production row
            Horizontal(
                Static("Production (z)"),
                Input(value=prod_url, placeholder="https://...", id="md-prod-url"),
                Button("Set", id="md-prod-set", variant="primary"),
                Button("Clear", id="md-prod-clear", variant="error"),
                classes="md-env-row",
            ),
            # Staging row
            Horizontal(
                Static("Staging (x)"),
                Input(value=dev_url, placeholder="https://...", id="md-dev-url"),
                Button("Set", id="md-dev-set", variant="primary"),
                Button("Clear", id="md-dev-clear", variant="error"),
                classes="md-env-row",
            ),
            # Supplementals section
            Static("Supplementals (c, v, b)", id="md-section-label"),
            Horizontal(
                Input(placeholder="label", id="md-sup-label"),
                Input(placeholder="https://...", id="md-sup-url"),
                Button("Add", id="md-sup-add", variant="success"),
                classes="md-add-row",
            ),
            VerticalScroll(id="md-sup-list"),
            Static(
                "middle-click title or f to open  |  esc to close",
                id="md-footer",
            ),
        )

    def on_mount(self) -> None:
        self._refresh_supplementals()

    def _refresh_supplementals(self) -> None:
        """Rebuild the supplemental-links list from the yaml."""
        container = self.query_one("#md-sup-list", VerticalScroll)
        container.remove_children()
        by_repo = dep.load_deployments()
        entries = by_repo.get(self._full_name, [])
        supplementals = [e for e in entries if e.label not in ("main", "dev")]
        for idx, link in enumerate(supplementals):
            row = Horizontal(
                Static(link.label, classes="md-sup-lbl"),
                Static(link.url, classes="md-sup-url"),
                Button("Remove", id=f"md-sup-remove-{idx}", variant="error"),
                classes="md-sup-row",
            )
            container.mount(row)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "md-prod-set":
            self._set_env("main", "md-prod-url")
        elif btn_id == "md-prod-clear":
            self._clear_env("main", "md-prod-url")
        elif btn_id == "md-dev-set":
            self._set_env("dev", "md-dev-url")
        elif btn_id == "md-dev-clear":
            self._clear_env("dev", "md-dev-url")
        elif btn_id == "md-sup-add":
            self._add_supplemental()
        elif btn_id.startswith("md-sup-remove-"):
            self._remove_supplemental(int(btn_id.split("-")[-1]))

    def _set_env(self, label: str, input_id: str) -> None:
        url = self.query_one(f"#{input_id}", Input).value.strip()
        if not url:
            self.notify("url is required", severity="warning")
            return
        # Remove existing entry for this label, then add the new one.
        by_repo = dep.load_deployments()
        for existing in by_repo.get(self._full_name, []):
            if existing.label == label:
                dep.remove_link(self._full_name, label, existing.url)
                break
        dep.add_link(self._full_name, label, url)
        self.notify(f"set {label}")

    def _clear_env(self, label: str, input_id: str) -> None:
        by_repo = dep.load_deployments()
        for existing in by_repo.get(self._full_name, []):
            if existing.label == label:
                dep.remove_link(self._full_name, label, existing.url)
                self.query_one(f"#{input_id}", Input).value = ""
                self.notify(f"cleared {label}")
                return
        self.notify(f"no {label} url to clear", severity="information")

    def _add_supplemental(self) -> None:
        label = self.query_one("#md-sup-label", Input).value.strip()
        url = self.query_one("#md-sup-url", Input).value.strip()
        if not label or not url:
            self.notify("label and url are required", severity="warning")
            return
        if label in ("main", "dev"):
            self.notify("use the fields above for main/dev", severity="warning")
            return
        dep.add_link(self._full_name, label, url)
        self.query_one("#md-sup-label", Input).value = ""
        self.query_one("#md-sup-url", Input).value = ""
        self.notify(f"added {label}")
        self._refresh_supplementals()

    def _remove_supplemental(self, index: int) -> None:
        by_repo = dep.load_deployments()
        entries = by_repo.get(self._full_name, [])
        supplementals = [e for e in entries if e.label not in ("main", "dev")]
        if 0 <= index < len(supplementals):
            link = supplementals[index]
            dep.remove_link(self._full_name, link.label, link.url)
            self.notify(f"removed {link.label}")
            self._refresh_supplementals()

    # ---- dismiss ----

    def on_click(self, event: events.Click) -> None:
        if event.button == 3:
            self.action_dismiss_panel()

    def action_dismiss_panel(self) -> None:
        self.dismiss(None)
