"""
Content for the Venezuelan-visa application landing-page cluster.

Cluster shape:
  /apply-for-venezuelan-visa                      <- pillar (main)
  /apply-for-venezuelan-visa/us-citizens          <- variant
  /apply-for-venezuelan-visa/business-visa        <- variant
  /apply-for-venezuelan-visa/china                <- variant

Each entry is consumed by `templates/apply_for_venezuelan_visa.html.j2`
through a single shared schema, so all four URLs render from one template
with per-variant overrides (hero copy, document checklist additions, FAQ
entries, warnings, breadcrumbs).

Authoritative sources (verify before editing):
  https://ve.usembassy.gov/venezuela-electronic-visa-application-process-update/
  https://www.fragomen.com/insights/venezuela-introduction-of-electronic-visa-system-for-tourist-and-business-travelers.html
  https://cancilleriadigital.mppre.gob.ve/
  https://china.embajada.gob.ve/
"""

from __future__ import annotations

from src.data.visa_document_landing import PLANILLA_HERO_LINE

# Fee per Fragomen advisory (April 2026): the official Cancillería Digital
# e-visa fee was raised to USD 180 for tourist (TR-V) and business (TR-N)
# applications, up from the legacy USD 60. Confirmed by US-citizen-focused
# coverage (TravelOffPath, TravUnited) reporting the same figure.
EVISA_FEE_USD = 180

# Official MPPRE screen recording of the Cancillería Digital request flow.
MPPRE_EVISA_REQUEST_PROCESS_VIDEO = (
    "https://mppre.gob.ve/gestor2/archivos/cancilleria_digital/video/"
    "1774461338_Request-Process-(2).mp4"
)

# Shared 6-step playbook used as the canonical "how to apply" sequence on
# the pillar page and on the US-citizen / business-visa variants. The
# China variant overrides this with its own consulate-routed sequence.
SHARED_EVISA_STEPS: list[dict] = [
    {
        "title": "Watch the instructional video",
        "detail": (
            "Venezuela’s Ministry of Foreign Affairs hosts a short screen "
            "recording of the Cancillería Digital request process. Watch it "
            "once before you register so you recognise the menus and upload "
            "steps. The live portal stays in Spanish — keep a translator tab "
            "open if you need it."
        ),
        "url": MPPRE_EVISA_REQUEST_PROCESS_VIDEO,
        "url_label": "Watch the instructional video",
    },
    {
        "title": "Register on Cancillería Digital",
        "detail": (
            "Create an account on Venezuela's official Ministry of Foreign "
            "Affairs (MPPRE) e-visa portal. The interface is in Spanish — "
            "use a translator if needed. Click \"regístrate\" on the login "
            "screen."
        ),
        "url": "https://cancilleriadigital.mppre.gob.ve/",
        "url_label": "Open the e-visa portal",
    },
    {
        "title": "Complete the electronic application form",
        "anchor": "electronic-application-form",
        "detail": (
            "Fill in personal, passport, travel, and financial information. "
            "You will be asked which visa type you want (Tourist TR-V or "
            "Business TR-N) and your intended dates of travel."
        ),
        "service_plug": (
            "Want us to handle the form and filing? Our same-day Venezuela "
            "visa application service prepares the package, submits the "
            "application, and monitors the case."
        ),
        "service_url": "/get-venezuela-visa",
        "service_label": "Get Venezuela visa help",
    },
    {
        "title": "Upload supporting documents",
        "detail": (
            "Digital scans of: (1) a valid passport with 6+ months validity "
            "and 2 blank pages, (2) a passport-sized photo, (3) hotel "
            "reservation or invitation letter, (4) round-trip flight "
            "itinerary, and (5) proof of funds. Business visas additionally "
            "require a corporate invitation letter from a Venezuelan entity."
        ),
    },
    {
        "title": "Pay the visa fee through the portal",
        "detail": (
            f"The fee is USD {EVISA_FEE_USD} (raised from USD 60 in 2025) "
            "and is paid digitally inside the portal. Confirm with your "
            "card issuer that international payments to Venezuela will not "
            "be auto-blocked."
        ),
    },
    {
        "title": "Wait for approval and download your e-visa",
        "detail": (
            "Approval is delivered through the portal — typically in around "
            "15 days, with a real-world range of 7–30 days. Print the "
            "approved visa and bring the printout with you; present it "
            "together with your passport at SVMI airport on arrival. Do not "
            "book non-refundable flights before the visa is in hand."
        ),
    },
]

