"""
People registry — the seed list of Venezuelan power figures that powers
the /people/ cluster.

Why this exists:
  Google Search Console shows that even pages with no editorial intent
  to rank on a person's name are pulling in name searches (e.g. someone
  searches "arianny seijo noguera" and lands on a briefing that just
  happens to mention her). That's incidental traffic with no place to
  convert. A dedicated /people/<slug> page per power figure captures
  that intent: the H1 + title match the query verbatim, schema.org
  Person markup makes us Knowledge-Panel-eligible, and the page funnels
  the reader into our wider Venezuela investment / sanctions coverage.

Stage 1 (this module) is a hand-curated registry — we own the bios,
roles, and the network graph between people. Stage 2 will extend the
same registry from the daily scraper (auto-stub new figures who appear
in scraped articles, then promote to full profiles on review).

Data shape rationale:
  • A single PERSON dict per slug, organised in a flat module-level
    dict (matches how src/data/sdn_profiles.py exposes its corpus and
    how src/data/caracas_landmarks.py exposes its rows).
  • Slug is the URL path component AND the dict key — once published
    to Google, a slug is permanent (changing it forfeits the rank).
  • `cohorts` is a list, not a single value: a person can legitimately
    sit in multiple cohort hubs (e.g. Cabello is both "executive" and
    "sanctioned"). Each cohort gets a `/people/by-role/<cohort>` hub,
    and the profile page surfaces "Other people in {cohort}" sibling
    grids for every cohort the person belongs to.
  • `sanctioned_slug` (optional) cross-links to the existing
    /sanctions/individuals/<slug> profile when the person is on the SDN
    list. This is the single highest-leverage link we can publish:
    bidirectional sanctions ↔ people graph, free authority transfer.
  • `wikidata_id` (optional) becomes the `sameAs` in the Person
    JSON-LD. This is the strongest signal Google uses to match a
    profile page to an existing Knowledge Graph entity.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional


# ──────────────────────────────────────────────────────────────────────
# Cohort taxonomy
# ──────────────────────────────────────────────────────────────────────
#
# Five top-level cohorts. Each becomes a /people/by-role/<slug> hub.
# Order matters: it controls the order cohort badges render on the
# profile page, and the order cohorts appear on the /people index.
#
# We deliberately keep this short. Five hubs is enough cohort coverage
# without splintering internal-link signal across too many thin pages.
# Sub-cohorts (e.g. "PDVSA exec" inside "executive") are expressed as
# tags inside the profile, not as separate hubs.

COHORTS: dict[str, dict[str, str]] = {
    "executive": {
        "label": "Executive & cabinet",
        "tagline": "Maduro government, vice-presidents, ministers, and the inner circle.",
        "anchor": "Venezuela executive & cabinet — Maduro government and ministers",
    },
    "energy": {
        "label": "PDVSA & energy",
        "tagline": "PDVSA leadership, oil ministry, and the people running Venezuela's energy sector.",
        "anchor": "PDVSA & Venezuela energy-sector leadership",
    },
    "military": {
        "label": "Military & security",
        "tagline": "FANB high command, GNB, DGCIM, SEBIN, and the armed-forces leadership.",
        "anchor": "Venezuela military & security leadership (FANB, GNB, DGCIM, SEBIN)",
    },
    "judiciary": {
        "label": "Judiciary & electoral",
        "tagline": "Attorney General, Supreme Court (TSJ), CNE, and the legal apparatus.",
        "anchor": "Venezuela judiciary & electoral leadership (TSJ, CNE, Fiscalía)",
    },
    "opposition": {
        "label": "Opposition & exile",
        "tagline": "Opposition leaders, exile figures, and pro-democracy organisers.",
        "anchor": "Venezuelan opposition & exile leadership",
    },
}


# ──────────────────────────────────────────────────────────────────────
# Dataclasses
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TimelineEntry:
    """One row in a person's career timeline."""
    year: str           # "2017" or "2017–2019" — string, not int (ranges)
    event: str          # "Appointed Vice President of Venezuela by Nicolás Maduro"


@dataclass(frozen=True)
class FAQ:
    q: str
    a: str


@dataclass(frozen=True)
class Source:
    label: str
    url: str


