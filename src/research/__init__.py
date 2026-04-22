"""
src/research/ — per-SDN research dossier pages served under
/research/sdn/<slug>.

ALLOWED_ENTITIES is the slug allowlist that gates which dossiers exist.
Adding a slug here is the only way a new /research/sdn/<slug> URL
becomes routable; the slug must also have a matching record in
src.sanctions and (optionally) a curated_sources.json entry.
"""

ALLOWED_ENTITIES: dict[str, str] = {
    # Carretero brothers (3 entries) — added April 2026 to serve the
    # 'ramon/vicente/roberto carretero' OSINT-style queries that started
    # showing up in GSC after their EO 13850 designation.
    "carretero-napolitano-ramon": "individuals",
    "carretero-napolitano-vicente-luis": "individuals",
    "carretero-napolitano-roberto": "individuals",
    # Saab cluster (6 entries) — six Venezuela-program SDN designees
    # share the SAAB surname. Disambiguation matters: Alex Nain Saab
    # Moran (Maduro's frontman) is searched 100x more than the rest,
    # but compliance teams routinely confuse him with his brothers
    # (Amir Luis, Luis Alberto) and his Colombian cousins (Isham Ali,
    # Shadi Nain Saab Certain) and especially with Tarek William Saab
    # Halabi — Venezuela's sitting Attorney General, no familial
    # relation to Alex Saab. Each gets its own dossier so the
    # surname-cluster strip in the disambiguator gives the analyst a
    # one-screen "is this my Saab?" answer.
    "saab-moran-alex-nain": "individuals",
    "saab-moran-amir-luis": "individuals",
    "saab-moran-luis-alberto": "individuals",
    "saab-certain-isham-ali": "individuals",
    "saab-certain-shadi-nain": "individuals",
    "saab-halabi-tarek-william": "individuals",
}