# Document checklist used by the pillar and US-citizen variant. Variants
# can extend or replace items via `extra_documents` / `documents` overrides.
SHARED_DOCUMENT_CHECKLIST: list[dict] = [
    {
        "label": "Valid passport",
        "detail": "Issued by your home country, with at least 6 months validity beyond your planned date of entry and 2 blank pages.",
    },
    {
        "label": "Passport-style digital photo",
        "detail": "Recent (within the last 6 months), white background, 600×600 pixels minimum, no glasses or headwear.",
    },
    {
        "label": "Round-trip flight itinerary",
        "detail": "Confirmed reservation showing entry and exit dates. Most direct services into Caracas (SVMI) route through Panama City, Bogotá, or Madrid.",
    },
    {
        "label": "Proof of accommodation",
        "detail": "Hotel reservation covering your full stay, or — for business travel — an invitation letter from a Venezuelan entity registered with SENIAT.",
    },
    {
        "label": f"{PLANILLA_HERO_LINE} (MPPRE application form)",
        "detail": (
            "The ministry’s structured visa request form, usually attached as a PDF in the "
            "portal. Fill all sections in our type-in sheet, then use your browser’s "
            "Print → Save as PDF, and upload the file where the system asks."
        ),
        "cta": {"label": "Complete now", "href": "/apply-for-venezuelan-visa/planilla"},
    },
    {
        "label": "Declaración jurada (sworn statement)",
        "detail": (
            "A short no–criminal-record declaration in Spanish, often required with the file. "
            "Standard text is pre-filled; add your name, country, and passport, type your "
            "signature (renders in cursive for print), or print and sign by hand on paper."
        ),
        "cta": {"label": "Complete now", "href": "/apply-for-venezuelan-visa/declaracion-jurada"},
    },
]


# ---------------------------------------------------------------------------
# Pillar page — /apply-for-venezuelan-visa
# ---------------------------------------------------------------------------
PILLAR: dict = {
    "slug": "",  # pillar lives at /apply-for-venezuelan-visa (no sub-slug)
    "page_label": "Apply for a Venezuelan visa",
    "h1": "How To Apply For A Venezuelan Visa (2026)",
    "kicker": "Visa & entry guide · Updated for the new e-visa portal",
    "lede": (
        "Venezuela now issues tourist (TR-V) and business (TR-N) visas "
        "through an online e-visa portal — the Cancillería Digital. "
        "This guide walks you through the full application."
    ),
    # Hero strip: only facts that are *not* repeated above the fold in the
    # lede, intro warnings, or later Fees / Timeline / step-by-step blocks.
    "key_facts": [
        {"label": "Visa validity", "value": "Up to 1 year, multiple-entry"},
        {"label": "Length of each stay", "value": "Up to 90 days per entry"},
    ],
    "intro_warnings": [
        {
            "label": "No visa on arrival",
            "body": "Venezuela does not issue tourist or business visas at the airport. You must hold an approved e-visa in your passport (or a printed approval) before boarding your flight.",
        },
        {
            "label": "Embassy of Venezuela in Washington DC has been closed since 2019",
            "body": "US-based applicants cannot file in person in the US. All US applications now run through the online Cancillería Digital portal — there is no consulate appointment to book.",
        },
    ],
    "needs_visa_summary": (
        "Most Western Hemisphere passport holders enter Venezuela visa-free "
        "for tourist stays of up to 60–90 days (UK, Canada, EU, Brazil, "
        "Colombia, Russia, UAE). The big exceptions are the United States "
        "and China — both nationalities require a tourist or business "
        "visa in advance. If you're not sure, use the visa-requirements "
        "checker."
    ),
    "documents": SHARED_DOCUMENT_CHECKLIST,
    "steps_intro": (
        "The application is done entirely online through Venezuela's "
        "Cancillería Digital portal. The interface is in Spanish only — "
        "budget about 60–90 minutes for the form itself, plus time to "
        "gather documents in advance."
    ),
    "steps": SHARED_EVISA_STEPS,
    "fees": [
        {"label": f"E-visa fee (TR-V tourist)", "value": f"USD {EVISA_FEE_USD}", "note": "Up from USD 60 in 2025; paid digitally inside the portal."},
        {"label": f"E-visa fee (TR-N business)", "value": f"USD {EVISA_FEE_USD}", "note": "Same headline fee as tourist; corporate invitation letter required."},
    ],
    "timeline": [
        {"label": "Recommended buffer", "value": "4–6 weeks before departure"},
        {"label": "Typical approval", "value": "≈ 15 days from submission"},
        {"label": "Real-world range", "value": "7–30 days (Fragomen / US Embassy)"},
    ],
    # Default FAQ — applies on the pillar page. Variants can append.
    "faqs": [
        {
            "q": "Do I need a visa to travel to Venezuela in 2026?",
            "a": "Yes for Americans and Chinese passport holders, no for many others — it depends on your nationality. US and Chinese citizens require a visa in advance and there is no visa on arrival. UK, Canadian, EU (most member states), Brazilian, Colombian, Russian, and UAE passport holders can enter visa-free for tourist stays of 60–90 days, with the exact limit depending on nationality.",
        },
        {
            "q": "How much does a Venezuela visa cost?",
            "a": f"The official Cancillería Digital e-visa fee is USD {EVISA_FEE_USD} for both the tourist (TR-V) and business (TR-N) visa, raised from USD 60 in 2025. The fee is paid digitally inside the portal. Some US-issued cards auto-block payments to Venezuela — pre-clear the transaction with your card issuer.",
        },
        {
            "q": "How long does it take to get a Venezuelan visa?",
            "a": "Approvals through the Cancillería Digital portal typically arrive in around 15 days. Fragomen and the US Embassy report a real-world range of 7–30 days depending on portal queue and document re-submission. Plan to apply at least 4–6 weeks before departure and do not book non-refundable flights before the visa is approved.",
        },
        {
            "q": "Where do US citizens apply for a Venezuelan visa?",
            "a": "Online, through the Cancillería Digital e-visa portal at cancilleriadigital.mppre.gob.ve. The Embassy of Venezuela in Washington DC has been closed since 2019, so there is no US-based consular appointment. The US Embassy in Caracas publishes a plain-English summary of the current process.",
        },
        {
            "q": "Can I apply for a Venezuelan visa on arrival?",
            "a": "No — Venezuela does not issue tourist or business visas at the airport for any nationality that requires a visa. You must hold an approved e-visa (or printed approval) before boarding your flight to Caracas.",
        },
        {
            "q": "What's the difference between a TR-V and TR-N visa?",
            "a": "TR-V is the tourist visa, valid for general leisure travel. TR-N is the business visa, intended for meetings, market research, contract negotiation, and similar activities. The TR-N requires a corporate invitation letter from a Venezuelan entity registered with SENIAT (the Venezuelan tax authority). Both are issued for up to 1 year, multiple-entry, with stays of up to 90 days per entry.",
        },
        {
            "q": "Is the Venezuelan e-visa the same fee for everyone?",
            "a": f"The headline fee is USD {EVISA_FEE_USD} for tourist and business e-visas filed through Cancillería Digital. Chinese applicants typically file at the Embassy of Venezuela in Beijing (or its Shanghai / Hong Kong consulates) and should confirm the current fee directly with the consulate accredited to their province, as in-person filings can carry different administrative costs.",
        },
        {
            "q": "Do I need a visa to travel through Venezuela in transit?",
            "a": "Yes, in most cases — most international transits at SVMI (Caracas) airport require you to clear immigration to change terminals, which means you need a tourist visa if your nationality requires one. Confirm your itinerary with the airline; direct connections to Caracas remain limited and most travelers route via Panama City, Bogotá, or Madrid.",
        },
    ],
    "related_links": [
        {
            "href": "/planilla-de-solicitud-de-visa",
            "label": PLANILLA_HERO_LINE,
            "description": (
                "Short guide plus a link to the type-in sheet: print a Spanish-labelled PDF "
                "for Cancillería Digital."
            ),
        },
        {"href": "/apply-for-venezuelan-visa/declaracion-jurada", "label": "Declaración jurada (sworn statement)", "description": "Pre-filled Spanish text; add your data and a typed cursive-style signature, then print to PDF."},
        {"href": "/tools/venezuela-visa-requirements", "label": "Check visa requirements by passport country", "description": "Interactive checker covering 10 nationalities, with current US travel-advisory level."},
        {"href": "/travel", "label": "Caracas travel hub", "description": "STEP enrolment, the printable Caracas Emergency Card, embassies, hotels, and hospitals."},
        {"href": "/apply-for-venezuelan-visa/us-citizens", "label": "Venezuela visa for US citizens", "description": "US-specific application route — closed DC embassy, e-visa portal, common questions."},
        {"href": "/apply-for-venezuelan-visa/business-visa", "label": "Venezuela business (TR-N) visa", "description": "Corporate invitation letter, SENIAT registration, executive travel guidance."},
        {"href": "/apply-for-venezuelan-visa/china", "label": "Venezuela visa for Chinese citizens", "description": "Beijing embassy and Shanghai / Hong Kong consulate routes."},
    ],
}