@dataclass(frozen=True)
class Person:
    slug: str
    name: str                          # canonical display name
    role: str                          # short role label, e.g. "Attorney General of Venezuela"
    cohorts: tuple[str, ...]           # one or more keys from COHORTS

    # Search-snippet copy
    one_liner: str                     # one-sentence "who is this" — feeds meta description
    bio: tuple[str, ...]               # 2–4 paragraph long-form bio (rendered as <p>s)

    aliases: tuple[str, ...] = ()      # alt names → JSON-LD alternateName + aliases section
    born: Optional[str] = None         # ISO date or "1962" — feeds Person.birthDate
    birthplace: Optional[str] = None   # feeds Person.birthPlace
    nationality: str = "Venezuelan"
    in_office_since: Optional[str] = None
    affiliations: tuple[str, ...] = () # short list, e.g. "PSUV", "GPP coalition"

    timeline: tuple[TimelineEntry, ...] = ()
    faqs: tuple[FAQ, ...] = ()
    sources: tuple[Source, ...] = ()

    # Cross-cluster anchors
    sector_path: Optional[str] = None  # e.g. "/sectors/oil-gas" for PDVSA execs
    sanctioned_slug: Optional[str] = None  # /sanctions/individuals/<slug> if on SDN
    wikidata_id: Optional[str] = None  # e.g. "Q333271" — drives sameAs

    # Network — slugs of related people (boss, peers, family). Bidirectional
    # surfacing happens at render time so we don't have to maintain inverse
    # edges manually.
    related: tuple[str, ...] = ()

    @property
    def url_path(self) -> str:
        return f"/people/{self.slug}"

    @property
    def primary_cohort(self) -> str:
        return self.cohorts[0] if self.cohorts else "executive"


# ──────────────────────────────────────────────────────────────────────
# Seed registry — Stage 1 v1
# ──────────────────────────────────────────────────────────────────────
#
# Ten seed figures chosen to (a) cover the five cohorts, (b) include
# the AG (Arianny Seijo Noguera) since that's the live GSC signal, and
# (c) cover names with high search demand in English (Maduro, María
# Corina Machado, Edmundo González).

