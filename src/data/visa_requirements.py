"""
Static curated dataset of Venezuela visa and entry requirements by
passport nationality, plus current US travel-advisory level.

Authoritative sources (verify before publishing changes):
  https://travel.state.gov/content/travel/en/traveladvisories/traveladvisories/venezuela-travel-advisory.html
  https://www.gov.uk/foreign-travel-advice/venezuela
  https://travel.gc.ca/destinations/venezuela
  https://www.eeas.europa.eu/venezuela_en

NOTE: The US row's `advisory_level` and `advisory_summary` are also
overridden at request time by the latest TravelAdvisoryScraper row in
the database (see server.tool_visa_requirements). Keep this dict as
the static fallback in case the scraper hasn't run yet or returns
no result.

Update whenever you confirm a policy change. The tool's UI always
links the user back to the relevant embassy / state-department page.
"""

from __future__ import annotations


# As of March 19, 2026 the US Department of State downgraded Venezuela
# from Level 4 ("Do Not Travel") to Level 3 ("Reconsider Travel"),
# removing the Wrongful Detention / Unrest / Other risk indicators
# while keeping Level 4 designations on specific border states. This
# is the static baseline; the live page also reads from the
# TravelAdvisoryScraper output and will reflect any further changes
# automatically.
VISA_REQUIREMENTS: list[dict] = [
    {
        "country": "United States",
        "code": "US",
        "visa_required": True,
        "visa_type": "Tourist (TR-V) or Business (TR-N) visa required in advance — visas are NOT available on arrival",
        "visa_validity": "Tourist: up to 1 year multiple-entry; Business: up to 1 year",
        "tourist_stay": "Up to 90 days per entry",
        # Processing time per Fragomen and the US Embassy in Caracas
        # (April 2026): the new Cancillería Digital e-visa typically clears
        # in around 15 days, with reported real-world ranges of 7-30 days
        # depending on portal queue and document re-submission cycles.
        # The pre-e-visa baseline (in-person at a third-country consulate)
        # was approximately six weeks. We surface a 4-6 week pre-departure
        # buffer so investors don't book non-refundable flights early.
        "processing_time_summary": "≈ 15 days (range 7–30 days)",
        "processing_time_detail": "Apply at least 4–6 weeks before departure. The Cancillería Digital e-visa typically clears in around 15 days, but Fragomen and the US Embassy report real-world ranges of 7–30 days depending on portal queue and document re-submission. Do not book non-refundable flights before the visa is in hand.",
        # The Venezuelan consular network's main domain (embajadadevenezuela.org)
        # is no longer resolvable in DNS. Until a stable consular URL exists,
        # point users at the State Department's Venezuela country page, which
        # itself links to current entry-requirement info and routes consular
        # services through the U.S. Embassy in Bogotá.
        "embassy_url": "https://travel.state.gov/content/travel/en/international-travel/International-Travel-Country-Information-Pages/Venezuela.html",
        # Apply via Venezuela's official Cancillería Digital e-visa portal.
        # The Embassy of Venezuela in Washington DC has been closed since
        # 2019, so applications run through the MPPRE e-visa system. The
        # US Embassy in Caracas publishes a plain-English summary of the
        # current process on ve.usembassy.gov.
        "visa_application_url": "https://cancilleriadigital.mppre.gob.ve/",
        "visa_application_guide_url": "https://ve.usembassy.gov/venezuela-electronic-visa-application-process-update/",
        "visa_application_warning": "The Embassy of Venezuela in Washington DC has been closed since 2019. Do not look for a US-based consulate — applications run through Venezuela's online e-visa portal.",
        "visa_application_steps": [
            {
                "title": "Read the US Embassy plain-English summary",
                "detail": "The US Embassy in Caracas maintains an English-language summary of the current Venezuelan e-visa process. Read it once before opening the Spanish-only application portal so you know what you're filling in.",
                "url": "https://ve.usembassy.gov/venezuela-electronic-visa-application-process-update/",
                "url_label": "US Embassy guide",
            },
            {
                "title": "Register on Cancillería Digital",
                "detail": "Create an account on Venezuela's official Ministry of Foreign Affairs (MPPRE) e-visa portal. The interface is in Spanish — use a translator if needed. Click \"regístrate\" on the login screen.",
                "url": "https://cancilleriadigital.mppre.gob.ve/",
                "url_label": "Open the portal",
            },
            {
                "title": "Complete the electronic application form",
                "detail": "Fill in personal, passport, travel, and financial information. You will be asked which visa type you want (Tourist TR-V or Business TR-N) and your intended dates of travel.",
            },
            {
                "title": "Upload supporting documents",
                "detail": "You will need a digital scan of: (1) a valid passport with at least 6 months validity and 2 blank pages, (2) a passport-sized photo, (3) hotel reservation or invitation letter, (4) round-trip flight itinerary, and (5) proof of funds. Business visas additionally require a corporate invitation letter from a Venezuelan entity.",
            },
            {
                "title": "Pay the visa fee through the portal",
                "detail": "The fee is paid digitally inside the portal. Amount and accepted payment methods are confirmed at submission — confirm with your card issuer that international payments to Venezuela will not be auto-blocked.",
            },
            {
                "title": "Wait for approval and download your e-visa",
                "detail": "Approval is delivered through the portal. Print the approved visa and bring the printout with you — present it together with your passport at SVMI airport on arrival. Allow several weeks; do not book non-refundable flights before the visa is in hand.",
            },
        ],
        "advisory_level": 3,
        "advisory_summary": "Reconsider Travel — risk of crime, kidnapping, terrorism, and poor health infrastructure. Do Not Travel (Level 4) still applies to the Colombia border region, Amazonas, Apure, Aragua (outside Maracay), rural Bolívar, Guárico, and Táchira states.",
        "advisory_url": "https://travel.state.gov/content/travel/en/traveladvisories/traveladvisories/venezuela-travel-advisory.html",
        "investor_note": "The US Embassy in Caracas formally reopened on March 30, 2026 after a seven-year closure, with Chargé d'Affaires Laura F. Dogu leading the mission. The consular section is still under restoration, so routine passport and visa services continue to be handled by the Venezuela Affairs Unit at US Embassy Bogotá. Plan in-country meetings via local counsel and avoid the four Level-4 border states.",
    },
    {
        "country": "United Kingdom",
        "code": "GB",
        "visa_required": False,
        "visa_type": "Visa-free for stays of up to 90 days",
        "visa_validity": "Tourist entry stamp issued at port of entry",
        "tourist_stay": "Up to 90 days",
        "processing_time_summary": "None — entry stamp at port of entry",
        "processing_time_detail": "No advance approval required. Tourist entry stamp issued at SVMI airport on arrival, valid for up to 90 days. Carry passport (6+ months validity), return ticket, and proof of accommodation — immigration officers may request any of these.",
        "embassy_url": "https://www.gov.uk/foreign-travel-advice/venezuela/entry-requirements",
        "advisory_level": 3,
        "advisory_summary": "FCDO advises against all but essential travel, citing kidnapping risk, political unrest, deteriorating economy, and limited consular assistance.",
        "advisory_url": "https://www.gov.uk/foreign-travel-advice/venezuela",
        "investor_note": "British citizens enter visa-free for up to 90 days. The British Embassy in Caracas operates with limited staff. Comprehensive travel insurance covering medical evacuation is essential — many policies exclude Venezuela.",
    },
    {
        "country": "Canada",
        "code": "CA",
        "visa_required": False,
        "visa_type": "Visa-free for stays of up to 90 days",
        "visa_validity": "Tourist entry stamp issued at port of entry",
        "tourist_stay": "Up to 90 days",
        "processing_time_summary": "None — entry stamp at port of entry",
        "processing_time_detail": "No advance approval required. Tourist entry stamp issued at SVMI airport on arrival, valid for up to 90 days. Carry passport (6+ months validity), return ticket, and proof of accommodation. Note: Canada has no embassy in Caracas — consular services are routed through Bogotá, so plan for limited in-country support.",
        "embassy_url": "https://travel.gc.ca/destinations/venezuela",
        "advisory_level": 4,
        "advisory_summary": "Avoid all travel due to high crime rates, civil unrest, hostage-taking risk, severe shortages of medicines and food, and the absence of consular services.",
        "advisory_url": "https://travel.gc.ca/destinations/venezuela",
        "investor_note": "Canadian citizens enter visa-free. Canada has no embassy in Caracas; consular services are provided from Bogotá. Banking access is constrained by sanctions and OFAC compliance practices of correspondent banks.",
    },
    {
        "country": "Brazil",
        "code": "BR",
        "visa_required": False,
        "visa_type": "Visa-free for stays of up to 60 days",
        "visa_validity": "Tourist entry stamp at port of entry",
        "tourist_stay": "Up to 60 days",
        "processing_time_summary": "None — entry stamp at port of entry",
        "processing_time_detail": "No advance approval required. Tourist entry stamp issued at port of entry, valid for up to 60 days. Carry passport (6+ months validity) and proof of onward travel. Land border crossings (Pacaraima/Santa Elena de Uairén) are open but volatile — flying into SVMI is the safer route.",
        "embassy_url": "https://www.gov.br/mre/pt-br/embaixada-caracas",
        "advisory_level": 2,
        "advisory_summary": "Brazilian government advises caution; consular and economic ties remain active.",
        "advisory_url": "https://www.gov.br/mre/pt-br",
        "investor_note": "Brazilian citizens benefit from visa-free entry and the strongest South American consular presence in Caracas. Land border crossings (Pacaraima/Santa Elena de Uairén) are open but volatile.",
    },
    {
        "country": "Colombia",
        "code": "CO",
        "visa_required": False,
        "visa_type": "Visa-free for stays of up to 90 days",
        "visa_validity": "Tourist entry stamp at port of entry",
        "tourist_stay": "Up to 90 days",
        "processing_time_summary": "None — entry stamp at port of entry",
        "processing_time_detail": "No advance approval required. Tourist entry stamp issued at port of entry, valid for up to 90 days. Cédula (Colombian national ID) is accepted at the land border in addition to passport.",
        "embassy_url": "https://caracas.consulado.gov.co/",
        "advisory_level": 2,
        "advisory_summary": "Colombian government has restored full consular relations as of 2022.",
        "advisory_url": "https://caracas.consulado.gov.co/",
        "investor_note": "With re-opened diplomatic ties, Colombian-Venezuelan border trade is recovering. Cross-border investment via Cúcuta is increasingly viable for goods and services.",
    },
    {
        "country": "European Union (Schengen)",
        "code": "EU",
        "visa_required": False,
        "visa_type": "Visa-free for stays of up to 90 days for most EU passports",
        "visa_validity": "Tourist entry stamp at port of entry",
        "tourist_stay": "Up to 90 days",
        "processing_time_summary": "None — entry stamp at port of entry",
        "processing_time_detail": "No advance approval required for most EU passports. Tourist entry stamp issued at SVMI airport on arrival, valid for up to 90 days. A handful of newer EU member states still require a visa — confirm with your foreign ministry before booking. Carry passport (6+ months validity), return ticket, and proof of accommodation.",
        "embassy_url": "https://www.eeas.europa.eu/venezuela_en",
        "advisory_level": 3,
        "advisory_summary": "EU member states broadly advise caution or avoidance of non-essential travel.",
        "advisory_url": "https://www.eeas.europa.eu/venezuela_en",
        "investor_note": "Most EU citizens enter visa-free. The EU and Spain maintain active diplomatic missions, providing the most consistent European consular footprint. Spanish and Italian investors benefit from cultural and language ties to Caracas business networks.",
    },
    {
        "country": "China",
        "code": "CN",
        "visa_required": True,
        "visa_type": "Visa required (Tourist L, Business F, or Investor classifications)",
        "visa_validity": "30-90 days, multiple-entry options available",
        "tourist_stay": "Per visa terms",
        # Same e-visa portal applies to most nationalities; in-person filing
        # at the Beijing embassy was historically ~6 weeks (Fragomen).
        "processing_time_summary": "≈ 15 days e-visa; up to 6 weeks in-person",
        "processing_time_detail": "The new Cancillería Digital e-visa typically clears in around 15 days. Legacy in-person filings at the Embassy of Venezuela in Beijing (or consulates in Shanghai/Hong Kong) historically took up to six weeks. Apply at least 4–6 weeks before departure, and confirm the channel currently accepted by the consulate accredited to your province.",
        "embassy_url": "http://ve.china-embassy.gov.cn/",
        # Chinese applicants apply through the Embassy of Venezuela in Beijing
        # (or its consulates in Shanghai / Hong Kong). china.embajada.gob.ve
        # is the official mission site.
        "visa_application_url": "https://china.embajada.gob.ve/",
        "visa_application_steps": [
            {
                "title": "Open the Embassy of Venezuela in China website",
                "detail": "The Beijing mission, with consulates in Shanghai and Hong Kong, is the canonical entry point for Chinese applicants. Use it to confirm the consulate accredited to your province.",
                "url": "https://china.embajada.gob.ve/",
                "url_label": "Embassy site",
            },
            {
                "title": "Choose your visa class",
                "detail": "Tourist (L), Business (F), or Investor classifications are available. Investor and long-stay business visas typically require a corporate invitation letter from a Venezuelan entity registered with SENIAT.",
            },
            {
                "title": "Submit application & supporting documents at the consulate",
                "detail": "Standard documents: valid passport (6+ months), passport photo, completed application form, hotel or invitation letter, round-trip itinerary, and proof of funds. Confirm fees and processing time with the specific consulate before filing.",
            },
        ],
        "advisory_level": 2,
        "advisory_summary": "Chinese government maintains full diplomatic and economic relations.",
        "advisory_url": "http://ve.china-embassy.gov.cn/",
        "investor_note": "Despite visa requirements, Chinese investors operate one of the largest foreign investment portfolios in Venezuela, particularly in oil & gas, mining, and infrastructure. Bilateral trade arrangements smooth FX repatriation friction for Chinese SOEs.",
    },
    {
        "country": "Russia",
        "code": "RU",
        "visa_required": False,
        "visa_type": "Visa-free for stays of up to 90 days",
        "visa_validity": "Tourist entry stamp at port of entry",
        "tourist_stay": "Up to 90 days",
        "processing_time_summary": "None — entry stamp at port of entry",
        "processing_time_detail": "No advance approval required. Tourist entry stamp issued at SVMI airport on arrival, valid for up to 90 days. Carry passport (6+ months validity) and return ticket.",
        "embassy_url": "https://venezuela.mid.ru/",
        "advisory_level": 2,
        "advisory_summary": "Russian government maintains a strategic relationship with Caracas.",
        "advisory_url": "https://venezuela.mid.ru/",
        "investor_note": "Russian citizens enter visa-free. Strategic energy and military cooperation creates pathways for Russian investors not available to Western counterparts, but secondary-sanctions risk for any non-Russian co-investor is acute.",
    },
    {
        "country": "United Arab Emirates",
        "code": "AE",
        "visa_required": False,
        "visa_type": "Visa-free for stays of up to 90 days",
        "visa_validity": "Tourist entry stamp at port of entry",
        "tourist_stay": "Up to 90 days",
        "processing_time_summary": "None — entry stamp at port of entry",
        "processing_time_detail": "No advance approval required. Tourist entry stamp issued at SVMI airport on arrival, valid for up to 90 days. Carry passport (6+ months validity), return ticket, and proof of accommodation.",
        "embassy_url": "https://www.mofa.gov.ae/en/missions/uae-missions-abroad",
        "advisory_level": 2,
        "advisory_summary": "UAE government maintains diplomatic and trade relations.",
        "advisory_url": "https://www.mofa.gov.ae/en/missions/uae-missions-abroad",
        "investor_note": "UAE citizens enter visa-free. Dubai has emerged as a meaningful intermediation hub for Venezuelan-related trade and asset structuring, particularly post-2022.",
    },
    {
        "country": "Other (please confirm with embassy)",
        "code": "OTHER",
        "visa_required": True,
        "visa_type": "Varies by nationality",
        "visa_validity": "Confirm with the nearest Venezuelan embassy",
        "tourist_stay": "Varies",
        "processing_time_summary": "Varies — typically 2–6 weeks",
        "processing_time_detail": "Online e-visa filings via Cancillería Digital typically clear in 2–4 weeks for most nationalities; in-person filings at certain consulates can take up to 6 weeks. Apply at least 4–6 weeks before departure and confirm the current channel and timeline with the Venezuelan diplomatic mission accredited to your country.",
        # Venezuela's foreign-affairs ministry (Cancillería) site is the
        # canonical pointer to the consular network; it works while
        # embajadadevenezuela.org no longer resolves.
        "embassy_url": "https://mppre.gob.ve/",
        # The official MPPRE visa service page lists the consular network
        # and the Cancillería Digital e-visa portal handles online filings
        # for most nationalities.
        "visa_application_url": "https://cancilleriadigital.mppre.gob.ve/",
        "visa_application_steps": [
            {
                "title": "Identify the Venezuelan mission accredited to your country",
                "detail": "The MPPRE consular directory lists current Venezuelan embassies and consulates. Many nationalities can file fully online; others must complete biometrics at a specific mission.",
                "url": "https://mppre.gob.ve/detalles_servicio/1",
                "url_label": "MPPRE visa info",
            },
            {
                "title": "Register on Cancillería Digital",
                "detail": "Venezuela's official online visa portal. Click \"regístrate\" on the login page to create an account, then complete the application form.",
                "url": "https://cancilleriadigital.mppre.gob.ve/",
                "url_label": "Open the portal",
            },
            {
                "title": "Upload documents and pay the fee",
                "detail": "Standard requirements: valid passport (6+ months, 2 blank pages), digital photo, hotel or invitation letter, round-trip itinerary, and proof of funds. Fee is paid digitally inside the portal.",
            },
        ],
        "advisory_level": None,
        "advisory_summary": "Check your home country's foreign affairs ministry for the current advisory level.",
        "advisory_url": "https://mppre.gob.ve/",
        "investor_note": "Always confirm visa status, validity, and the current published advisory level with both the Venezuelan diplomatic mission in your country and your home country's foreign affairs ministry before booking travel.",
    },
]


def list_visa_requirements() -> list[dict]:
    return VISA_REQUIREMENTS