# ---------------------------------------------------------------------------
# Variant — /apply-for-venezuelan-visa/us-citizens
# ---------------------------------------------------------------------------
US_CITIZENS: dict = {
    "slug": "us-citizens",
    "page_label": "Venezuela visa for US citizens",
    "h1": "Venezuela Visa for US Citizens (2026)",
    "kicker": "US passport · Updated for the e-visa era",
    "lede": (
        "US citizens now apply for Venezuelan tourist (TR-V) and business "
        "(TR-N) visas online through the Cancillería Digital portal — the "
        "Venezuelan embassy in Washington DC has been closed since 2019, "
        "so there is no US consular appointment to book. This page walks "
        "through the US-specific application: portal, fee, timeline, and "
        "the common payment and documentation snags."
    ),
    "key_facts": [
        {"label": "Visa class", "value": "TR-V (tourism) or TR-N (business), per your case"},
        {
            "label": "Entry pattern",
            "value": "Up to 1 year, multiple-entry visa — 90 days per visit",
        },
    ],
    "intro_warnings": [
        {
            "label": "Embassy of Venezuela in Washington DC is closed",
            "body": "Do not search for a US-based consulate. Since 2019 the Venezuelan diplomatic mission in DC has been shut. All US applications now flow through the online Cancillería Digital portal.",
        },
        {
            "label": "Travel advisory: US Department of State currently rates Venezuela Level 3 (Reconsider Travel)",
            "body": "Level 4 (Do Not Travel) still applies to the Colombia border region (Amazonas, Apure, Aragua outside Maracay, rural Bolívar, Guárico, Táchira). Read the State Department's full advisory before booking.",
        },
        {
            "label": "Card payments to Venezuela are routinely auto-blocked",
            "body": "Several US issuers (Chase, AmEx, Capital One) automatically decline international transactions to Venezuela. Call your card issuer before paying the e-visa fee and ask for a temporary one-time exception.",
        },
    ],
    "needs_visa_summary": (
        "Yes — every US passport holder needs a Venezuelan visa in advance, "
        "regardless of length of stay. Visas are not issued at the airport, "
        "and there is no separate ESTA-style waiver. The two relevant "
        "categories for most US travelers are the tourist (TR-V) and "
        "business (TR-N) visa."
    ),
    "documents": SHARED_DOCUMENT_CHECKLIST + [
        {
            "label": "US Embassy guide (read it first)",
            "detail": "The US Embassy in Caracas maintains an English-language summary of the current process. Read it before you open the Spanish-only portal.",
        },
    ],
    "steps_intro": (
        "Filing is done entirely online through Cancillería Digital. The "
        "Venezuelan embassy in Washington DC is closed, so there is no "
        "US-based appointment to book. Budget 60–90 minutes for the form, "
        "plus several days to gather documents in advance."
    ),
    "steps": SHARED_EVISA_STEPS,
    "fees": [
        {"label": "E-visa fee (TR-V tourist)", "value": f"USD {EVISA_FEE_USD}", "note": "Raised from USD 60 in 2025. Paid digitally inside the portal."},
        {"label": "E-visa fee (TR-N business)", "value": f"USD {EVISA_FEE_USD}", "note": "Same headline fee. Requires a Venezuelan corporate invitation letter."},
    ],
    "timeline": [
        {"label": "Recommended buffer", "value": "4–6 weeks before departure"},
        {"label": "Typical approval", "value": "≈ 15 days from submission"},
        {"label": "Real-world range", "value": "7–30 days (Fragomen / US Embassy)"},
    ],
    "faqs": [
        {
            "q": "Where do US citizens apply for a Venezuelan visa?",
            "a": "Online, through the Cancillería Digital e-visa portal at cancilleriadigital.mppre.gob.ve. The Embassy of Venezuela in Washington DC has been closed since 2019, so there is no US-based consular appointment. The US Embassy in Caracas publishes an English-language summary of the current e-visa process at ve.usembassy.gov.",
        },
        {
            "q": "How much does the Venezuelan e-visa cost for US citizens?",
            "a": f"The official Cancillería Digital e-visa fee is USD {EVISA_FEE_USD} for both the tourist (TR-V) and business (TR-N) visa, raised from USD 60 in 2025. Some US-issued cards (Chase, AmEx, Capital One) auto-block payments to Venezuela — pre-clear the transaction with your card issuer before submitting.",
        },
        {
            "q": "How long does it take US citizens to get a Venezuelan visa?",
            "a": "Approvals through Cancillería Digital typically arrive in around 15 days, with a real-world range of 7–30 days reported by Fragomen and the US Embassy in Caracas. Apply at least 4–6 weeks before departure and do not book non-refundable flights before the visa is in hand.",
        },
        {
            "q": "Is the Embassy of Venezuela in Washington DC open?",
            "a": "No — the Embassy of Venezuela in Washington DC has been closed since 2019, when the US recognized Juan Guaidó as interim president and the Maduro-aligned diplomats were expelled. The full diplomatic mission has not reopened. US-citizen visa applications are processed online through Cancillería Digital, not in person.",
        },
        {
            "q": "Can US citizens travel to Venezuela right now?",
            "a": "Yes, with caveats — as of March 19, 2026 the US State Department downgraded Venezuela from Level 4 (Do Not Travel) to Level 3 (Reconsider Travel), with Level 4 still applying to specific border states. The US Embassy in Caracas formally reopened on March 30, 2026. Travel is legal but requires careful planning, an approved e-visa, and comprehensive medical-evacuation insurance.",
        },
        {
            "q": "What's the difference between TR-V and TR-N for US passport holders?",
            "a": "TR-V is the tourist visa — for leisure, family visits, and general travel. TR-N is the business visa — for meetings, market research, contract negotiation, and similar activities. Both are issued for up to 1 year multiple-entry with stays of up to 90 days per entry. The TR-N additionally requires a corporate invitation letter from a Venezuelan entity registered with SENIAT.",
        },
        {
            "q": "Do US citizens need a visa to transit through Caracas?",
            "a": "Yes — a US passport holder needs an approved e-visa even for transit, because most international connections at SVMI airport require clearing immigration to change terminals. Direct US–Venezuela commercial flights remain suspended; most travelers route via Panama City, Bogotá, or Madrid.",
        },
    ],
    "related_links": [
        {"href": "/apply-for-venezuelan-visa", "label": "Main visa application guide", "description": "The full 6-step e-visa playbook, fees, and timeline for all nationalities."},
        {"href": "/apply-for-venezuelan-visa/business-visa", "label": "Venezuela business (TR-N) visa", "description": "Corporate invitation letter, SENIAT registration, executive travel guidance."},
        {"href": "/tools/venezuela-visa-requirements", "label": "Visa-requirements checker", "description": "Compare US visa rules with other nationalities side-by-side."},
        {"href": "/travel", "label": "Caracas travel hub", "description": "STEP enrolment, printable Caracas Emergency Card, embassies, hotels."},
    ],
}