PEOPLE: dict[str, Person] = {
    "nicolas-maduro": Person(
        slug="nicolas-maduro",
        name="Nicolás Maduro",
        aliases=("Nicolás Maduro Moros", "Maduro"),
        role="President of Venezuela",
        cohorts=("executive",),
        one_liner=(
            "Nicolás Maduro has been President of Venezuela since 2013, leading "
            "the PSUV-controlled government and overseeing the country's response "
            "to U.S. sanctions and the oil-sector crisis."
        ),
        bio=(
            "Nicolás Maduro Moros assumed the Venezuelan presidency in April 2013 "
            "following the death of Hugo Chávez, having previously served as "
            "Foreign Minister and Vice President. His tenure has been defined by "
            "U.S.-led sanctions, hyperinflation, the collapse of PDVSA output, and "
            "a series of contested elections.",
            "For foreign investors, Maduro's administration is the counterparty "
            "to every PDVSA joint venture, every license application processed "
            "by OFAC, and every regulatory ruling issued by the National Assembly "
            "and the TSJ. His direct circle — Delcy Rodríguez, Jorge Rodríguez, "
            "and Diosdado Cabello — controls the day-to-day policy apparatus.",
        ),
        born="1962-11-23",
        birthplace="Caracas, Venezuela",
        in_office_since="2013-04-19",
        affiliations=("PSUV", "GPP coalition"),
        timeline=(
            TimelineEntry("2006–2013", "Foreign Minister of Venezuela"),
            TimelineEntry("2012–2013", "Executive Vice President of Venezuela"),
            TimelineEntry("2013", "Assumed the presidency after the death of Hugo Chávez"),
            TimelineEntry("2018", "Re-elected in an election widely rejected by the U.S., EU, and most Latin American governments"),
            TimelineEntry("2024", "Declared winner of the July 28 presidential election; results disputed by the opposition and rejected by the U.S."),
        ),
        faqs=(
            FAQ(
                q="Who is Nicolás Maduro?",
                a="Nicolás Maduro Moros is the President of Venezuela, in office since April 2013. He leads the PSUV and the GPP coalition government and has been the central figure in Venezuela's relationship with U.S. sanctions, OFAC general licenses, and the PDVSA oil sector throughout his tenure.",
            ),
            FAQ(
                q="Is Nicolás Maduro sanctioned by the U.S.?",
                a="Yes. Maduro was added to the OFAC SDN list in 2017 under Venezuela-related sanctions programs. U.S. persons are prohibited from transacting with him, and the U.S. State Department has offered a reward for information leading to his arrest in connection with narcoterrorism charges.",
            ),
            FAQ(
                q="Why does Maduro matter to foreign investors?",
                a="Every Venezuela-related investment decision — joint ventures with PDVSA, sovereign or PDVSA bond positions, OFAC license applications, and operational permits — ultimately routes through institutions controlled by the Maduro government. Sector regulation, foreign-exchange access, and dispute resolution all answer to his administration.",
            ),
        ),
        sources=(
            Source("Wikipedia: Nicolás Maduro", "https://en.wikipedia.org/wiki/Nicol%C3%A1s_Maduro"),
            Source("OFAC SDN entry", "https://sanctions-search.ofac.treasury.gov/"),
            Source("U.S. Department of State Rewards for Justice", "https://rewardsforjustice.net/rewards/nicolas-maduro-moros/"),
        ),
        wikidata_id="Q333271",
        related=("delcy-rodriguez", "diosdado-cabello", "vladimir-padrino-lopez", "maria-corina-machado"),
    ),

    "delcy-rodriguez": Person(
        slug="delcy-rodriguez",
        name="Delcy Rodríguez",
        aliases=("Delcy Eloína Rodríguez Gómez", "Delcy Rodríguez Gómez"),
        role="Executive Vice President of Venezuela",
        cohorts=("executive",),
        one_liner=(
            "Delcy Rodríguez is Venezuela's Executive Vice President and Minister "
            "of Economy and Finance — the senior cabinet figure managing the "
            "economic relationship with U.S. sanctions and PDVSA."
        ),
        bio=(
            "Delcy Eloína Rodríguez Gómez has served as Executive Vice President "
            "of Venezuela since 2018 and concurrently as Minister of Economy, "
            "Finance, and Foreign Trade. She previously served as Foreign Minister "
            "and as President of the 2017 Constituent Assembly.",
            "Rodríguez is the principal point of contact for foreign-investor "
            "delegations and has led negotiations with international financial "
            "institutions, foreign creditors, and oil-sector counterparties under "
            "the OFAC general-license framework.",
        ),
        born="1969-05-18",
        birthplace="Caracas, Venezuela",
        in_office_since="2018-06-14",
        affiliations=("PSUV",),
        timeline=(
            TimelineEntry("2014–2017", "Foreign Minister of Venezuela"),
            TimelineEntry("2017–2018", "President of the National Constituent Assembly"),
            TimelineEntry("2018", "Appointed Executive Vice President"),
            TimelineEntry("2020", "Took on concurrent role as Minister of Economy, Finance, and Foreign Trade"),
        ),
        faqs=(
            FAQ(
                q="Who is Delcy Rodríguez?",
                a="Delcy Rodríguez is the Executive Vice President of Venezuela, in office since June 2018, and concurrently the Minister of Economy, Finance, and Foreign Trade. She is the senior cabinet official managing the country's economic policy and the principal counterpart for foreign investor delegations.",
            ),
            FAQ(
                q="Is Delcy Rodríguez sanctioned?",
                a="Yes. Rodríguez was added to the OFAC SDN list in 2018 under Venezuela-related programs, and is also subject to EU and Canadian sanctions. U.S. persons are prohibited from transacting with her.",
            ),
        ),
        sources=(
            Source("Wikipedia: Delcy Rodríguez", "https://en.wikipedia.org/wiki/Delcy_Rodr%C3%ADguez"),
        ),
        wikidata_id="Q5253488",
        related=("nicolas-maduro", "jorge-rodriguez", "diosdado-cabello"),
        sector_path="/sectors/economic",
    ),

    "diosdado-cabello": Person(
        slug="diosdado-cabello",
        name="Diosdado Cabello",
        aliases=("Diosdado Cabello Rondón",),
        role="Minister of Interior, Justice, and Peace",
        cohorts=("executive", "military"),
        one_liner=(
            "Diosdado Cabello is Minister of Interior, Justice, and Peace and the "
            "PSUV's first vice president — long considered the second most "
            "powerful figure in the Venezuelan government."
        ),
        bio=(
            "A retired military officer who participated in the 1992 coup attempt "
            "alongside Hugo Chávez, Diosdado Cabello Rondón has occupied senior "
            "positions throughout the Bolivarian government — including National "
            "Assembly president, Vice President, and governor of Miranda. He was "
            "appointed Minister of Interior, Justice, and Peace in 2024.",
            "Cabello commands a parallel power base inside the PSUV and the armed "
            "forces, making him one of the most-watched figures by foreign "
            "compliance teams. He has been on the OFAC SDN list since 2018.",
        ),
        born="1963-04-15",
        affiliations=("PSUV",),
        timeline=(
            TimelineEntry("1992", "Participated in the February coup attempt led by Hugo Chávez"),
            TimelineEntry("2012–2016", "President of the National Assembly"),
            TimelineEntry("2024", "Appointed Minister of Interior, Justice, and Peace"),
        ),
        faqs=(
            FAQ(
                q="Who is Diosdado Cabello?",
                a="Diosdado Cabello Rondón is the Venezuelan Minister of Interior, Justice, and Peace and the first vice president of the PSUV. A retired military officer, he is widely considered the second most powerful figure in the Maduro government.",
            ),
            FAQ(
                q="Is Diosdado Cabello on the OFAC SDN list?",
                a="Yes. Cabello has been on the OFAC SDN list since 2018 under Venezuela-related sanctions programs.",
            ),
        ),
        sources=(
            Source("Wikipedia: Diosdado Cabello", "https://en.wikipedia.org/wiki/Diosdado_Cabello"),
        ),
        wikidata_id="Q1227589",
        related=("nicolas-maduro", "vladimir-padrino-lopez"),
    ),

    "jorge-rodriguez": Person(
        slug="jorge-rodriguez",
        name="Jorge Rodríguez",
        aliases=("Jorge Jesús Rodríguez Gómez",),
        role="President of the National Assembly",
        cohorts=("executive", "judiciary"),
        one_liner=(
            "Jorge Rodríguez is President of the Venezuelan National Assembly and "
            "the government's lead negotiator with the opposition and with "
            "foreign governments."
        ),
        bio=(
            "Jorge Jesús Rodríguez Gómez is a psychiatrist and longtime PSUV "
            "official, currently serving as President of the National Assembly. "
            "He previously held the Communications and Information Ministry and "
            "led the government delegation in the Barbados and Mexico City "
            "negotiations with the opposition.",
            "Rodríguez is the brother of Vice President Delcy Rodríguez and is "
            "the public face of the Maduro government's diplomatic posture, "
            "including in negotiations with the U.S. on sanctions relief.",
        ),
        affiliations=("PSUV",),
        faqs=(
            FAQ(
                q="Who is Jorge Rodríguez?",
                a="Jorge Rodríguez is the President of the Venezuelan National Assembly and the Maduro government's chief negotiator with both the opposition and foreign governments. He is the brother of Vice President Delcy Rodríguez.",
            ),
        ),
        sources=(
            Source("Wikipedia: Jorge Rodríguez", "https://en.wikipedia.org/wiki/Jorge_Rodr%C3%ADguez_(politician)"),
        ),
        related=("delcy-rodriguez", "nicolas-maduro"),
    ),

    "vladimir-padrino-lopez": Person(
        slug="vladimir-padrino-lopez",
        name="Vladimir Padrino López",
        aliases=("Vladimir Padrino", "Padrino López"),
        role="Minister of Defense and FANB Strategic Operational Commander",
        cohorts=("military", "executive"),
        one_liner=(
            "General Vladimir Padrino López is Venezuela's Minister of Defense "
            "and the longest-serving head of the FANB armed forces — the senior "
            "uniformed figure in the Maduro government."
        ),
        bio=(
            "Vladimir Padrino López has served as Minister of Defense since 2014 "
            "and as Strategic Operational Commander of the Bolivarian National "
            "Armed Forces (FANB). He commands the army, navy, air force, "
            "Bolivarian National Guard (GNB), and the militia.",
            "Padrino López is the central figure foreign investors and "
            "compliance teams watch when assessing the loyalty of the Venezuelan "
            "military to the Maduro government — historically the decisive "
            "variable in any political-transition scenario.",
        ),
        affiliations=("FANB",),
        timeline=(
            TimelineEntry("2014", "Appointed Minister of Defense"),
            TimelineEntry("2017", "Added as Strategic Operational Commander of the FANB"),
        ),
        faqs=(
            FAQ(
                q="Who is Vladimir Padrino López?",
                a="General Vladimir Padrino López is Venezuela's Minister of Defense and Strategic Operational Commander of the Bolivarian National Armed Forces (FANB). He has been the senior uniformed figure in the Maduro government since 2014.",
            ),
            FAQ(
                q="Is Padrino López sanctioned by the U.S.?",
                a="Yes. Padrino López has been on the OFAC SDN list since 2018, designated under Venezuela-related sanctions programs.",
            ),
        ),
        sources=(
            Source("Wikipedia: Vladimir Padrino López", "https://en.wikipedia.org/wiki/Vladimir_Padrino_L%C3%B3pez"),
        ),
        wikidata_id="Q3556283",
        related=("nicolas-maduro", "diosdado-cabello"),
    ),

    "arianny-seijo-noguera": Person(
        slug="arianny-seijo-noguera",
        name="Arianny Seijo Noguera",
        aliases=("Arianny Vanessa Seijo Noguera",),
        role="Attorney General of Venezuela",
        cohorts=("judiciary", "executive"),
        one_liner=(
            "Arianny Seijo Noguera is the Attorney General of Venezuela "
            "(Fiscal General de la República), heading the Public Ministry "
            "and the country's federal-prosecution apparatus."
        ),
        bio=(
            "Arianny Seijo Noguera serves as Fiscal General de la República — "
            "the Attorney General of Venezuela — leading the Public Ministry "
            "(Ministerio Público), the body responsible for criminal "
            "prosecutions and constitutional oversight of state institutions.",
            "For foreign investors and compliance teams, the Attorney General "
            "is the decisive figure on prosecutions involving foreign "
            "corporations, asset seizures, criminal exposure of executives "
            "operating in-country, and the legal framework for OFAC-licensed "
            "transactions. The Public Ministry's posture toward foreign-investor "
            "litigation is a leading indicator of country risk.",
        ),
        affiliations=("Ministerio Público",),
        faqs=(
            FAQ(
                q="Who is Arianny Seijo Noguera?",
                a="Arianny Seijo Noguera is the Attorney General of Venezuela (Fiscal General de la República), heading the Public Ministry — the body responsible for criminal prosecutions and constitutional oversight in Venezuela.",
            ),
            FAQ(
                q="What does the Venezuelan Attorney General do?",
                a="The Fiscal General leads the Ministerio Público, which prosecutes criminal cases, oversees the constitutional conduct of state institutions, and represents the state in litigation. For foreign investors, the office is the decisive authority on prosecutions involving foreign companies, asset seizures, and the criminal exposure of executives operating in Venezuela.",
            ),
            FAQ(
                q="Is Arianny Seijo Noguera sanctioned?",
                a="As of publication, Arianny Seijo Noguera is not listed on the OFAC SDN list. Compliance teams should always verify against the live OFAC Sanctions Search before relying on this for decision-making.",
            ),
        ),
        sources=(
            Source("Ministerio Público de Venezuela (official)", "https://www.mp.gob.ve/"),
        ),
        sector_path="/sectors/legal",
        related=("nicolas-maduro", "jorge-rodriguez"),
    ),

    "maria-corina-machado": Person(
        slug="maria-corina-machado",
        name="María Corina Machado",
        aliases=("María Corina Machado Parisca",),
        role="Leader of the Vente Venezuela opposition party",
        cohorts=("opposition",),
        one_liner=(
            "María Corina Machado is the leader of Vente Venezuela and the "
            "principal figure in the Venezuelan democratic opposition — winner "
            "of the 2023 opposition primary and the 2024 Nobel Peace Prize."
        ),
        bio=(
            "María Corina Machado Parisca is a Venezuelan industrial engineer "
            "and politician who founded Vente Venezuela and Súmate. She won "
            "the 2023 opposition primary with more than 90 percent of the vote "
            "but was barred by the TSJ-controlled Comptroller from running in "
            "the July 2024 presidential election.",
            "Machado backed Edmundo González as the opposition's unity "
            "candidate in 2024, and the opposition coalition has since "
            "maintained that González won the election. She was awarded the "
            "Nobel Peace Prize in 2024 for her leadership of Venezuela's "
            "democratic movement.",
        ),
        born="1967-10-07",
        birthplace="Caracas, Venezuela",
        affiliations=("Vente Venezuela", "Plataforma Unitaria"),
        timeline=(
            TimelineEntry("2002", "Co-founded the civic election-monitoring NGO Súmate"),
            TimelineEntry("2013", "Founded Vente Venezuela"),
            TimelineEntry("2023", "Won the opposition primary with over 90% of the vote"),
            TimelineEntry("2024", "Awarded the Nobel Peace Prize"),
        ),
        faqs=(
            FAQ(
                q="Who is María Corina Machado?",
                a="María Corina Machado is the leader of the Venezuelan opposition party Vente Venezuela. She won the 2023 opposition primary, was barred from the 2024 presidential ballot, backed Edmundo González as the unity candidate, and was awarded the 2024 Nobel Peace Prize for her leadership of Venezuela's democratic movement.",
            ),
            FAQ(
                q="Did María Corina Machado win the Nobel Peace Prize?",
                a="Yes. The Norwegian Nobel Committee awarded the 2024 Nobel Peace Prize to María Corina Machado for her leadership of Venezuela's pro-democracy opposition.",
            ),
        ),
        sources=(
            Source("Wikipedia: María Corina Machado", "https://en.wikipedia.org/wiki/Mar%C3%ADa_Corina_Machado"),
            Source("The Nobel Peace Prize 2024", "https://www.nobelprize.org/prizes/peace/2024/summary/"),
        ),
        wikidata_id="Q435846",
        related=("edmundo-gonzalez", "nicolas-maduro"),
    ),

    "edmundo-gonzalez": Person(
        slug="edmundo-gonzalez",
        name="Edmundo González Urrutia",
        aliases=("Edmundo González",),
        role="Opposition presidential candidate (2024)",
        cohorts=("opposition",),
        one_liner=(
            "Edmundo González Urrutia is a retired Venezuelan diplomat who ran "
            "as the unity opposition candidate in the July 2024 presidential "
            "election after María Corina Machado was barred from the ballot."
        ),
        bio=(
            "Edmundo González Urrutia is a career diplomat who served as "
            "Venezuela's ambassador to Algeria and Argentina. In 2024 he was "
            "selected by the Plataforma Unitaria opposition coalition as the "
            "unity presidential candidate after María Corina Machado was "
            "disqualified by the TSJ-controlled Comptroller.",
            "The opposition coalition publishes voting-table receipts that, "
            "they argue, show González won the July 28, 2024 election — a "
            "claim disputed by the Maduro-aligned CNE. González went into "
            "exile in Spain in September 2024.",
        ),
        born="1949-08-29",
        affiliations=("Plataforma Unitaria",),
        faqs=(
            FAQ(
                q="Who is Edmundo González Urrutia?",
                a="Edmundo González Urrutia is a retired Venezuelan diplomat who ran as the unity opposition candidate in the July 2024 presidential election. The opposition coalition maintains that he won the vote based on published precinct-level tallies; he went into exile in Spain in September 2024.",
            ),
        ),
        sources=(
            Source("Wikipedia: Edmundo González", "https://en.wikipedia.org/wiki/Edmundo_Gonz%C3%A1lez"),
        ),
        wikidata_id="Q113533432",
        related=("maria-corina-machado",),
    ),
}


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────


