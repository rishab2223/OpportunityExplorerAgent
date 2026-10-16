from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class JobPosting(BaseModel):
    source: str
    job_id: str
    title: str = ""
    company: str = ""
    location: str = ""
    workplace_type: str = ""
    listing_url: str = ""
    apply_url: str = ""
    recruiter_name: str = ""
    recruiter_title: str = ""
    recruiter_profile_url: str = ""
    posted_at: str = ""
    salary: str = ""
    employment_type: str = ""
    description: str = ""
    # Why a filter in review mode held this job back, e.g. "asks for 8+
    # years; your limit is 7". Empty for a job no filter touched.
    held_back: str = ""
    scraped_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class JobScore(BaseModel):
    # Out of score_scale: 100 since Oct 2026, the sum of score_parts. A run
    # saved before then scored 1-10, and its records carry no scale - hence
    # the default.
    relevance: int = Field(ge=0, le=100)
    why_score: str = ""
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    score_scale: int = 10
    # Points per part of the rubric (score.PARTS), e.g. {"skills": 30, ...}.
    score_parts: dict[str, int] = Field(default_factory=dict)
    # What the stronger second look said, for a job just under the bar.
    second_look: str = ""
    # The scoring call failed twice: the job is kept, unscored, so a good
    # one is not lost - and it says so.
    score_failed: bool = False


class ScoredJob(JobPosting, JobScore):
    pass


class MatchEnrichment(BaseModel):
    resume_edit_suggestions: str = ""
    interview_prep: str = ""


class SectionEdit(BaseModel):
    heading: str = Field(
        "",
        description="Section heading, copied exactly from the SECTIONS list",
    )
    latex_body: str = Field(
        "",
        description=(
            "Raw LaTeX replacing everything between this \\section line and the "
            "next one. No markdown fences; no \\section, \\documentclass, or "
            "\\usepackage commands."
        ),
    )


class TexEnrichment(MatchEnrichment):
    section_edits: list[SectionEdit] = Field(
        default_factory=list,
        description=(
            "Replacement bodies for only the sections being tailored; a section "
            "not listed here stays unchanged"
        ),
    )


class MatchRecord(ScoredJob, MatchEnrichment):
    resume_latex: str = ""
    resume_source: str = ""
    resume_source_detail: str = ""
    resume_tex_file: str = ""
    resume_pdf_file: str = ""
    resume_pdf_path: str = ""
    resume_pdf_error: str = ""
    resume_pages: int = 0
    latex_skip_reason: str = ""
    written_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
