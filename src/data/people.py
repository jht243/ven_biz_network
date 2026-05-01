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


# Editorial verification stamp. Every profile was checked against
# news coverage on or before this date. Surfaced on every page so the
# reader can see how recent the data is — and we update it whenever
# we re-sweep the registry. Bump this when you re-verify.
VERIFIED_AS_OF = "2026-04-30"


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


# Status taxonomy. The badge rendered at the top of each profile is
# driven by this enum-like string so the visual treatment is uniform
# across the registry, and so we can audit at a glance how many
# profiles are stale-by-default ("former") vs operational ("current").
#
# Values:
#   "current"             — currently holds the named role; no badge
#   "former"              — was in role; recently removed/resigned
#   "in_us_custody"       — in U.S. federal detention
#   "in_ven_custody"      — detained by Venezuelan authorities
#   "in_exile"            — living abroad, not in custody
#
# Templates map each value to the badge text and color (see
# templates/people/profile.html.j2 — the mapping is intentionally
# defined in the template, not the data, so editorial copy changes
# don't require a code edit).
STATUS_VALUES: tuple[str, ...] = (
    "current", "former", "in_us_custody", "in_ven_custody", "in_exile",
)


@dataclass(frozen=True)
class Person:
    slug: str
    name: str                          # canonical display name
    role: str                          # short role label, e.g. "Attorney General of Venezuela"
    cohorts: tuple[str, ...]           # one or more keys from COHORTS

    # Search-snippet copy
    one_liner: str                     # one-sentence "who is this" — feeds meta description
    bio: tuple[str, ...]               # 2–4 paragraph long-form bio (rendered as <p>s)

    status: str = "current"            # one of STATUS_VALUES — drives the profile-page badge
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
        role="Former President of Venezuela (in U.S. federal custody)",
        cohorts=("executive",),
        status="in_us_custody",
        one_liner=(
            "Nicolás Maduro is the former President of Venezuela. He was "
            "captured by U.S. military forces in Caracas on January 3, "
            "2026 and is currently in U.S. federal custody, charged in the "
            "Southern District of New York with narco-terrorism conspiracy "
            "and related counts."
        ),
        bio=(
            "Nicolás Maduro Moros served as President of Venezuela from "
            "April 2013 — when he assumed the office following the death "
            "of Hugo Chávez — until January 3, 2026, when U.S. special "
            "operations forces captured him and his wife Cilia Flores in "
            "Caracas as part of a wider military operation called "
            "Operation Resolve. Both were transported to the United States "
            "and indicted in the Southern District of New York. Maduro was "
            "charged with narco-terrorism conspiracy, cocaine importation "
            "conspiracy, and weapons offences against the United States.",
            "Inside Venezuela, Vice President Delcy Rodríguez was sworn in "
            "as Acting President on January 5, 2026 under constitutional "
            "succession. The Venezuelan government and the National "
            "Assembly continue to assert that Maduro is the de jure "
            "president of Venezuela; the United States and most of its "
            "allies treat him as removed from power. The legitimacy of "
            "the U.S. capture remains contested under international law.",
            "For foreign-investor and compliance teams, Maduro's "
            "incarceration is the most significant change to the "
            "Venezuelan political risk picture in over a decade. PDVSA "
            "joint ventures, OFAC general licenses, sovereign-debt "
            "trajectory, and the entire post-2017 sanctions architecture "
            "were all built around his administration; the Rodríguez-led "
            "transition is the new counterparty.",
        ),
        born="1962-11-23",
        birthplace="Caracas, Venezuela",
        affiliations=("PSUV", "GPP coalition"),
        timeline=(
            TimelineEntry("2006–2013", "Foreign Minister of Venezuela"),
            TimelineEntry("2012–2013", "Executive Vice President of Venezuela"),
            TimelineEntry("2013", "Assumed the presidency after the death of Hugo Chávez"),
            TimelineEntry("2017", "Added to the OFAC SDN list under Venezuela-related sanctions"),
            TimelineEntry("2018", "Re-elected in an election widely rejected by the U.S., EU, and most Latin American governments"),
            TimelineEntry("2024", "Declared winner of the July 28 presidential election; results disputed by the opposition and rejected by the U.S."),
            TimelineEntry("2026", "Captured by U.S. military forces in Caracas on January 3 (Operation Resolve); arraigned in U.S. federal court in New York"),
        ),
        faqs=(
            FAQ(
                q="Where is Nicolás Maduro now?",
                a="Nicolás Maduro is in U.S. federal custody. He was captured by U.S. special operations forces in Caracas on January 3, 2026 and was arraigned in the Southern District of New York on charges including narco-terrorism conspiracy, cocaine importation conspiracy, and weapons offences against the United States.",
            ),
            FAQ(
                q="Is Maduro still the President of Venezuela?",
                a="No, in practical terms. Vice President Delcy Rodríguez was sworn in as Acting President on January 5, 2026 and continues to lead the Venezuelan government. The Venezuelan state and the National Assembly continue to assert that Maduro is the de jure president; the United States, the EU, and most Latin American governments treat him as removed from power.",
            ),
            FAQ(
                q="Why does Maduro's capture matter to foreign investors?",
                a="The post-2017 Venezuelan sanctions architecture, OFAC general licenses, PDVSA joint-venture frameworks, and sovereign-debt restructuring conversations were all anchored on the Maduro administration. His removal restarts the political-risk calculation: foreign-investor counterparties now negotiate with the Rodríguez-led transition government, not the Maduro circle.",
            ),
        ),
        sources=(
            Source("Wikipedia: Nicolás Maduro", "https://en.wikipedia.org/wiki/Nicol%C3%A1s_Maduro"),
            Source("U.S. State Department: Nicolás Maduro Moros (Captured)", "https://www.state.gov/nicolas-maduro-moros"),
            Source("U.S. Department of War — capture announcement", "https://www.war.gov/News/News-Stories/Article/Article/4370431/trump-announces-us-militarys-capture-of-maduro/"),
            Source("Wikipedia: Prosecution of Nicolás Maduro and Cilia Flores", "https://en.wikipedia.org/wiki/Prosecution_of_Nicol%C3%A1s_Maduro_and_Cilia_Flores"),
        ),
        wikidata_id="Q333271",
        related=("cilia-flores", "delcy-rodriguez", "diosdado-cabello", "maria-corina-machado", "edmundo-gonzalez"),
    ),

    "delcy-rodriguez": Person(
        slug="delcy-rodriguez",
        name="Delcy Rodríguez",
        aliases=("Delcy Eloína Rodríguez Gómez", "Delcy Rodríguez Gómez"),
        role="Acting President of Venezuela",
        cohorts=("executive", "energy"),
        status="current",
        one_liner=(
            "Delcy Rodríguez is the Acting President of Venezuela, sworn "
            "in on January 5, 2026 after the U.S. military's capture of "
            "Nicolás Maduro. She is the senior figure foreign-investor and "
            "compliance teams now negotiate with on every Venezuelan "
            "counterparty question."
        ),
        bio=(
            "Delcy Eloína Rodríguez Gómez served as Executive Vice "
            "President of Venezuela from June 2018 and concurrently as "
            "Minister of Economy, Finance, and Foreign Trade. After the "
            "U.S. military captured Nicolás Maduro on January 3, 2026, "
            "she was sworn in as Acting President on January 5 under "
            "constitutional succession rules. Although the National "
            "Assembly's 90-day cap on her interim role expired in early "
            "April, she has remained in office as the Venezuelan state "
            "and the United States negotiate the country's political "
            "transition.",
            "Since taking the presidency, Rodríguez has overseen a major "
            "cabinet overhaul — replacing roughly half of Maduro's "
            "ministers, dismissing Defense Minister Vladimir Padrino "
            "López in March 2026, and elevating allies into the "
            "intelligence and military commands. She also concurrently "
            "holds the Oil Ministry portfolio and leads the government's "
            "engagement with the Trump administration on the post-Maduro "
            "transition timeline.",
            "Rodríguez was added to the OFAC SDN list in 2018 under "
            "Venezuela-related sanctions programs. The U.S. Treasury "
            "delisted her in 2026 as part of the post-capture political "
            "settlement; other senior officials in her government "
            "remain sanctioned.",
        ),
        born="1969-05-18",
        birthplace="Caracas, Venezuela",
        in_office_since="2026-01-05",
        affiliations=("PSUV",),
        timeline=(
            TimelineEntry("2014–2017", "Foreign Minister of Venezuela"),
            TimelineEntry("2017–2018", "President of the National Constituent Assembly"),
            TimelineEntry("2018–2026", "Executive Vice President of Venezuela"),
            TimelineEntry("2018", "Added to the OFAC SDN list"),
            TimelineEntry("2020–2026", "Concurrently Minister of Economy, Finance, and Foreign Trade"),
            TimelineEntry("2026", "Sworn in as Acting President of Venezuela on January 5 following the U.S. capture of Nicolás Maduro"),
            TimelineEntry("2026", "Removed from the OFAC SDN list as part of the post-capture political settlement"),
            TimelineEntry("2026", "Dismissed Defense Minister Vladimir Padrino López on March 18 and replaced him with Gustavo González López"),
        ),
        faqs=(
            FAQ(
                q="Who is Delcy Rodríguez?",
                a="Delcy Rodríguez is the Acting President of Venezuela, sworn in on January 5, 2026 following the U.S. military's capture of Nicolás Maduro on January 3. She previously served as Executive Vice President from 2018 and as Minister of Economy, Finance, and Foreign Trade from 2020.",
            ),
            FAQ(
                q="Is Delcy Rodríguez sanctioned by the U.S.?",
                a="No, not currently. Rodríguez was on the OFAC SDN list from 2018 under Venezuela-related sanctions programs but was removed in 2026 as part of the political settlement that followed the U.S. capture of Nicolás Maduro. Other senior officials in her government remain on the SDN list.",
            ),
            FAQ(
                q="Is she really the president, or just acting?",
                a="Rodríguez was sworn in as Acting President under Venezuelan constitutional succession rules. The TSJ-mandated 90-day cap on her interim role expired in early April 2026 without elections. As of late April 2026 she remains in office, and is the de facto head of the Venezuelan government recognized by the United States as the negotiation counterparty.",
            ),
        ),
        sources=(
            Source("Wikipedia: Delcy Rodríguez", "https://en.wikipedia.org/wiki/Delcy_Rodr%C3%ADguez"),
            Source("Britannica — Delcy Rodríguez, Acting President of Venezuela", "https://www.britannica.com/biography/Who-Is-Delcy-Rodriguez-the-Acting-President-of-Venezuela"),
            Source("PBS NewsHour — Who is Delcy Rodríguez", "https://www.pbs.org/newshour/world/who-is-delcy-rodriguez-venezuelas-interim-president-after-maduros-ouster"),
        ),
        wikidata_id="Q5253488",
        related=("nicolas-maduro", "jorge-rodriguez", "diosdado-cabello", "gustavo-gonzalez-lopez", "arianny-seijo-noguera"),
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
        role="Former Defense Minister of Venezuela (until March 2026)",
        cohorts=("military", "executive"),
        status="former",
        one_liner=(
            "General Vladimir Padrino López served as Venezuela's Defense "
            "Minister for over 11 years until being dismissed by Acting "
            "President Delcy Rodríguez on March 18, 2026 — the longest "
            "tenure of any modern Venezuelan defense chief."
        ),
        bio=(
            "Vladimir Padrino López served as Minister of Defense from "
            "October 2014 to March 18, 2026, and as Strategic Operational "
            "Commander of the Bolivarian National Armed Forces (FANB) "
            "from 2017. He commanded the army, navy, air force, "
            "Bolivarian National Guard (GNB), and the militia.",
            "Padrino López was dismissed in March 2026 by Acting President "
            "Delcy Rodríguez and replaced by General Gustavo González "
            "López, the former military-counterintelligence chief and a "
            "Rodríguez ally. The dismissal is widely read as the most "
            "important post-Maduro cabinet move so far — Padrino had been "
            "the senior uniformed figure underpinning the previous "
            "government, and his removal signaled the consolidation of "
            "the Rodríguez-led transition.",
            "Padrino López remains on the OFAC SDN list under "
            "Venezuela-related programs.",
        ),
        affiliations=("FANB",),
        timeline=(
            TimelineEntry("2014", "Appointed Minister of Defense"),
            TimelineEntry("2017", "Added as Strategic Operational Commander of the FANB"),
            TimelineEntry("2018", "Added to the OFAC SDN list"),
            TimelineEntry("2026", "Dismissed as Defense Minister on March 18 by Acting President Delcy Rodríguez; replaced by Gustavo González López"),
        ),
        faqs=(
            FAQ(
                q="Who is Vladimir Padrino López?",
                a="General Vladimir Padrino López is the former Defense Minister of Venezuela. He held the role from October 2014 until March 18, 2026 — the longest tenure of any modern Venezuelan defense chief — and was replaced by General Gustavo González López after the U.S. capture of Nicolás Maduro and the subsequent cabinet overhaul under Acting President Delcy Rodríguez.",
            ),
            FAQ(
                q="Is Padrino López sanctioned by the U.S.?",
                a="Yes. Padrino López has been on the OFAC SDN list since 2018 under Venezuela-related sanctions programs and remains on the list as of April 2026.",
            ),
        ),
        sources=(
            Source("Wikipedia: Vladimir Padrino López", "https://en.wikipedia.org/wiki/Vladimir_Padrino_L%C3%B3pez"),
            Source("Al Jazeera — Delcy Rodríguez replaces Padrino", "https://www.aljazeera.com/news/2026/3/18/delcy-rodriguez-replaces-venezuelas-defence-minister-vladimir-padrino"),
        ),
        wikidata_id="Q3556283",
        related=("nicolas-maduro", "diosdado-cabello", "gustavo-gonzalez-lopez", "delcy-rodriguez"),
    ),

    "arianny-seijo-noguera": Person(
        slug="arianny-seijo-noguera",
        name="Arianny Seijo Noguera",
        aliases=("Arianny Viviana Seijo Noguera", "Arianny Vanessa Seijo Noguera"),
        role="Attorney General of Venezuela (Procuradora General)",
        cohorts=("judiciary", "executive"),
        status="current",
        in_office_since="2026-03-24",
        one_liner=(
            "Arianny Seijo Noguera is the Attorney General of Venezuela "
            "(Procuradora General de la República), the senior state "
            "lawyer representing the Venezuelan state in civil and "
            "commercial litigation. She was appointed by Acting President "
            "Delcy Rodríguez and confirmed by the National Assembly on "
            "March 24, 2026."
        ),
        bio=(
            "Arianny Seijo Noguera was appointed Procuradora General de "
            "la República — the head of Venezuela's State Solicitor "
            "office, translated as Attorney General — by Acting President "
            "Delcy Rodríguez and confirmed by the National Assembly on "
            "March 24, 2026. She replaced Reinaldo Muñoz Pedroza, who "
            "held the role for nearly a decade.",
            "Before her appointment, Seijo Noguera served as legal "
            "counsel for Petróleos de Venezuela (PDVSA) and participated "
            "in the drafting of the Amnesty Law approved by the National "
            "Assembly in February 2026. She holds a doctorate in law from "
            "the University of Westminster and two master's degrees from "
            "U.K. universities.",
            "The Procuraduría General is distinct from the Ministerio "
            "Público (headed by the Fiscal General — currently Larry "
            "Devoe). The Procuradora represents the Venezuelan state in "
            "civil, commercial, and arbitration matters — directly "
            "relevant to foreign-investor disputes including ICSID "
            "arbitration, the Crystallex / CITGO writ-of-execution "
            "proceedings, and PDVSA-bond restructuring conversations. "
            "Seijo Noguera's PDVSA background makes her appointment "
            "particularly significant for the oil-sector compliance "
            "framework.",
        ),
        affiliations=("Procuraduría General de la República",),
        timeline=(
            TimelineEntry("2026", "Confirmed by the National Assembly as Procuradora General de la República on March 24, replacing Reinaldo Muñoz Pedroza"),
        ),
        faqs=(
            FAQ(
                q="Who is Arianny Seijo Noguera?",
                a="Arianny Seijo Noguera is the Attorney General of Venezuela (Procuradora General de la República). She was appointed by Acting President Delcy Rodríguez and confirmed by the National Assembly on March 24, 2026, replacing Reinaldo Muñoz Pedroza. Before her appointment she was legal counsel for PDVSA.",
            ),
            FAQ(
                q="What does the Procuradora General do?",
                a="The Procurador General de la República heads Venezuela's State Solicitor office. The office represents the Venezuelan state in civil, commercial, and arbitration disputes — including international arbitration cases brought by foreign investors, the Crystallex / CITGO litigation in U.S. courts, and PDVSA-bond restructuring negotiations. It is distinct from the Fiscalía (Public Ministry), which handles criminal prosecutions.",
            ),
            FAQ(
                q="Is the Procuradora the same as the Fiscal General?",
                a="No. Both are translated as 'Attorney General' in English but are separate offices. The Procurador General de la República (currently Arianny Seijo Noguera) represents the state in civil and commercial litigation. The Fiscal General de la República (currently Larry Devoe) heads the Public Ministry and prosecutes criminal cases.",
            ),
            FAQ(
                q="Is Arianny Seijo Noguera sanctioned?",
                a="As of April 2026, Arianny Seijo Noguera is not listed on the OFAC SDN list. Compliance teams should always verify against the live OFAC Sanctions Search before relying on this for decision-making.",
            ),
        ),
        sources=(
            Source("Asamblea Nacional approves Seijo Noguera as Procuradora General — Últimas Noticias", "https://en.ultimasnoticias.com.ve/politica/an-aprobo-designacion-de-arianny-seijo-noguera-como-procuradora-general/"),
            Source("MIPPCI — Designación de Arianny Seijo Noguera", "https://mippci.gob.ve/an-autoriza-designacion-de-arianny-seijo-noguera-como-nueva-procuradora-general-de-la-republica/"),
            Source("Caracas Research — Venezuela's New Attorney General: Implications for Legal and Energy Sectors", "https://caracasresearch.com/briefing/venezuela-s-new-attorney-general-implications-for-legal-and-energy-sectors-20260324-315"),
        ),
        sector_path="/sectors/legal",
        related=("delcy-rodriguez", "larry-devoe", "hector-obregon", "tarek-william-saab"),
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
        role="Opposition presidential candidate (2024; in exile)",
        cohorts=("opposition",),
        status="in_exile",
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

    # ── Executive / cabinet ──────────────────────────────────────────

    "yvan-gil": Person(
        slug="yvan-gil",
        name="Yván Gil",
        aliases=("Yván Gil Pinto",),
        role="Foreign Minister of Venezuela",
        cohorts=("executive",),
        one_liner=(
            "Yván Gil is Venezuela's Foreign Minister, the senior diplomat "
            "managing the Maduro government's external relations including "
            "negotiations with the U.S., the EU, and Latin American "
            "neighbours over sanctions and recognition."
        ),
        bio=(
            "Yván Gil Pinto serves as Minister of People's Power for Foreign "
            "Affairs of Venezuela. Before taking the foreign-affairs portfolio "
            "he served as Minister of Agriculture and Land. He is the public "
            "face of the Maduro government in international forums, including "
            "the UN, ALBA-TCP, and CELAC.",
            "For foreign-investor and compliance teams, the Foreign Ministry "
            "is the principal Venezuelan channel for diplomatic-protocol "
            "matters affecting business operations — visa coordination, "
            "embassy access, and the formal counterpart for foreign-state "
            "engagement on commercial and consular questions.",
        ),
        affiliations=("PSUV",),
        faqs=(
            FAQ(
                q="Who is Yván Gil?",
                a="Yván Gil is the Foreign Minister of Venezuela, the senior diplomat representing the Maduro government in international relations. He previously served as Minister of Agriculture and Land.",
            ),
        ),
        sources=(
            Source("Ministerio del Poder Popular para Relaciones Exteriores", "https://mppre.gob.ve/"),
        ),
        sector_path="/sectors/diplomatic",
        related=("nicolas-maduro", "delcy-rodriguez"),
    ),

    "tareck-el-aissami": Person(
        slug="tareck-el-aissami",
        name="Tareck El Aissami",
        aliases=("Tareck Zaidan El Aissami Maddah",),
        role="Former Vice President and Oil Minister of Venezuela (in Venezuelan custody)",
        cohorts=("executive", "energy"),
        status="in_ven_custody",
        one_liner=(
            "Tareck El Aissami is a former Executive Vice President and "
            "former Oil Minister of Venezuela — long one of the most "
            "powerful figures in the Maduro government before being arrested "
            "in 2024 on corruption charges."
        ),
        bio=(
            "Tareck Zaidan El Aissami Maddah served as Executive Vice "
            "President of Venezuela (2017–2018), Minister of Industries and "
            "National Production, and Minister of Petroleum (2020–2023). He "
            "was added to the OFAC SDN list in 2017, with the U.S. Treasury "
            "calling him a 'prominent Venezuelan drug trafficker' in the "
            "designation announcement.",
            "El Aissami resigned the Oil Ministry in March 2023 amid a major "
            "PDVSA corruption probe and was arrested by Venezuelan authorities "
            "in April 2024 on charges including treason and money laundering. "
            "His arrest is one of the most consequential internal "
            "Bolivarian-government rifts since the death of Hugo Chávez.",
        ),
        born="1974-11-12",
        affiliations=("PSUV",),
        timeline=(
            TimelineEntry("2017", "Added to the OFAC SDN list under Venezuela-related sanctions"),
            TimelineEntry("2017–2018", "Executive Vice President of Venezuela"),
            TimelineEntry("2020–2023", "Minister of Petroleum and President of PDVSA's holding board"),
            TimelineEntry("2023", "Resigned the Oil Ministry amid a major PDVSA corruption probe"),
            TimelineEntry("2024", "Arrested by Venezuelan authorities on treason and money-laundering charges"),
        ),
        faqs=(
            FAQ(
                q="Who is Tareck El Aissami?",
                a="Tareck El Aissami is a former Executive Vice President of Venezuela (2017–2018) and former Oil Minister (2020–2023). He has been on the OFAC SDN list since 2017 and was arrested in 2024 by Venezuelan authorities on treason and money-laundering charges connected to a major PDVSA corruption investigation.",
            ),
            FAQ(
                q="Why is Tareck El Aissami significant for sanctions and oil-sector compliance?",
                a="As Oil Minister and de-facto head of PDVSA's holding board from 2020–2023, El Aissami was the central counterparty for foreign oil-sector negotiations with Venezuela during the OFAC general-license window. His subsequent arrest signaled a significant internal rupture and reshaped the PDVSA leadership team.",
            ),
        ),
        sources=(
            Source("Wikipedia: Tareck El Aissami", "https://en.wikipedia.org/wiki/Tareck_El_Aissami"),
            Source("US Treasury OFAC press release (2017)", "https://home.treasury.gov/news/press-releases/sm0021"),
        ),
        wikidata_id="Q1717693",
        sector_path="/sectors/oil-gas",
        related=("nicolas-maduro", "rafael-ramirez", "pedro-tellechea"),
    ),

    "cilia-flores": Person(
        slug="cilia-flores",
        name="Cilia Flores",
        aliases=("Cilia Adela Flores de Maduro",),
        role="Former First Lady of Venezuela (in U.S. federal custody)",
        cohorts=("executive",),
        status="in_us_custody",
        one_liner=(
            "Cilia Flores is the wife of former president Nicolás Maduro "
            "and a longtime PSUV leader who was captured alongside him by "
            "U.S. military forces on January 3, 2026. She is currently in "
            "U.S. federal custody and has been indicted in the Southern "
            "District of New York."
        ),
        bio=(
            "Cilia Adela Flores de Maduro is a Venezuelan lawyer and "
            "politician who served as Attorney General of Venezuela "
            "(2006–2007), as President of the National Assembly "
            "(2006–2011), and as a member of the National Assembly "
            "thereafter. She has been a senior PSUV figure throughout the "
            "Bolivarian government and was widely viewed as a principal "
            "political adviser to her husband, Nicolás Maduro.",
            "Flores was captured alongside Maduro in Caracas on January "
            "3, 2026 during the U.S. military's Operation Resolve, "
            "transported to the United States, and arraigned in the "
            "Southern District of New York. She has pleaded not guilty "
            "to the charges.",
        ),
        affiliations=("PSUV",),
        timeline=(
            TimelineEntry("2006–2007", "Attorney General of Venezuela"),
            TimelineEntry("2006–2011", "President of the National Assembly"),
            TimelineEntry("2026", "Captured by U.S. military forces in Caracas on January 3 alongside Nicolás Maduro; indicted in the Southern District of New York"),
        ),
        faqs=(
            FAQ(
                q="Who is Cilia Flores?",
                a="Cilia Flores is a Venezuelan lawyer, a longtime PSUV leader, the wife of former President Nicolás Maduro, and a former Attorney General of Venezuela (2006–2007) and former President of the National Assembly (2006–2011). She was captured by U.S. military forces in Caracas on January 3, 2026 and is currently in U.S. federal custody.",
            ),
            FAQ(
                q="Is Cilia Flores in U.S. custody?",
                a="Yes. Flores was captured alongside Nicolás Maduro by U.S. special operations forces in Caracas on January 3, 2026, transported to the United States, and arraigned in the Southern District of New York. She has pleaded not guilty.",
            ),
        ),
        sources=(
            Source("Wikipedia: Cilia Flores", "https://en.wikipedia.org/wiki/Cilia_Flores"),
            Source("Wikipedia: Prosecution of Nicolás Maduro and Cilia Flores", "https://en.wikipedia.org/wiki/Prosecution_of_Nicol%C3%A1s_Maduro_and_Cilia_Flores"),
        ),
        wikidata_id="Q3680898",
        related=("nicolas-maduro",),
    ),

    "freddy-bernal": Person(
        slug="freddy-bernal",
        name="Freddy Bernal",
        aliases=("Freddy Alirio Bernal Rosales",),
        role="Governor of Táchira state",
        cohorts=("executive",),
        one_liner=(
            "Freddy Bernal is the governor of Táchira — Venezuela's main "
            "border state with Colombia — and a longtime senior PSUV "
            "official with a security and intelligence background."
        ),
        bio=(
            "Freddy Alirio Bernal Rosales is a former Caracas mayor "
            "(Libertador municipality, 2000–2008) and a longtime senior "
            "PSUV operator with a background in the police and intelligence "
            "services. He has been governor of Táchira state, on the "
            "Colombian border, where his portfolio touches smuggling, "
            "border security, and the bilateral relationship with Bogotá.",
            "Bernal has been on the OFAC SDN list since 2018. The Táchira "
            "governorship is consequential for foreign-investor and "
            "compliance teams because it sits at the centre of the "
            "Venezuela-Colombia trade corridor and the informal-economy "
            "flows that move across it.",
        ),
        affiliations=("PSUV",),
        faqs=(
            FAQ(
                q="Who is Freddy Bernal?",
                a="Freddy Bernal is the governor of Táchira state in Venezuela, on the Colombian border. He is a longtime PSUV official, a former Caracas mayor, and has been on the OFAC SDN list since 2018.",
            ),
        ),
        sources=(
            Source("Wikipedia: Freddy Bernal", "https://en.wikipedia.org/wiki/Freddy_Bernal"),
        ),
        wikidata_id="Q3087089",
        related=("nicolas-maduro", "diosdado-cabello"),
    ),

    "hector-rodriguez": Person(
        slug="hector-rodriguez",
        name="Héctor Rodríguez",
        aliases=("Héctor Rodríguez Castro",),
        role="Governor of Miranda state",
        cohorts=("executive",),
        one_liner=(
            "Héctor Rodríguez is the governor of Miranda state — which "
            "includes much of the Caracas metropolitan area — and a former "
            "Education Minister widely seen as one of the PSUV's "
            "next-generation leaders."
        ),
        bio=(
            "Héctor Rodríguez Castro is a Venezuelan politician who served "
            "as Minister of Education (2014–2017) before being elected "
            "governor of Miranda in 2017. Miranda includes most of the "
            "eastern Caracas metropolitan area, making the governorship one "
            "of the most politically and economically consequential in the "
            "country.",
        ),
        affiliations=("PSUV",),
        faqs=(
            FAQ(
                q="Who is Héctor Rodríguez?",
                a="Héctor Rodríguez is the governor of Miranda state in Venezuela and a former Minister of Education (2014–2017). Miranda includes most of the eastern Caracas metropolitan area, making the governorship one of the most consequential political positions outside the federal cabinet.",
            ),
        ),
        sources=(
            Source("Wikipedia: Héctor Rodríguez Castro", "https://en.wikipedia.org/wiki/H%C3%A9ctor_Rodr%C3%ADguez_Castro"),
        ),
        related=("nicolas-maduro", "diosdado-cabello"),
    ),

    "jorge-arreaza": Person(
        slug="jorge-arreaza",
        name="Jorge Arreaza",
        aliases=("Jorge Alberto Arreaza Montserrat",),
        role="Former Foreign Minister and former Vice President of Venezuela",
        cohorts=("executive",),
        status="former",
        one_liner=(
            "Jorge Arreaza is a former Foreign Minister, former Vice "
            "President, and son-in-law of Hugo Chávez — one of the longest-"
            "serving senior figures in the Bolivarian government."
        ),
        bio=(
            "Jorge Alberto Arreaza Montserrat served as Executive Vice "
            "President of Venezuela (2013–2016) and as Foreign Minister "
            "(2017–2021). He is married to Rosa Virginia Chávez, the eldest "
            "daughter of Hugo Chávez, giving him an unusual position of "
            "continuity between the Chávez and Maduro eras.",
        ),
        born="1973-06-06",
        affiliations=("PSUV",),
        faqs=(
            FAQ(
                q="Who is Jorge Arreaza?",
                a="Jorge Arreaza is a former Foreign Minister of Venezuela (2017–2021) and former Vice President (2013–2016). He is married to Rosa Virginia Chávez, the eldest daughter of Hugo Chávez, and has been one of the longest-serving senior figures in the Bolivarian government.",
            ),
        ),
        sources=(
            Source("Wikipedia: Jorge Arreaza", "https://en.wikipedia.org/wiki/Jorge_Arreaza"),
        ),
        wikidata_id="Q3499942",
        related=("nicolas-maduro", "delcy-rodriguez"),
    ),

    # ── PDVSA & energy ───────────────────────────────────────────────

    "rafael-ramirez": Person(
        slug="rafael-ramirez",
        name="Rafael Ramírez",
        aliases=("Rafael Darío Ramírez Carreño",),
        role="Former PDVSA President and Oil Minister (in exile)",
        cohorts=("energy", "executive"),
        status="in_exile",
        one_liner=(
            "Rafael Ramírez led PDVSA and the Venezuelan oil sector for 14 "
            "years (2002–2014) — the longest run of any modern PDVSA "
            "president — and is now a prominent dissident living in exile."
        ),
        bio=(
            "Rafael Darío Ramírez Carreño was President of PDVSA and "
            "Minister of Petroleum and Mining of Venezuela from 2002 to "
            "2014, the period during which Hugo Chávez restructured PDVSA "
            "after the 2002–2003 oil-industry strike. Under Ramírez, PDVSA "
            "became the central instrument of Bolivarian fiscal and foreign "
            "policy, including the founding of joint-venture vehicles with "
            "foreign majors.",
            "Ramírez was Venezuela's ambassador to the UN (2014–2017) before "
            "breaking publicly with the Maduro government and going into "
            "exile in Italy. He has since become one of the most "
            "well-informed dissident voices on PDVSA's internal "
            "decision-making — a useful primary commentator for foreign "
            "investors trying to read the oil-sector politics.",
        ),
        born="1962-12-22",
        timeline=(
            TimelineEntry("2002–2014", "President of PDVSA and Minister of Petroleum and Mining"),
            TimelineEntry("2014–2017", "Permanent Representative of Venezuela to the UN"),
            TimelineEntry("2017", "Broke publicly with the Maduro government and went into exile"),
        ),
        faqs=(
            FAQ(
                q="Who is Rafael Ramírez?",
                a="Rafael Ramírez is a former President of PDVSA and former Minister of Petroleum and Mining of Venezuela (2002–2014), the longest tenure of any modern PDVSA chief. He has lived in exile since 2017 and has become one of the most prominent dissident voices on PDVSA's internal decision-making.",
            ),
            FAQ(
                q="Why does Rafael Ramírez matter to investors today?",
                a="Although out of office for over a decade, Ramírez retains deep first-hand knowledge of PDVSA's joint-venture architecture, debt-issuance history, and operational state. His public commentary in exile is among the more substantive primary-source readings of how PDVSA actually works internally.",
            ),
        ),
        sources=(
            Source("Wikipedia: Rafael Ramírez", "https://en.wikipedia.org/wiki/Rafael_Ram%C3%ADrez_(politician)"),
        ),
        wikidata_id="Q1366090",
        sector_path="/sectors/oil-gas",
        related=("tareck-el-aissami", "pedro-tellechea", "nicolas-maduro"),
    ),

    "pedro-tellechea": Person(
        slug="pedro-tellechea",
        name="Pedro Tellechea",
        aliases=("Pedro Rafael Tellechea Ruiz",),
        role="Former PDVSA President and Oil Minister",
        cohorts=("energy",),
        status="former",
        one_liner=(
            "Pedro Tellechea is a former President of PDVSA and Oil "
            "Minister of Venezuela — the executive who briefly led the "
            "Venezuelan oil sector during the OFAC General License 44 "
            "window before being removed in 2024."
        ),
        bio=(
            "Pedro Rafael Tellechea Ruiz, an industrial engineer and "
            "longtime executive at petrochemical conglomerate Pequiven, "
            "was appointed President of PDVSA in January 2023 after the "
            "departure of Asdrúbal Chávez and was given the Petroleum "
            "Ministry portfolio in March 2023 after Tareck El Aissami's "
            "resignation. His tenure coincided with the U.S. Treasury's "
            "issuance of OFAC General License 44, which briefly authorized "
            "previously-prohibited oil-sector transactions with PDVSA.",
            "Tellechea was removed from both roles in 2024 amid the "
            "ongoing PDVSA corruption probe and the expiry of GL 44. His "
            "trajectory is the canonical case study for foreign investors "
            "tracking how political volatility inside PDVSA's leadership "
            "interacts with the U.S. sanctions regime.",
        ),
        timeline=(
            TimelineEntry("2023", "Appointed President of PDVSA (January) and Oil Minister (March)"),
            TimelineEntry("2023", "OFAC issued General License 44 authorizing certain oil-sector transactions"),
            TimelineEntry("2024", "Removed from both PDVSA and the Oil Ministry"),
        ),
        faqs=(
            FAQ(
                q="Who is Pedro Tellechea?",
                a="Pedro Tellechea is a former President of PDVSA (2023–2024) and former Minister of Petroleum (2023–2024). He led the Venezuelan oil sector during the OFAC General License 44 window before being removed in 2024.",
            ),
        ),
        sources=(
            Source("Wikipedia: Pedro Tellechea", "https://en.wikipedia.org/wiki/Pedro_Tellechea"),
        ),
        sector_path="/sectors/oil-gas",
        related=("rafael-ramirez", "tareck-el-aissami", "nicolas-maduro"),
    ),

    "asdrubal-chavez": Person(
        slug="asdrubal-chavez",
        name="Asdrúbal Chávez",
        aliases=("Asdrúbal José Chávez Jiménez",),
        role="Former PDVSA and Citgo President",
        cohorts=("energy",),
        status="former",
        one_liner=(
            "Asdrúbal Chávez is a Venezuelan oil executive and a cousin "
            "of Hugo Chávez who served as president of CITGO and later "
            "as president of PDVSA."
        ),
        bio=(
            "Asdrúbal José Chávez Jiménez is a chemical engineer who spent "
            "his career inside PDVSA before being appointed CEO of CITGO "
            "Petroleum, PDVSA's U.S. refining subsidiary, in 2014. He has "
            "subsequently held senior PDVSA leadership roles. He is a "
            "first cousin of Hugo Chávez.",
            "CITGO is the central asset in any analysis of Venezuela's "
            "U.S.-jurisdiction footprint: it is the ultimate collateral "
            "behind PDVSA 2020 bonds and the contested asset in the "
            "Crystallex-led writ-of-execution proceedings in Delaware. "
            "Asdrúbal Chávez's tenure spans the period in which CITGO's "
            "control passed effectively to U.S. courts.",
        ),
        sources=(
            Source("Wikipedia: Asdrúbal Chávez", "https://en.wikipedia.org/wiki/Asdr%C3%BAbal_Ch%C3%A1vez"),
        ),
        sector_path="/sectors/oil-gas",
        related=("rafael-ramirez", "pedro-tellechea"),
    ),

    "manuel-quevedo": Person(
        slug="manuel-quevedo",
        name="Manuel Quevedo",
        aliases=("Manuel Salvador Quevedo Fernández",),
        role="Former PDVSA President and Oil Minister",
        cohorts=("energy", "military"),
        status="former",
        one_liner=(
            "Manuel Quevedo is a retired Venezuelan National Guard "
            "general who served as President of PDVSA and Oil Minister "
            "from 2017 to 2020, during a steep collapse in oil output."
        ),
        bio=(
            "Manuel Salvador Quevedo Fernández is a retired Bolivarian "
            "National Guard (GNB) major general who was appointed "
            "President of PDVSA and Minister of Petroleum by Nicolás "
            "Maduro in November 2017 — the first time PDVSA was led by an "
            "active military officer with no oil-industry background. "
            "During his tenure, Venezuelan oil output fell from "
            "approximately 1.9 million barrels per day to under 700,000 "
            "bpd, the steepest non-conflict-driven production collapse in "
            "modern oil history.",
            "Quevedo was removed from both roles in April 2020. His "
            "appointment is widely cited as the inflection point at which "
            "PDVSA's operational capacity broke down, and his replacement "
            "by Tareck El Aissami marked the start of the "
            "OFAC-license-driven rebuild attempts.",
        ),
        timeline=(
            TimelineEntry("2017–2020", "President of PDVSA and Minister of Petroleum"),
            TimelineEntry("2017–2020", "Venezuelan oil output collapsed from ~1.9M bpd to <700k bpd"),
            TimelineEntry("2020", "Removed and replaced by Tareck El Aissami"),
        ),
        faqs=(
            FAQ(
                q="Who is Manuel Quevedo?",
                a="Manuel Quevedo is a retired Venezuelan National Guard general who served as President of PDVSA and Minister of Petroleum from 2017 to 2020. His tenure coincided with the steepest collapse in Venezuelan oil output in modern history.",
            ),
        ),
        sources=(
            Source("Wikipedia: Manuel Quevedo", "https://en.wikipedia.org/wiki/Manuel_Quevedo"),
        ),
        sector_path="/sectors/oil-gas",
        related=("rafael-ramirez", "tareck-el-aissami", "vladimir-padrino-lopez"),
    ),

    # ── Military & security ──────────────────────────────────────────

    "nestor-reverol": Person(
        slug="nestor-reverol",
        name="Néstor Reverol",
        aliases=("Néstor Luis Reverol Torres",),
        role="Former Minister of Interior and Electric Energy; former Corpozulia president",
        cohorts=("military", "executive"),
        status="former",
        one_liner=(
            "Néstor Reverol is a retired Bolivarian National Guard major "
            "general who served as Minister of Interior, Justice, and "
            "Peace (2016–2020), Minister of Electric Energy, and most "
            "recently as president of Corpozulia (2024–2025). He remains "
            "under U.S. federal indictment on narcotics charges."
        ),
        bio=(
            "Néstor Luis Reverol Torres is a retired GNB major general "
            "who has held a sequence of senior security and infrastructure "
            "portfolios in the Maduro government — Minister of Interior, "
            "Justice, and Peace (2016–2020); Minister of Electric Energy "
            "(2020–2024); and most recently president of Corpozulia "
            "(2024–2025), the regional development corporation for Zulia "
            "state. He has been on the OFAC SDN list since 2017 and is "
            "under U.S. federal indictment on narcotics-trafficking "
            "charges issued in the Eastern District of New York in 2015.",
            "Reverol's electricity portfolio coincided with the period of "
            "repeated multi-day blackouts on the Venezuelan national grid "
            "that began in 2019 — a continuing constraint on industrial "
            "operations, oil-sector uptime, and any foreign-investor "
            "scenario premised on local production capacity.",
        ),
        affiliations=("FANB",),
        timeline=(
            TimelineEntry("2016–2020", "Minister of Interior, Justice, and Peace"),
            TimelineEntry("2017", "Added to the OFAC SDN list; indicted in U.S. federal court on narcotics charges"),
            TimelineEntry("2020–2024", "Minister of Electric Energy"),
            TimelineEntry("2024–2025", "President of Corpozulia"),
        ),
        faqs=(
            FAQ(
                q="Who is Néstor Reverol?",
                a="Néstor Reverol is a retired Bolivarian National Guard major general who has held senior portfolios in the Maduro government, including Minister of Interior, Justice, and Peace (2016–2020), Minister of Electric Energy (2020–2024), and president of Corpozulia (2024–2025). He has been on the OFAC SDN list since 2017 and is under U.S. federal indictment on narcotics-trafficking charges.",
            ),
        ),
        sources=(
            Source("Wikipedia: Néstor Reverol", "https://en.wikipedia.org/wiki/N%C3%A9stor_Reverol"),
        ),
        sector_path="/sectors/energy",
        related=("vladimir-padrino-lopez", "diosdado-cabello"),
    ),

    "ivan-hernandez-dala": Person(
        slug="ivan-hernandez-dala",
        name="Iván Hernández Dala",
        aliases=("Iván Rafael Hernández Dala",),
        role="President of CANTV; former DGCIM director (2014–2024)",
        cohorts=("military",),
        status="former",
        one_liner=(
            "General Iván Hernández Dala headed Venezuela's military "
            "counterintelligence service (DGCIM) for ten years until "
            "October 2024, and was named president of CANTV — the state "
            "telecom operator — in November 2024."
        ),
        bio=(
            "Iván Rafael Hernández Dala led Venezuela's General "
            "Directorate of Military Counterintelligence (DGCIM) from "
            "2014 to October 2024, when Nicolás Maduro replaced him with "
            "Major General Javier Marcano Tábata. He was subsequently "
            "appointed president of CANTV, Venezuela's state-owned "
            "telecommunications operator, in November 2024.",
            "The DGCIM under Hernández Dala's command was documented by "
            "the UN's Independent International Fact-Finding Mission on "
            "Venezuela as responsible for torture and arbitrary-detention "
            "practices against dissidents. He has been on the OFAC SDN "
            "list since 2019 and is a sanctions target of the EU, UK, "
            "and Canada.",
            "After the U.S. capture of Maduro on January 3, 2026, Acting "
            "President Delcy Rodríguez appointed General Gustavo "
            "González López as DGCIM director — a separate appointment "
            "post-dating Hernández Dala's earlier removal.",
        ),
        affiliations=("FANB", "CANTV"),
        timeline=(
            TimelineEntry("2014–2024", "Director of the General Directorate of Military Counterintelligence (DGCIM)"),
            TimelineEntry("2019", "Added to the OFAC SDN list"),
            TimelineEntry("2024", "Removed from DGCIM in October; replaced by Javier Marcano Tábata"),
            TimelineEntry("2024", "Appointed president of CANTV, Venezuela's state-owned telecommunications operator"),
        ),
        faqs=(
            FAQ(
                q="Is Iván Hernández Dala still the head of DGCIM?",
                a="No. Hernández Dala headed Venezuela's military counterintelligence service from 2014 until October 2024, when he was replaced by Javier Marcano Tábata. He was subsequently appointed president of CANTV. After the U.S. capture of Nicolás Maduro in January 2026, General Gustavo González López took over the DGCIM.",
            ),
            FAQ(
                q="Is Iván Hernández Dala sanctioned?",
                a="Yes. He has been on the OFAC SDN list since 2019 and is a sanctions target of the EU, UK, and Canada for human-rights violations during his command of the DGCIM.",
            ),
        ),
        sources=(
            Source("Wikipedia: Iván Hernández Dala", "https://en.wikipedia.org/wiki/Iv%C3%A1n_Hern%C3%A1ndez_Dala"),
            Source("UN Independent International Fact-Finding Mission on Venezuela", "https://www.ohchr.org/en/hr-bodies/hrc/ffmv/index"),
        ),
        sector_path="/sectors/telecom",
        related=("gustavo-gonzalez-lopez", "vladimir-padrino-lopez", "diosdado-cabello"),
    ),

    "domingo-hernandez-larez": Person(
        slug="domingo-hernandez-larez",
        name="Domingo Hernández Lárez",
        aliases=("Domingo Antonio Hernández Lárez",),
        role="Former Strategic Operational Commander of the FANB (until March 2026)",
        cohorts=("military",),
        status="former",
        one_liner=(
            "General-in-Chief Domingo Hernández Lárez served as the "
            "Strategic Operational Commander of Venezuela's Bolivarian "
            "National Armed Forces (CEOFANB) from 2021 until March 19, "
            "2026, when he was dismissed by Acting President Delcy "
            "Rodríguez and replaced by Major General Rafael Prieto "
            "Martínez."
        ),
        bio=(
            "Domingo Antonio Hernández Lárez holds the rank of "
            "General-in-Chief, the highest active rank in the Venezuelan "
            "armed forces. As Strategic Operational Commander of the FANB "
            "(CEOFANB) from 2021, he ran the day-to-day operational "
            "integration of the army, navy, air force, GNB, and militia.",
            "He was dismissed from CEOFANB on March 19, 2026 — one day "
            "after Defense Minister Vladimir Padrino López was replaced "
            "in the same Rodríguez cabinet overhaul. Major General "
            "Rafael Prieto Martínez, who had been serving as Inspector "
            "General of the FANB since October 2024, was named as his "
            "successor at CEOFANB. Hernández Lárez is a sanctions target "
            "of the U.S., EU, UK, and Canada.",
        ),
        affiliations=("FANB",),
        timeline=(
            TimelineEntry("2021–2026", "Strategic Operational Commander of the FANB (CEOFANB)"),
            TimelineEntry("2026", "Dismissed from CEOFANB on March 19; replaced by Major General Rafael Prieto Martínez"),
        ),
        faqs=(
            FAQ(
                q="Who is Domingo Hernández Lárez?",
                a="General-in-Chief Domingo Hernández Lárez is a senior Venezuelan military officer who served as the Strategic Operational Commander of the Bolivarian National Armed Forces (CEOFANB) from 2021 until March 19, 2026, when he was dismissed by Acting President Delcy Rodríguez and replaced by Major General Rafael Prieto Martínez.",
            ),
        ),
        sources=(
            Source("Wikipedia: Domingo Hernández Lárez", "https://es.wikipedia.org/wiki/Domingo_Hern%C3%A1ndez_L%C3%A1rez"),
            Source("La Patilla — Hernández Lárez replaced by Rafael Prieto Martínez", "https://lapatilla.com/2026/03/19/le-serrucharon-el-cargo-a-domingo-hernandez-larez-designaron-a-rafael-prieto-martinez-como-nuevo-jefe-del-ceofanb/"),
        ),
        related=("vladimir-padrino-lopez", "gustavo-gonzalez-lopez", "delcy-rodriguez"),
    ),

    "henry-rangel-silva": Person(
        slug="henry-rangel-silva",
        name="Henry Rangel Silva",
        aliases=("Henry de Jesús Rangel Silva",),
        role="Former Defense Minister of Venezuela",
        cohorts=("military", "executive"),
        status="former",
        one_liner=(
            "General-in-Chief Henry Rangel Silva is a former Defense "
            "Minister and former governor of Trujillo state, one of the "
            "longest-serving senior military figures in the Bolivarian "
            "government."
        ),
        bio=(
            "Henry de Jesús Rangel Silva is a retired General-in-Chief "
            "of the Venezuelan armed forces who served as Minister of "
            "Defense (2012–2013) and as governor of Trujillo state "
            "(2012, 2017–2021). He has been on the U.S. Treasury OFAC "
            "kingpin list since 2008 — a designation that predates the "
            "Venezuela-program SDN architecture.",
        ),
        affiliations=("FANB",),
        faqs=(
            FAQ(
                q="Who is Henry Rangel Silva?",
                a="General-in-Chief Henry Rangel Silva is a former Defense Minister of Venezuela (2012–2013) and former governor of Trujillo state. He has been a U.S. Treasury OFAC sanctions target since 2008, predating the Venezuela-specific sanctions programs.",
            ),
        ),
        sources=(
            Source("Wikipedia: Henry Rangel Silva", "https://en.wikipedia.org/wiki/Henry_Rangel_Silva"),
        ),
        wikidata_id="Q5719498",
        related=("vladimir-padrino-lopez",),
    ),

    # ── Judiciary & electoral ────────────────────────────────────────

    "tarek-william-saab": Person(
        slug="tarek-william-saab",
        name="Tarek William Saab",
        aliases=("Tarek William Saab Halabi",),
        role="Acting Ombudsman; former Fiscal General of Venezuela (2017–2026)",
        cohorts=("judiciary", "executive"),
        status="former",
        one_liner=(
            "Tarek William Saab is a Venezuelan lawyer who served as "
            "Fiscal General de la República — head of the Public Ministry "
            "— from 2017 until February 25, 2026, when he resigned and "
            "was named acting Defensor del Pueblo (Ombudsman). He was "
            "replaced as Fiscal by Larry Devoe."
        ),
        bio=(
            "Tarek William Saab Halabi is a Venezuelan lawyer, poet, and "
            "longtime Bolivarian-government official who served as "
            "Ombudsman of Venezuela (2014–2017) before being appointed "
            "Fiscal General de la República by the National Constituent "
            "Assembly in August 2017. He held the Fiscal post for nearly "
            "a decade and is on the OFAC SDN list under Venezuela-related "
            "programs.",
            "His tenure as Fiscal General overlapped with the most "
            "significant period of foreign-investor litigation against "
            "Venezuela — the PDVSA 2020 bond default, the Crystallex "
            "writ-of-execution proceedings against CITGO, the prosecution "
            "of opposition leaders, and the major PDVSA-Cripto corruption "
            "probe that led to the 2024 arrest of former Vice President "
            "Tareck El Aissami.",
            "Saab resigned the Fiscal post on February 25, 2026 — four "
            "days after the National Assembly approved the Amnesty Law "
            "and ten months after his Ombudsman predecessor Alfredo Ruiz "
            "left office. The same day, Acting President Delcy Rodríguez "
            "named him acting Defensor del Pueblo. He was replaced as "
            "Fiscal General by Larry Devoe, who was formally confirmed "
            "by the National Assembly on April 9, 2026.",
        ),
        born="1962-09-10",
        affiliations=("PSUV",),
        timeline=(
            TimelineEntry("2014–2017", "Ombudsman of Venezuela (Defensor del Pueblo)"),
            TimelineEntry("2017", "Appointed Fiscal General de la República by the National Constituent Assembly"),
            TimelineEntry("2026", "Resigned as Fiscal General on February 25; named acting Defensor del Pueblo the same day"),
            TimelineEntry("2026", "Replaced as Fiscal General by Larry Devoe (confirmed by the National Assembly April 9)"),
        ),
        faqs=(
            FAQ(
                q="Who is Tarek William Saab?",
                a="Tarek William Saab is a Venezuelan lawyer who served as Fiscal General de la República (head of the Public Ministry) from 2017 to February 25, 2026. He resigned the Fiscal post and was named acting Defensor del Pueblo (Ombudsman). He was replaced as Fiscal by Larry Devoe.",
            ),
            FAQ(
                q="Why did Tarek William Saab resign as Fiscal General?",
                a="Saab announced his resignation on February 25, 2026 by saying he had completed his cycle leading the institution. The move came four days after the National Assembly approved the Amnesty Law and shortly after the cabinet overhaul that began under Acting President Delcy Rodríguez following the U.S. capture of Nicolás Maduro.",
            ),
            FAQ(
                q="Is Tarek William Saab the same person as Alex Saab?",
                a="No. Tarek William Saab Halabi (the Fiscal General / Ombudsman) and Alex Nain Saab Moran (a businessman accused of being a frontman for the Maduro government and the subject of a high-profile U.S. extradition case) share a surname but are not closely related. They are two of the most-confused names in Venezuela-related sanctions research.",
            ),
        ),
        sources=(
            Source("Wikipedia: Tarek William Saab", "https://en.wikipedia.org/wiki/Tarek_William_Saab"),
            Source("CNN — Saab renuncia como fiscal de Venezuela", "https://cnnespanol.cnn.com/2026/02/25/venezuela/tarek-william-saab-alfredo-ruiz-angulo-renuncias-orix"),
            Source("CNN — Larry Devoe confirmado como fiscal general", "https://cnnespanol.cnn.com/2026/04/09/venezuela/larry-devoe-nuevo-fiscal-asamblea-orix"),
        ),
        wikidata_id="Q3503497",
        sector_path="/sectors/legal",
        related=("larry-devoe", "arianny-seijo-noguera", "nicolas-maduro", "delcy-rodriguez"),
    ),

    "maikel-moreno": Person(
        slug="maikel-moreno",
        name="Maikel Moreno",
        aliases=("Maikel José Moreno Pérez",),
        role="Former President of the Supreme Tribunal of Justice (TSJ)",
        cohorts=("judiciary",),
        status="former",
        one_liner=(
            "Maikel Moreno served as President of Venezuela's Supreme "
            "Tribunal of Justice (TSJ) from 2017 to 2022 — one of the "
            "most heavily sanctioned figures in the Bolivarian government "
            "and the subject of U.S. federal corruption charges."
        ),
        bio=(
            "Maikel José Moreno Pérez is a former Venezuelan judge who "
            "served as President of the TSJ from 2017 to 2022. The TSJ is "
            "Venezuela's supreme court and, under his presidency, ruled "
            "in favour of nearly every Maduro-government position in "
            "constitutional and electoral disputes — including the "
            "decisions disqualifying opposition candidates from running "
            "for president.",
            "Moreno was indicted in U.S. federal court in 2020 on money-"
            "laundering and corruption charges and is on the OFAC SDN "
            "list. His tenure is the central case study cited by foreign-"
            "investor litigation teams when assessing the independence "
            "(or absence) of the Venezuelan judicial system.",
        ),
        affiliations=("PSUV",),
        timeline=(
            TimelineEntry("2017–2022", "President of the Supreme Tribunal of Justice (TSJ)"),
            TimelineEntry("2020", "Indicted in U.S. federal court on money-laundering and corruption charges"),
        ),
        faqs=(
            FAQ(
                q="Who is Maikel Moreno?",
                a="Maikel Moreno is a former President of Venezuela's Supreme Tribunal of Justice (TSJ), serving from 2017 to 2022. He was indicted in U.S. federal court in 2020 on money-laundering and corruption charges and remains on the OFAC SDN list.",
            ),
        ),
        sources=(
            Source("Wikipedia: Maikel Moreno", "https://en.wikipedia.org/wiki/Maikel_Moreno"),
        ),
        wikidata_id="Q42306474",
        sector_path="/sectors/legal",
        related=("tarek-william-saab", "nicolas-maduro"),
    ),

    "caryslia-rodriguez": Person(
        slug="caryslia-rodriguez",
        name="Caryslia Rodríguez",
        aliases=("Caryslia Beatriz Rodríguez Rodríguez",),
        role="President of the Supreme Tribunal of Justice (TSJ)",
        cohorts=("judiciary",),
        one_liner=(
            "Caryslia Rodríguez is the President of Venezuela's Supreme "
            "Tribunal of Justice (TSJ) — the senior judicial figure who "
            "ruled to certify the contested 2024 presidential election "
            "result."
        ),
        bio=(
            "Caryslia Beatriz Rodríguez Rodríguez was elected President "
            "of the Supreme Tribunal of Justice in 2024. The TSJ has "
            "been the body issuing the most consequential post-election "
            "rulings of the Maduro era — including the August 2024 "
            "ruling certifying Nicolás Maduro as the winner of the July "
            "2024 presidential election, which the U.S., EU, and most "
            "Latin American governments rejected.",
        ),
        timeline=(
            TimelineEntry("2024", "Elected President of the Supreme Tribunal of Justice (TSJ)"),
            TimelineEntry("2024", "Issued the TSJ ruling certifying the contested July 2024 presidential election result"),
        ),
        faqs=(
            FAQ(
                q="Who is Caryslia Rodríguez?",
                a="Caryslia Rodríguez is the President of Venezuela's Supreme Tribunal of Justice (TSJ), elected to the role in 2024. The TSJ under her presidency issued the August 2024 ruling certifying the contested July 2024 presidential election result.",
            ),
        ),
        sources=(
            Source("Tribunal Supremo de Justicia", "http://www.tsj.gob.ve/"),
        ),
        sector_path="/sectors/legal",
        related=("nicolas-maduro", "elvis-hidrobo-amoroso", "arianny-seijo-noguera"),
    ),

    "elvis-hidrobo-amoroso": Person(
        slug="elvis-hidrobo-amoroso",
        name="Elvis Amoroso",
        aliases=("Elvis Eduardo Amoroso", "Elvis Hidrobo Amoroso"),
        role="President of the National Electoral Council (CNE)",
        cohorts=("judiciary",),
        one_liner=(
            "Elvis Hidrobo Amoroso is the President of Venezuela's "
            "National Electoral Council (CNE) — the body that "
            "administered the contested July 2024 presidential election "
            "and certified Nicolás Maduro as winner."
        ),
        bio=(
            "Elvis Eduardo Hidrobo Amoroso was elected President of the "
            "CNE in 2023. The CNE is Venezuela's electoral authority, "
            "responsible for organizing and certifying federal and state "
            "elections. Hidrobo Amoroso previously served as Comptroller "
            "General of Venezuela — the office that issued the "
            "disqualification ruling barring María Corina Machado from "
            "the 2024 presidential ballot.",
            "He has been on the OFAC SDN list under Venezuela-related "
            "programs since the period of his Comptroller tenure. His "
            "control of the CNE during the 2024 election cycle is the "
            "central electoral-integrity dispute foreign-policy "
            "observers cite when assessing the legitimacy of the "
            "Maduro government's continued rule.",
        ),
        affiliations=("PSUV",),
        faqs=(
            FAQ(
                q="Who is Elvis Hidrobo Amoroso?",
                a="Elvis Hidrobo Amoroso is the President of Venezuela's National Electoral Council (CNE), elected to the role in 2023. He previously served as Comptroller General — the office that issued the disqualification ruling barring María Corina Machado from the 2024 presidential ballot. He is on the OFAC SDN list.",
            ),
        ),
        sources=(
            Source("Consejo Nacional Electoral (CNE)", "https://www.cne.gob.ve/"),
        ),
        sector_path="/sectors/governance",
        related=("caryslia-rodriguez", "nicolas-maduro", "maria-corina-machado"),
    ),

    # ── Opposition & exile ───────────────────────────────────────────

    "leopoldo-lopez": Person(
        slug="leopoldo-lopez",
        name="Leopoldo López",
        aliases=("Leopoldo Eduardo López Mendoza",),
        role="Founder of the Voluntad Popular opposition party (in exile)",
        cohorts=("opposition",),
        status="in_exile",
        one_liner=(
            "Leopoldo López is the founder of the Voluntad Popular "
            "opposition party and one of the longest-prominent figures "
            "of the Venezuelan democratic opposition — currently in "
            "exile in Spain."
        ),
        bio=(
            "Leopoldo Eduardo López Mendoza is a Venezuelan economist "
            "and politician who founded the Voluntad Popular party in "
            "2009. He served as mayor of Chacao municipality (Caracas) "
            "before being arrested in 2014 on charges of incitement, "
            "and was held by Venezuelan authorities until 2019 when he "
            "left the country to seek refuge in the Spanish embassy in "
            "Caracas. He has been in exile in Spain since 2020.",
            "López is the political mentor of former interim president "
            "Juan Guaidó and remains an active organiser of the "
            "Venezuelan opposition from abroad. His detention and "
            "subsequent exile is one of the most-cited cases in U.S. "
            "and EU human-rights designations against the Maduro "
            "government.",
        ),
        born="1971-04-29",
        birthplace="Caracas, Venezuela",
        affiliations=("Voluntad Popular", "Plataforma Unitaria"),
        timeline=(
            TimelineEntry("2008–2014", "Mayor of Chacao municipality, Caracas"),
            TimelineEntry("2009", "Co-founded Voluntad Popular"),
            TimelineEntry("2014–2019", "Held in detention by Venezuelan authorities"),
            TimelineEntry("2019", "Left house arrest and took refuge in the Spanish embassy in Caracas"),
            TimelineEntry("2020", "Went into exile in Spain"),
        ),
        faqs=(
            FAQ(
                q="Who is Leopoldo López?",
                a="Leopoldo López is the founder of the Voluntad Popular opposition party and one of the longest-prominent figures of the Venezuelan democratic opposition. He has lived in exile in Spain since 2020.",
            ),
        ),
        sources=(
            Source("Wikipedia: Leopoldo López", "https://en.wikipedia.org/wiki/Leopoldo_L%C3%B3pez"),
        ),
        wikidata_id="Q443499",
        related=("juan-guaido", "maria-corina-machado", "edmundo-gonzalez"),
    ),

    "juan-guaido": Person(
        slug="juan-guaido",
        name="Juan Guaidó",
        aliases=("Juan Gerardo Guaidó Márquez",),
        role="Former President of the National Assembly and Interim President of Venezuela (2019–2023; in exile)",
        cohorts=("opposition",),
        status="in_exile",
        one_liner=(
            "Juan Guaidó is a former President of the Venezuelan "
            "National Assembly who declared himself Interim President "
            "of Venezuela in 2019 — a claim recognized at the time by "
            "the U.S. and over 50 other governments."
        ),
        bio=(
            "Juan Gerardo Guaidó Márquez is a Venezuelan industrial "
            "engineer and politician who served as President of the "
            "opposition-controlled National Assembly elected in 2015. "
            "Citing constitutional provisions on a vacant presidency, "
            "Guaidó assumed the role of Interim President of Venezuela "
            "in January 2019 — a claim recognized by the United States, "
            "the United Kingdom, the European Parliament, the Lima "
            "Group, and over 50 other governments at its peak.",
            "International recognition of the Guaidó interim presidency "
            "eroded over 2020–2022, and the opposition-led National "
            "Assembly voted to dissolve the interim-government structure "
            "in December 2022. Guaidó left Venezuela for the United "
            "States in 2023 and has remained based there since.",
        ),
        born="1983-07-28",
        affiliations=("Voluntad Popular", "Plataforma Unitaria"),
        timeline=(
            TimelineEntry("2019", "Declared Interim President of Venezuela; recognized by the U.S. and 50+ governments"),
            TimelineEntry("2022", "Opposition-led National Assembly voted to dissolve the interim-government structure"),
            TimelineEntry("2023", "Left Venezuela for the United States"),
        ),
        faqs=(
            FAQ(
                q="Who is Juan Guaidó?",
                a="Juan Guaidó is a former President of the Venezuelan National Assembly who served as the U.S.-recognized Interim President of Venezuela from January 2019 until the National Assembly voted to dissolve the interim-government structure in December 2022. He has been based in the United States since 2023.",
            ),
        ),
        sources=(
            Source("Wikipedia: Juan Guaidó", "https://en.wikipedia.org/wiki/Juan_Guaid%C3%B3"),
        ),
        wikidata_id="Q56304019",
        related=("leopoldo-lopez", "maria-corina-machado", "edmundo-gonzalez"),
    ),

    "henrique-capriles": Person(
        slug="henrique-capriles",
        name="Henrique Capriles",
        aliases=("Henrique Capriles Radonski",),
        role="Opposition leader and two-time presidential candidate",
        cohorts=("opposition",),
        status="current",
        one_liner=(
            "Henrique Capriles is a longtime Venezuelan opposition "
            "leader, former governor of Miranda state, and two-time "
            "presidential candidate against Hugo Chávez and Nicolás "
            "Maduro."
        ),
        bio=(
            "Henrique Capriles Radonski is a Venezuelan lawyer who "
            "served as governor of Miranda state (2008–2017) and as "
            "the unified opposition candidate against Hugo Chávez in "
            "2012 and against Nicolás Maduro in the April 2013 "
            "post-Chávez special election. He lost both elections by "
            "narrow margins amid disputed conditions.",
            "Capriles was barred from holding public office by the "
            "Comptroller of Venezuela for 15 years in 2017, a "
            "disqualification that has since been lifted. He remains "
            "active in opposition politics and has been an alternative "
            "voice within the broader Plataforma Unitaria coalition "
            "during the María Corina Machado / Edmundo González cycle.",
        ),
        born="1972-07-11",
        birthplace="Caracas, Venezuela",
        affiliations=("Primero Justicia", "Plataforma Unitaria"),
        faqs=(
            FAQ(
                q="Who is Henrique Capriles?",
                a="Henrique Capriles is a Venezuelan lawyer and opposition leader who served as governor of Miranda state (2008–2017) and was the unified opposition candidate against Hugo Chávez in 2012 and against Nicolás Maduro in 2013. He remains a senior figure in the opposition coalition.",
            ),
        ),
        sources=(
            Source("Wikipedia: Henrique Capriles Radonski", "https://en.wikipedia.org/wiki/Henrique_Capriles_Radonski"),
        ),
        wikidata_id="Q1062303",
        related=("maria-corina-machado", "leopoldo-lopez", "edmundo-gonzalez"),
    ),

    # ── Figures who took office during / after the January 2026 transition ──

    "larry-devoe": Person(
        slug="larry-devoe",
        name="Larry Devoe",
        aliases=("Larry Daniel Devoe Márquez",),
        role="Fiscal General of Venezuela (head of the Public Ministry)",
        cohorts=("judiciary",),
        status="current",
        in_office_since="2026-02-25",
        one_liner=(
            "Larry Devoe is the Fiscal General de la República — head of "
            "Venezuela's Public Ministry — confirmed by the National "
            "Assembly on April 9, 2026 after serving as interim Fiscal "
            "from February 25. He replaced Tarek William Saab, who held "
            "the role for nearly a decade."
        ),
        bio=(
            "Larry Daniel Devoe Márquez is a Venezuelan lawyer who took "
            "control of the Fiscalía on February 25, 2026, the same day "
            "Tarek William Saab announced his resignation. The National "
            "Assembly formally confirmed Devoe as Fiscal General de la "
            "República on April 9, 2026 with 275 votes in favor.",
            "Devoe holds a master's degree in Constitutional Law from "
            "the University of Valencia and a master's in Democracy, "
            "Human Rights and the Rule of Law from the University of "
            "Alcalá. He previously served as Venezuela's representative "
            "to the UN Human Rights Council and to the Inter-American "
            "Commission on Human Rights, and as executive secretary of "
            "the National Human Rights Council (CNDH).",
            "For foreign-investor and compliance teams, the Fiscalía "
            "under Devoe is the criminal-prosecution counterparty on "
            "PDVSA-corruption proceedings (including the ongoing "
            "PDVSA-Cripto case against former Vice President Tareck El "
            "Aissami) and on prosecutions involving foreign companies "
            "operating in Venezuela. Independent observers have "
            "characterised the Devoe appointment as a 'line of "
            "continuity' with Saab's tenure rather than a clean break.",
        ),
        affiliations=("Ministerio Público",),
        timeline=(
            TimelineEntry("2026", "Took control of the Fiscalía as interim Fiscal General on February 25"),
            TimelineEntry("2026", "Confirmed by the National Assembly as Fiscal General on April 9 with 275 votes"),
        ),
        faqs=(
            FAQ(
                q="Who is Larry Devoe?",
                a="Larry Devoe is the Fiscal General de la República — the head of Venezuela's Public Ministry — confirmed by the National Assembly on April 9, 2026. He had been serving as interim Fiscal since February 25, when Tarek William Saab resigned. Devoe is a constitutional and human-rights lawyer who previously represented Venezuela at the UN Human Rights Council.",
            ),
            FAQ(
                q="What does the Fiscal General do?",
                a="The Fiscal General de la República heads the Ministerio Público, the Venezuelan Public Ministry. The office prosecutes criminal cases, oversees the constitutional conduct of state institutions, and is the body that has prosecuted the PDVSA-Cripto corruption case against former Vice President Tareck El Aissami.",
            ),
            FAQ(
                q="Is the Fiscal General the same as the Procuradora General?",
                a="No. Both are translated as 'Attorney General' in English but are separate Venezuelan offices. The Fiscal General de la República (currently Larry Devoe) heads the Public Ministry and prosecutes criminal cases. The Procuradora General de la República (currently Arianny Seijo Noguera) represents the Venezuelan state in civil and commercial litigation, including international arbitration disputes.",
            ),
        ),
        sources=(
            Source("CNN — Larry Devoe confirmado como nuevo fiscal general", "https://cnnespanol.cnn.com/2026/04/09/venezuela/larry-devoe-nuevo-fiscal-asamblea-orix"),
            Source("CNN — Quién es Larry Devoe", "https://cnnespanol.cnn.com/2026/04/15/venezuela/larry-devoe-nuevo-fiscal-general-perfil-orix"),
            Source("Infobae — Larry Devoe colaborador cercano del chavismo", "https://www.infobae.com/venezuela/2026/04/09/larry-devoe-colaborador-cercano-del-chavismo-fue-designado-como-nuevo-fiscal-general-de-venezuela/"),
        ),
        sector_path="/sectors/legal",
        related=("tarek-william-saab", "arianny-seijo-noguera", "delcy-rodriguez", "tareck-el-aissami"),
    ),

    "gustavo-gonzalez-lopez": Person(
        slug="gustavo-gonzalez-lopez",
        name="Gustavo González López",
        aliases=("Gustavo Enrique González López",),
        role="Defense Minister of Venezuela",
        cohorts=("military", "executive"),
        status="current",
        in_office_since="2026-03-18",
        one_liner=(
            "General Gustavo González López is Venezuela's Defense "
            "Minister, appointed by Acting President Delcy Rodríguez on "
            "March 18, 2026 to replace Vladimir Padrino López. A "
            "U.S.-trained intelligence officer, he was previously head "
            "of the DGCIM and SEBIN."
        ),
        bio=(
            "Gustavo Enrique González López is a 65-year-old Venezuelan "
            "general who was appointed Minister of Defense on March 18, "
            "2026 by Acting President Delcy Rodríguez, replacing Vladimir "
            "Padrino López after Padrino's 11-year tenure. The "
            "appointment is the most consequential cabinet move so far "
            "of the Rodríguez transition government — replacing the "
            "uniformed figure who held the FANB together under Maduro "
            "with a Rodríguez-aligned intelligence chief.",
            "González López's background is in intelligence rather than "
            "operational command. He served as Venezuela's domestic "
            "intelligence director (SEBIN) until mid-2024, then worked "
            "with Rodríguez as head of strategic affairs at PDVSA. After "
            "the U.S. capture of Maduro on January 3, 2026, Rodríguez "
            "promoted him to head the DGCIM (military counterintelligence) "
            "and the Presidential Honor Guard before elevating him to "
            "the Defense Ministry in March.",
            "González López is sanctioned by the U.S. and the EU for "
            "human-rights abuses connected to his time leading SEBIN.",
        ),
        affiliations=("FANB", "SEBIN", "DGCIM"),
        timeline=(
            TimelineEntry("?–2024", "Director of SEBIN (Venezuelan domestic intelligence)"),
            TimelineEntry("2024", "Head of strategic affairs at PDVSA"),
            TimelineEntry("2026", "Appointed director of DGCIM and head of the Presidential Honor Guard by Acting President Delcy Rodríguez (January)"),
            TimelineEntry("2026", "Appointed Minister of Defense on March 18, replacing Vladimir Padrino López"),
        ),
        faqs=(
            FAQ(
                q="Who is Gustavo González López?",
                a="General Gustavo González López is the current Minister of Defense of Venezuela, appointed by Acting President Delcy Rodríguez on March 18, 2026 to replace Vladimir Padrino López. He is a longtime intelligence officer who previously led SEBIN (domestic intelligence) and the DGCIM (military counterintelligence). He is sanctioned by the U.S. and EU for human-rights abuses.",
            ),
            FAQ(
                q="Why was González López appointed Defense Minister?",
                a="His appointment is widely read as Acting President Delcy Rodríguez consolidating control of the Venezuelan armed forces by replacing longtime Padrino loyalists with a Rodríguez-aligned intelligence chief. The Trump administration views the move as a meaningful step in the post-Maduro transition.",
            ),
        ),
        sources=(
            Source("NBC News — Venezuela's acting president replaces longtime defense minister with intelligence head", "https://www.nbcnews.com/world/venezuela/venezuelas-acting-president-replaces-long-time-defense-minister-rcna264159"),
            Source("Al Jazeera — Delcy Rodriguez replaces Padrino", "https://www.aljazeera.com/news/2026/3/18/delcy-rodriguez-replaces-venezuelas-defence-minister-vladimir-padrino"),
            Source("PBS NewsHour — Venezuela's acting president names new defense chief", "https://www.pbs.org/newshour/world/venezuelas-acting-president-names-new-defense-chief-to-replace-longtime-maduro-loyalist"),
        ),
        related=("vladimir-padrino-lopez", "delcy-rodriguez", "ivan-hernandez-dala", "diosdado-cabello"),
    ),

    "hector-obregon": Person(
        slug="hector-obregon",
        name="Héctor Obregón",
        aliases=("Héctor Andrés Obregón Pérez",),
        role="President of PDVSA",
        cohorts=("energy",),
        status="current",
        in_office_since="2024-08",
        one_liner=(
            "Héctor Obregón is the President of Petróleos de Venezuela "
            "(PDVSA) — Venezuela's state oil company — originally named "
            "by Nicolás Maduro in August 2024 and ratified by Acting "
            "President Delcy Rodríguez in March 2026."
        ),
        bio=(
            "Héctor Andrés Obregón Pérez is a longtime PDVSA insider "
            "who was named President of the company by Nicolás Maduro "
            "in August 2024, succeeding Pedro Tellechea. He was "
            "ratified in the role by Acting President Delcy Rodríguez "
            "via Decree No. 5,273 published in the Official Gazette on "
            "March 13, 2026 — making him one of the senior figures "
            "from the Maduro era who has retained his post under the "
            "Rodríguez transition government.",
            "As PDVSA president, Obregón has set a target of growing "
            "Venezuelan oil production by 18 percent in 2026. His "
            "tenure spans the politically consequential expiry of OFAC "
            "General License 44, the ongoing CITGO writ-of-execution "
            "proceedings in U.S. courts, and the PDVSA-Cripto "
            "corruption probe — making him the central operational "
            "counterparty for any foreign oil-sector engagement with "
            "Venezuela.",
        ),
        affiliations=("PDVSA",),
        timeline=(
            TimelineEntry("2024", "Named President of PDVSA in August by Nicolás Maduro, replacing Pedro Tellechea"),
            TimelineEntry("2026", "Ratified as PDVSA President by Acting President Delcy Rodríguez via Official Gazette decree on March 13"),
        ),
        faqs=(
            FAQ(
                q="Who is the current president of PDVSA?",
                a="Héctor Obregón is the President of PDVSA. He was originally appointed by Nicolás Maduro in August 2024 and was ratified in the role by Acting President Delcy Rodríguez via Official Gazette decree on March 13, 2026.",
            ),
            FAQ(
                q="What is PDVSA's oil-production target for 2026?",
                a="Obregón has stated that PDVSA's target for 2026 is to grow oil production by at least 18 percent.",
            ),
        ),
        sources=(
            Source("Últimas Noticias — Héctor Obregón ratificado como presidente de PDVSA", "https://ultimasnoticias.com.ve/economia/petroleo/hector-obregon-ratificado-como-presidente-de-pdvsa/"),
            Source("Bloomberg — Héctor Obregón profile", "https://www.bloomberg.com/profile/person/23599579"),
        ),
        sector_path="/sectors/oil-gas",
        related=("pedro-tellechea", "delcy-rodriguez", "tareck-el-aissami", "rafael-ramirez"),
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