def get_person(slug: str) -> Optional[Person]:
    """Fetch a person profile by slug, or None if not in the registry."""
    return PEOPLE.get(slug)


def all_people() -> list[Person]:
    """Alphabetised list of all registered people — drives /people index."""
    return sorted(PEOPLE.values(), key=lambda p: p.name.lower())


def people_in_cohort(cohort: str) -> list[Person]:
    """All people whose cohorts include `cohort`, alphabetised."""
    return sorted(
        (p for p in PEOPLE.values() if cohort in p.cohorts),
        key=lambda p: p.name.lower(),
    )


def cohort_meta(cohort: str) -> Optional[dict[str, str]]:
    return COHORTS.get(cohort)


def all_cohorts() -> list[tuple[str, dict[str, str]]]:
    """Cohorts in declaration order — used by the /people hub."""
    return list(COHORTS.items())


def related_people(person: Person, *, limit: int = 6) -> list[Person]:
    """Resolve `person.related` slugs to Person objects, dropping unknowns."""
    out: list[Person] = []
    for slug in person.related:
        target = PEOPLE.get(slug)
        if target and target.slug != person.slug:
            out.append(target)
        if len(out) >= limit:
            break
    return out


def cohort_siblings(person: Person, *, limit: int = 6) -> list[Person]:
    """Other people sharing the primary cohort, alphabetised, capped."""
    cohort = person.primary_cohort
    out = [p for p in people_in_cohort(cohort) if p.slug != person.slug]
    return out[:limit]


# ──────────────────────────────────────────────────────────────────────
# Slug helper (re-exported for any future ingest pipeline)
# ──────────────────────────────────────────────────────────────────────


_SLUG_RX = re.compile(r"[^a-z0-9]+")


def slugify_name(name: str) -> str:
    """Deterministic slug from a person's name. Stable across runs."""
    norm = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return _SLUG_RX.sub("-", norm.lower()).strip("-")