# ---------------------------------------------------------------------------
# Variant — /apply-for-venezuelan-visa/business-visa
# ---------------------------------------------------------------------------
BUSINESS_VISA: dict = {
    "slug": "business-visa",
    "page_label": "Venezuela business (TR-N) visa",
    "h1": "Venezuela Business Visa (TR-N): Application Guide for 2026",
    "kicker": "TR-N business visa · For executives, investors, and consultants",
    "lede": (
        "The TR-N business visa is the right category for executives, "
        "investors, and consultants traveling to Venezuela for meetings, "
        "market research, contract negotiation, or due diligence. Filing "
        "runs through the same Cancillería Digital e-visa portal as the "
        "tourist (TR-V) visa, but the TR-N additionally requires a "
        "corporate invitation letter from a Venezuelan entity registered "
        "with SENIAT — the documentation that most often delays first-"
        "time applicants."
    ),
    "key_facts": [
        {"label": "Visa class", "value": "TR-N (Negocios) — not the tourist (TR-V) class"},
        {
            "label": "Entry pattern",
            "value": "Same as TR-V: up to 1 year mult.-entry, 90 days per visit",
        },
    ],
    "intro_warnings": [
        {
            "label": "Corporate invitation letter is the long-pole item",
            "body": "Without a SENIAT-registered Venezuelan entity sponsoring the trip, the TR-N application will not be approved. Start the invitation-letter process before you open the Cancillería Digital portal.",
        },
        {
            "label": "Sanctions due diligence applies",
            "body": "If you are a US person (or a non-US person dealing with US-origin goods, services, or USD payments), screen your Venezuelan counterparty against OFAC's SDN list before signing any contract. The TR-N visa does not pre-clear sanctions exposure.",
        },
        {
            "label": "Most sophisticated investors keep negotiations in third-country jurisdictions",
            "body": "Bogotá, Panama City, Madrid, and Dubai remain the preferred neutral venues for primary commercial discussions. Use the TR-N for in-country execution, site visits, and signing — not for the first round of conversation.",
        },
    ],
    "needs_visa_summary": (
        "Yes — every US, Chinese, and most non-Western Hemisphere passport "
        "holder needs a TR-N visa in advance for business travel. Even "
        "nationalities that enter visa-free for tourism (UK, Canada, EU, "
        "Brazil, Colombia, Russia, UAE) should apply for a TR-N if they "
        "plan to conduct meetings, sign contracts, or earn income while "
        "in country, to avoid an immigration enforcement issue on exit."
    ),
    "documents": SHARED_DOCUMENT_CHECKLIST + [
        {
            "label": "Corporate invitation letter (REQUIRED for TR-N)",
            "detail": "Issued on the Venezuelan host entity's letterhead, signed by an authorized representative, and addressed to the Venezuelan consul. Must reference the host's SENIAT (RIF) registration number, the purpose of the visit, the dates of travel, and a commitment to cover or share trip costs.",
        },
        {
            "label": "Letter from your employer",
            "detail": "On company letterhead, confirming your role, the business purpose of the trip, and that you will return to your post after the visit.",
        },
        {
            "label": "SENIAT registration evidence (from the host)",
            "detail": "A copy of the Venezuelan host's RIF (Registro de Información Fiscal) certificate, demonstrating active SENIAT registration.",
        },
    ],
    "steps_intro": (
        "The TR-N flow is identical to the tourist visa with one critical "
        "addition: the corporate invitation letter from a SENIAT-registered "
        "Venezuelan entity. Start that workflow with your host first, then "
        "open the Cancillería Digital portal once the letter is in hand."
    ),
    "steps": [
        {
            "title": "Secure a corporate invitation letter from your Venezuelan host",
            "detail": (
                "Begin with your Venezuelan counterparty. The letter must "
                "be on host-entity letterhead, signed by an authorized "
                "officer, addressed to the Venezuelan consul, and reference "
                "the host's SENIAT RIF number, the purpose of the visit, "
                "and the dates of travel."
            ),
        },
        {
            "title": "Watch the instructional video",
            "detail": (
                "The MPPRE screen recording walks through the same request "
                "flow you will use for a TR-N filing. Watch it before you "
                "register so you know where business documents attach in the "
                "portal."
            ),
            "url": MPPRE_EVISA_REQUEST_PROCESS_VIDEO,
            "url_label": "Watch the instructional video",
        },
        {
            "title": "Register on Cancillería Digital",
            "detail": (
                "Create an account on Venezuela's official MPPRE e-visa "
                "portal. The interface is in Spanish only — use a "
                "translator. Click \"regístrate\" on the login screen."
            ),
            "url": "https://cancilleriadigital.mppre.gob.ve/",
            "url_label": "Open the e-visa portal",
        },
        {
            "title": "Select \"Visa de Negocios (TR-N)\" and complete the form",
            "detail": (
                "Choose the business visa class, not the tourist class. "
                "Fill in personal, passport, travel, and financial "
                "information. The form will reference the host entity's "
                "details from your invitation letter."
            ),
        },
        {
            "title": "Upload supporting documents",
            "detail": (
                "Upload digital scans of your passport (6+ months validity, "
                "2 blank pages), passport photo, hotel reservation or "
                "host-arranged accommodation, return-itinerary, proof of "
                "funds, employer letter, and the corporate invitation "
                "letter from your Venezuelan host."
            ),
        },
        {
            "title": f"Pay the USD {EVISA_FEE_USD} fee through the portal",
            "detail": (
                "Same fee as the tourist visa. Paid digitally inside the "
                "portal. Pre-clear the transaction with your card issuer "
                "if it is US-issued."
            ),
        },
        {
            "title": "Wait for approval and download your e-visa",
            "detail": (
                "Approval typically arrives in around 15 days through the "
                "portal. Print the approved e-visa, present it with your "
                "passport at SVMI airport on arrival, and carry the "
                "corporate invitation letter as a paper backup in case "
                "secondary inspection asks about the purpose of the visit."
            ),
        },
    ],
    "fees": [
        {"label": "E-visa fee (TR-N business)", "value": f"USD {EVISA_FEE_USD}", "note": "Raised from USD 60 in 2025. Paid digitally inside the portal."},
        {"label": "Corporate invitation letter", "value": "No fee", "note": "Issued by the Venezuelan host. Notarization is sometimes requested by the host's legal team."},
        {"label": "Document translation", "value": "Variable", "note": "Some hosts ask for a Spanish translation of the employer letter. Budget USD 50–150 if you do not have in-house Spanish."},
    ],
    "timeline": [
        {"label": "Invitation letter", "value": "1–3 weeks (host-dependent)"},
        {"label": "Recommended buffer", "value": "6–8 weeks before departure"},
        {"label": "Typical approval", "value": "≈ 15 days from submission"},
        {"label": "Real-world range", "value": "7–30 days (Fragomen / US Embassy)"},
    ],
    "faqs": [
        {
            "q": "What is the Venezuelan TR-N business visa?",
            "a": "TR-N is Venezuela's business-visitor visa class. It authorizes activities like commercial meetings, market research, contract negotiation, due diligence, and signing — but not local employment. Issued for up to 1 year multiple-entry with stays of up to 90 days per entry. Application is filed online through Cancillería Digital.",
        },
        {
            "q": "How is the TR-N different from the TR-V tourist visa?",
            "a": "The two visas share the same headline fee (USD 180), the same validity window (up to 1 year multiple-entry), and the same maximum stay (90 days per entry). The TR-N additionally requires a corporate invitation letter from a Venezuelan entity registered with SENIAT, plus a letter from your employer confirming the business purpose. The TR-V does not require either.",
        },
        {
            "q": "Do I need a separate Venezuelan work visa to attend meetings?",
            "a": "No — attending meetings, conducting due diligence, and negotiating contracts are squarely within the TR-N business-visitor scope. A separate work visa (laboral) is only required if you intend to take up local Venezuelan employment or be paid by a Venezuelan entity.",
        },
        {
            "q": "What does the corporate invitation letter need to include?",
            "a": "It should be on the Venezuelan host entity's letterhead, signed by an authorized officer, and addressed to the Venezuelan consul. The letter must reference the host's SENIAT (RIF) registration number, describe the purpose of your visit, list the dates of travel, and confirm that the host either covers or shares trip costs.",
        },
        {
            "q": "Can my Venezuelan host issue the invitation letter without SENIAT registration?",
            "a": "No — the SENIAT (RIF) number is a hard requirement. If your counterparty cannot produce active SENIAT registration, treat that as a red flag for both the visa application and the underlying commercial relationship. SENIAT registration is the most basic indicator that a Venezuelan entity is operating in good standing.",
        },
        {
            "q": "Is OFAC compliance handled inside the visa process?",
            "a": "No — the Venezuelan visa process is independent of US sanctions enforcement. If you are a US person (or a non-US person dealing with US-origin goods, services, or USD payments), screen your Venezuelan counterparty against OFAC's SDN list independently before signing any contract. The TR-N visa does not pre-clear sanctions exposure in either direction.",
        },
        {
            "q": "Should I run business meetings in Venezuela or in a third country?",
            "a": "No — run primary commercial discussions outside Venezuela. Sophisticated investors typically use Bogotá, Panama City, Madrid, or Dubai for the negotiating phase, and reserve the TR-N for in-country execution, site visits, and signing. This pattern reduces both legal-process and physical-security exposure.",
        },
    ],
    "related_links": [
        {"href": "/apply-for-venezuelan-visa", "label": "Main visa application guide", "description": "The full 6-step e-visa playbook for tourist and business visas."},
        {"href": "/apply-for-venezuelan-visa/us-citizens", "label": "Venezuela visa for US citizens", "description": "US-specific application route — closed DC embassy, e-visa portal, payment snags."},
        {"href": "/sanctions-tracker", "label": "OFAC Venezuela sanctions tracker", "description": "Screen your counterparty against the current SDN list before signing."},
        {"href": "/invest-in-venezuela", "label": "Invest in Venezuela", "description": "Sector-by-sector view of where post-sanctions capital is moving."},
    ],
}


