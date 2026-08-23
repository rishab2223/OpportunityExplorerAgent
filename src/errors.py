from __future__ import annotations


STEP_LABELS = {
    "load_resume": "Load resume",
    "scrape": "Scrape jobs",
    "score": "Score jobs",
    "enrich": "Interview prep & resume suggestions",
}


class StepError(Exception):
    def __init__(
        self,
        step_name: str,
        message: str,
        detail: str = "",
        fallbacks: str = "",
        what_happened: str = "",
    ) -> None:
        super().__init__(message)
        self.step_name = step_name
        self.message = message
        self.detail = detail
        self.fallbacks = fallbacks
        self.what_happened = what_happened or (
            f"{STEP_LABELS.get(step_name, step_name)} failed. Later steps were not run."
        )
