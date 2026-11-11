"""Generate small fixture resume PDFs for the load test (fake people, no real PII).

Run: uv run python loadtest/generate_fixtures.py
Writes loadtest/fixtures/resume_1.pdf ... resume_N.pdf (N = FIXTURES, default 3).
Each PDF has distinct text, so each is a distinct cache entry.
"""

import os
from pathlib import Path

import fitz

FIXTURES_DIR = Path(__file__).parent / "fixtures"

TEMPLATE = """\
{name}
{title} — Example City
sample{i}@example.invalid

SUMMARY
{title} with {years} years of experience building web services, data
pipelines and internal tools. Comfortable owning features end to end,
from design through deployment and monitoring.

EXPERIENCE
Example Corp — {title} ({start}–present)
- Built and operated REST APIs serving {reqs}k requests/day.
- Cut p95 latency {pct}% by profiling and caching hot paths.
- Mentored {juniors} junior engineers; led weekly design reviews.

SKILLS
Python, FastAPI, PostgreSQL, Redis, Docker, CI/CD, Prometheus, {extra}

EDUCATION
B.S. Computer Science, Example State University
"""

PROFILES = [
    ("Alex Sample", "Backend Engineer", 5, 2021, 40, 35, 2, "Kubernetes"),
    ("Blake Fixture", "Data Engineer", 3, 2023, 15, 20, 1, "Airflow"),
    ("Casey Placeholder", "Platform Engineer", 7, 2019, 80, 45, 3, "Terraform"),
]


def main() -> None:
    count = int(os.getenv("FIXTURES", "3"))
    FIXTURES_DIR.mkdir(exist_ok=True)
    for i in range(count):
        name, title, years, start, reqs, pct, juniors, extra = PROFILES[i % len(PROFILES)]
        text = TEMPLATE.format(
            # The index keeps every fixture's text distinct even past len(PROFILES).
            name=f"{name} {i + 1}",
            title=title,
            years=years,
            start=start,
            reqs=reqs,
            pct=pct,
            juniors=juniors,
            extra=extra,
            i=i + 1,
        )
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=10)
        path = FIXTURES_DIR / f"resume_{i + 1}.pdf"
        doc.save(str(path))
        doc.close()
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