# ---------------------------------------------------------------------------
# Variant — /apply-for-venezuelan-visa/china
# ---------------------------------------------------------------------------
CHINA_CITIZENS: dict = {
    "slug": "china",
    "page_label": "Venezuela visa for Chinese citizens",
    "h1": "Venezuela Visa for Chinese Citizens (2026)",
    "kicker": "Chinese passport · Beijing, Shanghai, Hong Kong consular routes",
    "lede": (
        "Chinese passport holders apply for Venezuelan tourist (L), "
        "business (F), or investor visas through the Embassy of Venezuela "
        "in Beijing — or its consulates in Shanghai and Hong Kong. The "
        "online Cancillería Digital portal is also available for many "
        "applications, with an ≈ 15-day approval window. Confirm the "
        "channel with the consulate accredited to your province before "
        "filing."
    ),
    "key_facts": [
        {
            "label": "In-person consular network",
            "value": "Embassy in Beijing; consulates in Shanghai & Hong Kong (confirm which serves your province).",
        },
        {
            "label": "Visa terms (varies by class)",
            "value": "30–90 days, multiple-entry available — your mission confirms length.",
        },
    ],
    "intro_warnings": [
        {
            "label": "Confirm the channel with your accredited consulate",
            "body": "Channel rules can vary by province. Some applicants are routed through the online Cancillería Digital portal; others are required to file in person at Beijing, Shanghai, or Hong Kong. Confirm before paying any fees.",
        },
        {
            "label": "Investor and long-stay business visas require a SENIAT-registered Venezuelan host",
            "body": "Just like the TR-N business visa for other nationalities, the Chinese F-class and investor classifications require a corporate invitation letter from a Venezuelan entity registered with SENIAT.",
        },
    ],
    "needs_visa_summary": (
        "Yes — every Chinese passport holder needs a Venezuelan visa in "
        "advance. Visas are not issued on arrival. Tourist (L), business "
        "(F), and investor classifications are all available; the choice "
        "depends on the purpose and length of your stay."
    ),
    "documents": [
        {
            "label": "Valid passport",
            "detail": "Chinese passport with at least 6 months validity beyond your planned date of entry, and 2 blank pages.",
        },
        {
            "label": "Passport-style photo",
            "detail": "Recent (within the last 6 months), white background. Confirm the consulate's specific size and format requirement.",
        },
        {
            "label": "Round-trip itinerary",
            "detail": "Confirmed flight reservation. Direct China–Caracas service is limited; most itineraries route through Madrid or Doha.",
        },
        {
            "label": "Hotel reservation or invitation letter",
            "detail": "Hotel reservation for tourists; corporate invitation letter from a SENIAT-registered Venezuelan entity for business and investor visas.",
        },
        {
            "label": "Visa application form",
            "detail": "Available from the Embassy of Venezuela in China website, or completed inside the Cancillería Digital portal.",
        },
        {
            "label": f"{PLANILLA_HERO_LINE} (MPPRE application form)",
            "detail": (
                "Structured ministry form, usually attached as a PDF. Fill and "
                "Print → Save as PDF, then upload in the "
                "portal or email as your consulate instructs."
            ),
            "cta": {"label": "Complete now", "href": "/apply-for-venezuelan-visa/planilla"},
        },
        {
            "label": "Declaración jurada (sworn statement)",
            "detail": (
                "Standard Spanish sworn statement. Pre-fill, sign (typed cursive for PDF or wet "
                "signature after printing), and upload."
            ),
            "cta": {"label": "Complete now", "href": "/apply-for-venezuelan-visa/declaracion-jurada"},
        },
    ],
    "steps_intro": (
        "Two channels are available — the Beijing/Shanghai/Hong Kong "
        "consular network, and the Cancillería Digital online portal. "
        "Confirm with the consulate accredited to your province which "
        "channel they currently expect, then follow the relevant track."
    ),
    "steps": [
        {
            "title": "Open the Embassy of Venezuela in China website",
            "detail": (
                "The Beijing mission is the canonical entry point, with "
                "consulates in Shanghai and Hong Kong serving applicants "
                "in southern and special-administrative-region provinces. "
                "Confirm the consulate accredited to your province."
            ),
            "url": "https://china.embajada.gob.ve/",
            "url_label": "Embassy of Venezuela in China",
        },
        {
            "title": "Choose your visa class",
            "detail": (
                "Tourist (L) for leisure travel, Business (F) for "
                "commercial visits, or Investor classifications for "
                "long-stay capital deployment. Investor and long-stay F "
                "visas require a corporate invitation letter from a "
                "SENIAT-registered Venezuelan entity."
            ),
        },
        {
            "title": "Confirm channel — online or in-person",
            "detail": (
                "Many tourist and business applications are now filed "
                "through Cancillería Digital. Investor and long-stay "
                "categories may still require in-person filing. Confirm "
                "with the consulate before you pay any fees."
            ),
            "url": "https://cancilleriadigital.mppre.gob.ve/",
            "url_label": "Open the e-visa portal",
        },
        {
            "title": "Submit application & supporting documents",
            "detail": (
                "Standard documents: valid passport (6+ months), passport "
                "photo, completed application form, hotel or invitation "
                "letter, round-trip itinerary, and proof of funds. "
                "Investor and business categories add the corporate "
                "invitation letter and SENIAT RIF certificate."
            ),
        },
        {
            "title": "Pay the visa fee",
            "detail": (
                "Fee varies by visa class and channel. Online filings "
                "follow the Cancillería Digital fee schedule (USD 180 for "
                "tourist and business e-visas as of 2026). Confirm "
                "in-person fees directly with the consulate."
            ),
        },
        {
            "title": "Collect or download your visa",
            "detail": (
                "Online approvals are delivered through the Cancillería "
                "Digital portal — typically in around 15 days. In-person "
                "filings can take up to 6 weeks. Print the approved visa "
                "and bring it with you."
            ),
        },
    ],
    "fees": [
        {"label": "E-visa fee (tourist / business)", "value": f"USD {EVISA_FEE_USD}", "note": "Online filings via Cancillería Digital. Confirm at submission."},
        {"label": "Investor / long-stay classifications", "value": "Confirm with consulate", "note": "Fees vary by visa class and processing channel."},
    ],
    "timeline": [
        {"label": "Online (Cancillería Digital)", "value": "≈ 15 days"},
        {"label": "In-person (Beijing / Shanghai / Hong Kong)", "value": "Up to 6 weeks"},
        {"label": "Recommended buffer", "value": "6–8 weeks before departure"},
    ],
    "faqs": [
        {
            "q": "Do Chinese citizens need a visa for Venezuela?",
            "a": "Yes — every Chinese passport holder needs a Venezuelan visa in advance, and there is no visa on arrival. Tourist (L), business (F), and investor classifications are all available, with the choice depending on the purpose and length of your stay.",
        },
        {
            "q": "Where do Chinese citizens apply for a Venezuelan visa?",
            "a": "At the Embassy of Venezuela in Beijing or its consulates in Shanghai and Hong Kong. Many tourist and business applications can also be filed online through the Cancillería Digital e-visa portal. Confirm the channel currently expected with the consulate accredited to your province.",
        },
        {
            "q": "How long does a Venezuelan visa take for Chinese applicants?",
            "a": "Online applications through Cancillería Digital typically clear in around 15 days. Legacy in-person filings at the Beijing embassy or its consulates can take up to 6 weeks. Plan for a 6–8 week pre-departure buffer to absorb document re-submission cycles.",
        },
        {
            "q": "What documents do Chinese citizens need for a Venezuelan visa?",
            "a": "A valid passport (6+ months validity, 2 blank pages), passport-style photo, completed application form, round-trip itinerary, hotel reservation or invitation letter, and proof of funds. Business and investor categories additionally require a corporate invitation letter from a SENIAT-registered Venezuelan entity and the host's RIF certificate.",
        },
        {
            "q": "How much does a Venezuelan visa cost for Chinese citizens?",
            "a": f"The official Cancillería Digital e-visa fee is USD {EVISA_FEE_USD} for tourist and business filings. Investor and long-stay classifications, and any in-person filings at the Beijing/Shanghai/Hong Kong consulates, may carry different administrative costs — confirm with the consulate accredited to your province before submitting.",
        },
        {
            "q": "Are there direct flights from China to Venezuela?",
            "a": "No — there is no nonstop China–Caracas commercial service, and direct flights are limited. Most itineraries route through Madrid (Iberia) or Doha (Qatar Airways), with onward connection to SVMI airport in Caracas. Confirm onward connections with the airline before booking.",
        },
    ],
    "related_links": [
        {"href": "/apply-for-venezuelan-visa", "label": "Main visa application guide", "description": "The full 6-step e-visa playbook for all nationalities."},
        {"href": "/apply-for-venezuelan-visa/business-visa", "label": "Venezuela business visa", "description": "Corporate invitation letter, SENIAT registration, executive guidance."},
        {"href": "/tools/venezuela-visa-requirements", "label": "Visa-requirements checker", "description": "Compare Chinese rules with other nationalities side-by-side."},
        {"href": "/invest-in-venezuela", "label": "Invest in Venezuela", "description": "Sector view — Chinese SOEs hold one of the largest foreign investment portfolios in Venezuela."},
    ],
}


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------
VARIANTS: dict[str, dict] = {
    "us-citizens": US_CITIZENS,
    "business-visa": BUSINESS_VISA,
    "china": CHINA_CITIZENS,
}


def get_pillar() -> dict:
    return PILLAR


def get_variant(slug: str) -> dict | None:
    return VARIANTS.get(slug)


def list_variant_slugs() -> list[str]:
    return list(VARIANTS.keys())
