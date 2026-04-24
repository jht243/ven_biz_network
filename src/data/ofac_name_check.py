"""
Registry-driven OFAC SDN "name-check" answer pages.

WHY THIS MODULE EXISTS
──────────────────────
Google Search Console shows real compliance-officer queries of the form
`"<SURNAME LAST, First>" ofac sdn` that we get impressions on (because
our per-SDN profile pages share the surname) but no clicks (because the
literal "First Last" string isn't in our titles — we're a Venezuela site
and the canonical person sits on a different OFAC program). The obvious
example: `"rodriguez hernandez, juan" ofac sdn` — 540 impressions / 0 CTR
in the 90-day report, where the only SDN entry matching that exact name
is Jose Saul Rodriguez Hernandez (Mexico, CJNG fentanyl network,
E.O. 14059), which we intentionally do not ingest.

The play is not to expand scrape scope (that dilutes Venezuela focus and
puts us in direct competition with OpenSanctions on every SDN entry).
Instead we build a small, hand-curated set of *answer pages* that
resolve each query with:

  1. A direct yes/no for the Venezuela SDN list (the list we actually
     own), surfaced above the fold and in the `<title>`.
  2. Disambiguation against the closest exact-name match on the full
     SDN list, with a deep link to OFAC's official source so a
     compliance officer can verify in one click.
  3. A cluster of Venezuela SDNs that share a surname with the queried
     name, each linking into our existing `/sanctions/individuals/<slug>`
     profiles. This is the internal-link value the page returns: a user
     who landed here by typo still gets pulled into our Venezuela
     coverage.

DATA CONTRACT
─────────────
Each `NameCheckAnswer` is a hand-authored record. We deliberately do
*not* auto-populate from OFAC scrapes — every entry ships with a
human-verified cross-reference to OFAC's authoritative source. The
`slug` is the URL segment, stable for the life of the page. The
`adjacent_cluster_surnames` list drives the "other Venezuela SDNs that
share your surname" section by calling `sdn_profiles.list_by_surname`
at render time, so the cluster auto-refreshes whenever our OFAC scraper
ingests a new designation.

EXTENDING
─────────
To add a new answer page:
  1. Verify the OFAC SDN CSV / AKA CSV for every exact-name variant of
     the queried name (see scripts/ or the curl incantation in the
     transcript linked in the commit message).
  2. Append a `NameCheckAnswer` below. The slug becomes the URL.
  3. Append the URL to the sitemap static block in server.py.
  4. Submit the URL via IndexNow/Google so the answer page gets crawled
     before the next GSC impression wave.

A missing entry returns None from `get_answer()` and the route returns
404 — that is the safe default. Do NOT silently fall through to a
generic "we have no data" page; an empty answer page for every
conceivable name query would be a thin-content disaster in GSC.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ExternalSDNMatch:
    """A name-match that exists on the OFAC SDN list but NOT in the
    Venezuela-program subset we publish profile pages for.

    We show these as amber-callout cross-references so a compliance
    officer who mis-typed a Venezuela query (or who is actually hunting
    a Mexican / Colombian / Nicaraguan SDN that happens to share the
    surname) doesn't dead-end on our page.
    """
    name: str                # exact OFAC SURNAME, Given Names form
    program: str             # full program code, e.g. ILLICIT-DRUGS-EO14059
    program_label: str       # human-readable, e.g. "E.O. 14059 — CJNG / illicit-drugs"
    nationality: str         # "Mexico", "Colombia", etc.
    dob: str                 # ISO-formatted or human, e.g. "05 Oct 1968"
    place_of_birth: str      # "Oaxaca, Mexico"
    remarks_extract: str     # short human sentence summarizing the designation context
    ofac_source_url: str     # direct link to OFAC's sanctionssearch.ofac.treas.gov Details.aspx
    press_release_url: Optional[str] = None  # Treasury press release, if available


@dataclass(frozen=True)
class NameCheckAnswer:
    """One answer page for one literal compliance query.

    Rendered at `/tools/ofac-sdn-name-check/<slug>`.

    CONTRACT:
      - `query_verbatim` is placed in the H1 and `<title>` exactly as the
        user typed it (surname-first, comma, first name) so Google can
        match on the tokenized query.
      - `natural_name` is the "First Middle Last" form used in body
        copy and meta description.
      - `on_venezuela_sdn` must be False unless the name literally
        appears on the Venezuela program (in which case you should be
        creating a `/sanctions/individuals/<slug>` profile, not a
        name-check page).
      - `external_sdn_matches` is ORDERED — the first entry is treated
        as the "closest exact match" and promoted to the hero callout.
    """
    slug: str
    query_verbatim: str
    natural_name: str
    surnames: tuple[str, ...]
    on_venezuela_sdn: bool
    answer_headline: str
    answer_summary: str  # <=160 chars — used as meta description
    disambiguation_note: str  # the "why this search is ambiguous" paragraph
    external_sdn_matches: tuple[ExternalSDNMatch, ...] = field(default_factory=tuple)
    related_queries: tuple[str, ...] = field(default_factory=tuple)
    last_verified_iso: str = "2026-04-24"


# ──────────────────────────────────────────────────────────────────────
# The registry.
# ──────────────────────────────────────────────────────────────────────
#
# Each entry below has been verified against the raw OFAC SDN CSV
# (https://sanctionslistservice.ofac.treas.gov/api/publicationpreview/exports/sdn.csv)
# and the AKA CSV (.../exports/alt.csv). The provenance audit timestamp
# is carried on `last_verified_iso` and surfaced in the page footer
# so a compliance reader knows how fresh the cross-reference is.

_ANSWERS: dict[str, NameCheckAnswer] = {
    # ─────────────────────────────────────────────────────────────
    # GSC query: `"rodriguez hernandez, juan" ofac sdn` — 540
    # impressions / 0 clicks in the 90-day audit.
    #
    # OFAC audit (2026-04-24):
    #   • Full SDN CSV scan for "RODRIGUEZ HERNANDEZ" surfaces exactly
    #     ONE primary entry: ent_num 49765, "RODRIGUEZ HERNANDEZ,
    #     Jose Saul", program ILLICIT-DRUGS-EO14059 (CJNG / fentanyl).
    #   • Full SDN CSV scan for "Juan" + "RODRIGUEZ" + "HERNANDEZ"
    #     returns zero rows. Juan-named Venezuela SDNs exist (Mendoza
    #     Jover, Dugarte Padron, Hidalgo Pandares) but none with both
    #     surnames.
    #   • AKA CSV scan: no aliases map to "Rodriguez Hernandez, Juan".
    # ─────────────────────────────────────────────────────────────
    "rodriguez-hernandez-juan": NameCheckAnswer(
        slug="rodriguez-hernandez-juan",
        query_verbatim='Rodriguez Hernandez, Juan',
        natural_name="Juan Rodriguez Hernandez",
        surnames=("Rodriguez", "Hernandez"),
        on_venezuela_sdn=False,
        answer_headline='No person named "Rodriguez Hernandez, Juan" appears on the OFAC Venezuela SDN list.',
        answer_summary=(
            "No Juan Rodriguez Hernandez on the OFAC Venezuela SDN list as of April 2026. "
            "The only exact surname match on the full SDN list is Jose Saul Rodriguez Hernandez, "
            "designated under the Mexico fentanyl program (E.O. 14059)."
        ),
        disambiguation_note=(
            "Spanish-language naming conventions make this query ambiguous. In the "
            "OFAC SDN format \"RODRIGUEZ HERNANDEZ, Juan\" the first token is the "
            "paternal surname (apellido paterno), the second is the maternal surname "
            "(apellido materno), and the given name follows the comma. A compliance "
            "officer screening a counterparty named \"Juan Rodriguez\" in a Venezuelan "
            "contract should also test \"Rodriguez\" or \"Hernandez\" as single-surname "
            "queries and check every active Venezuela SDN that shares either surname — "
            "we surface that full list below."
        ),
        external_sdn_matches=(
            ExternalSDNMatch(
                name="RODRIGUEZ HERNANDEZ, Jose Saul",
                program="ILLICIT-DRUGS-EO14059",
                program_label="E.O. 14059 — Counter-fentanyl / illicit drugs (unrelated to Venezuela)",
                nationality="Mexico",
                dob="05 Oct 1968",
                place_of_birth="Oaxaca, Mexico",
                remarks_extract=(
                    "Designated in September 2024 as a front person for the El Tanque "
                    "fuel-theft network linked to CJNG (Cartel de Jalisco Nueva "
                    "Generacion). Sanctioned alongside four other individuals and "
                    "13 Veracruz-based companies."
                ),
                ofac_source_url="https://sanctionssearch.ofac.treas.gov/Details.aspx?id=49765",
                press_release_url="https://home.treasury.gov/news/press-releases/jy2568",
            ),
        ),
        related_queries=(
            '"rodriguez gomez, jorge jesus" ofac sdn',
            '"hernandez dala, ivan rafael" ofac sdn',
            'ofac sdn venezuela cedula lookup',
            'juan rodriguez venezuela sanctions',
        ),
        last_verified_iso="2026-04-24",
    ),
}


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────


def get_answer(slug: str) -> Optional[NameCheckAnswer]:
    """Resolve a name-check answer by URL slug.

    Returns None for any slug not in the hand-curated registry so the
    Flask route can 404 cleanly. See module docstring for why this is
    the safe default.
    """
    if not slug:
        return None
    return _ANSWERS.get(slug.strip().lower())


def list_answer_slugs() -> list[str]:
    """Every registered slug. Used by the sitemap builder and IndexNow
    submitter so new entries get crawled the moment they ship."""
    return sorted(_ANSWERS.keys())


def list_answers() -> list[NameCheckAnswer]:
    """Every answer, alpha-sorted by slug. Used by the hub page so
    a user browsing /tools/ofac-sdn-name-check/ can see what queries
    we've already answered."""
    return [_ANSWERS[slug] for slug in list_answer_slugs()]
