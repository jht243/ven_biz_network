"""Static seed data for the Caracas Research Real Estate vertical.

The first release ships with a small normalized sample listing set. The module
shape is built so a future data feed can replace LISTINGS without changing the
route or template layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median


METHODOLOGY = (
    "Market figures are based on sampled public listings and should be treated "
    "as directional, not definitive appraisals."
)

GENERAL_SOURCE_NOTES = (
    {
        "label": "U.S. State Department Venezuela Travel Advisory",
        "url": "https://travel.state.gov/content/travel/en/traveladvisories/traveladvisories/venezuela-travel-advisory.html",
        "note": "Current U.S. government risk context for crime, kidnapping, health infrastructure, and regional no-travel areas.",
    },
    {
        "label": "OFAC Venezuela-related sanctions program",
        "url": "https://ofac.treasury.gov/sanctions-programs-and-country-information/venezuela-related-sanctions",
        "note": "Primary U.S. sanctions reference for counterparty screening and licensing questions.",
    },
    {
        "label": "World Bank Doing Business archive: Registering Property",
        "url": "https://archive.doingbusiness.org/en/data/exploreeconomies/venezuela",
        "note": "Archived benchmark for property-registration steps, time, cost, and land-administration quality.",
    },
)

CANADA_SOURCE_NOTES = (
    {
        "label": "Government of Canada travel advice for Venezuela",
        "url": "https://travel.gc.ca/destinations/venezuela",
        "note": "Canada's official travel-risk view for Canadian citizens and residents.",
    },
    {
        "label": "Canadian sanctions related to Venezuela",
        "url": "https://www.international.gc.ca/world-monde/international_relations-relations_internationales/sanctions/venezuela.aspx?lang=eng",
        "note": "Canadian sanctions page covering asset freezes, financial prohibitions, permit authority, and recent amendments.",
    },
)

LEGAL_SOURCE_NOTES = (
    {
        "label": "Multilaw Real Estate Guide: Venezuela",
        "url": "https://multilaw.com/Multilaw/Multilaw/RealEstate/Real_Estate_Guide_Venezuela.aspx",
        "note": "Law-firm guide noting foreign ownership is generally possible, with SAREN registration/verification requirements for foreigners.",
    },
    {
        "label": "World Bank Registering Property methodology",
        "url": "https://archive.doingbusiness.org/en/methodology/registering-property",
        "note": "Explains how property-transfer procedures, timing, cost, and land-administration quality are measured.",
    },
)


@dataclass(frozen=True)
class Listing:
    slug: str
    title: str
    city: str
    city_slug: str
    neighborhood: str
    property_type: str
    transaction: str
    price_usd: int
    square_meters: int
    bedrooms: int
    bathrooms: int
    parking: int | None
    source_label: str
    source_url: str
    original_spanish: str
    english_summary: str
    main_image: str
    first_seen: str
    last_seen: str
    quality_score: int

    @property
    def price_per_m2(self) -> int:
        return round(self.price_usd / self.square_meters)


LISTINGS: tuple[Listing, ...] = (
    Listing(
        slug="caracas-altamira-2-bedroom-apartment-185000",
        title="Altamira 2-Bedroom Apartment Near Avenida Francisco de Miranda",
        city="Caracas",
        city_slug="caracas",
        neighborhood="Altamira",
        property_type="Apartment",
        transaction="Sale",
        price_usd=185000,
        square_meters=96,
        bedrooms=2,
        bathrooms=2,
        parking=2,
        source_label="Sampled public listing",
        source_url="https://listado.mercadolibre.com.ve/inmuebles/apartamento-altamira-caracas",
        original_spanish="Apartamento en Altamira con dos habitaciones, dos banos, dos puestos de estacionamiento y vigilancia privada.",
        english_summary="Two-bedroom apartment in Altamira with private security, two parking spaces, and walkable access to offices, restaurants, and transit corridors.",
        main_image="https://images.unsplash.com/photo-1522708323590-d24dbb6b0267?auto=format&fit=crop&w=1200&q=75",
        first_seen="2026-04-20",
        last_seen="2026-04-30",
        quality_score=82,
    ),
    Listing(
        slug="caracas-las-mercedes-3-bedroom-apartment-320000",
        title="Las Mercedes 3-Bedroom Apartment With Parking",
        city="Caracas",
        city_slug="caracas",
        neighborhood="Las Mercedes",
        property_type="Apartment",
        transaction="Sale",
        price_usd=320000,
        square_meters=145,
        bedrooms=3,
        bathrooms=3,
        parking=3,
        source_label="Sampled public listing",
        source_url="https://listado.mercadolibre.com.ve/inmuebles/apartamento-las-mercedes-caracas",
        original_spanish="Apartamento amplio en Las Mercedes, tres habitaciones, tres banos, puestos de estacionamiento y edificio con pozo de agua.",
        english_summary="Larger apartment in Las Mercedes, a premium Caracas district, with parking, building services, and strong appeal for buyers prioritizing convenience.",
        main_image="https://images.unsplash.com/photo-1493809842364-78817add7ffb?auto=format&fit=crop&w=1200&q=75",
        first_seen="2026-04-20",
        last_seen="2026-04-30",
        quality_score=79,
    ),
    Listing(
        slug="margarita-playa-el-agua-vacation-apartment-78000",
        title="Playa El Agua Vacation Apartment on Margarita Island",
        city="Margarita Island",
        city_slug="margarita-island",
        neighborhood="Playa El Agua",
        property_type="Vacation apartment",
        transaction="Sale",
        price_usd=78000,
        square_meters=72,
        bedrooms=2,
        bathrooms=2,
        parking=1,
        source_label="Sampled public listing",
        source_url="https://listado.mercadolibre.com.ve/inmuebles/apartamento-playa-el-agua-margarita",
        original_spanish="Apartamento vacacional cerca de Playa El Agua, dos habitaciones, piscina, estacionamiento y areas sociales.",
        english_summary="Vacation-style apartment near Playa El Agua with pool access and parking, positioned for buyers evaluating island lifestyle or seasonal rental use.",
        main_image="https://images.unsplash.com/photo-1507525428034-b723cf961d3e?auto=format&fit=crop&w=1200&q=75",
        first_seen="2026-04-21",
        last_seen="2026-04-30",
        quality_score=75,
    ),
    Listing(
        slug="valencia-el-vinedo-3-bedroom-apartment-95000",
        title="El Vinedo Valencia 3-Bedroom Apartment",
        city="Valencia",
        city_slug="valencia",
        neighborhood="El Vinedo",
        property_type="Apartment",
        transaction="Sale",
        price_usd=95000,
        square_meters=110,
        bedrooms=3,
        bathrooms=2,
        parking=2,
        source_label="Sampled public listing",
        source_url="https://listado.mercadolibre.com.ve/inmuebles/apartamento-el-vinedo-valencia",
        original_spanish="Apartamento en El Vinedo, Valencia, tres habitaciones, dos banos, dos puestos y acceso a comercios.",
        english_summary="Mid-market Valencia apartment in El Vinedo with three bedrooms and practical access to retail, services, and major road corridors.",
        main_image="https://images.unsplash.com/photo-1505693416388-ac5ce068fe85?auto=format&fit=crop&w=1200&q=75",
        first_seen="2026-04-22",
        last_seen="2026-04-30",
        quality_score=73,
    ),
    Listing(
        slug="lecheria-canal-front-2-bedroom-apartment-135000",
        title="Lecheria Canal-Front 2-Bedroom Apartment",
        city="Lecheria",
        city_slug="lecheria",
        neighborhood="El Morro",
        property_type="Apartment",
        transaction="Sale",
        price_usd=135000,
        square_meters=88,
        bedrooms=2,
        bathrooms=2,
        parking=1,
        source_label="Sampled public listing",
        source_url="https://listado.mercadolibre.com.ve/inmuebles/apartamento-lecheria-el-morro",
        original_spanish="Apartamento en Lecheria sector El Morro, vista al canal, dos habitaciones, dos banos y puesto de estacionamiento.",
        english_summary="Canal-front apartment in Lecheria's El Morro area, suitable for buyers comparing coastal lifestyle assets and liquidity in premium eastern markets.",
        main_image="https://images.unsplash.com/photo-1512917774080-9991f1c4c750?auto=format&fit=crop&w=1200&q=75",
        first_seen="2026-04-23",
        last_seen="2026-04-30",
        quality_score=77,
    ),
)


GUIDES: dict[str, dict] = {
    "venezuela-homes-for-sale": {
        "path": "/real-estate/venezuela-homes-for-sale/",
        "title": "Venezuela Homes for Sale: Listings, Prices & Buyer Guide",
        "description": "Venezuela homes for sale in English: sampled listings, city price ranges, price-per-m2 context, title checks, seller verification, and buyer diligence guidance.",
        "keywords": "Venezuela homes for sale, Venezuela real estate listings, cheap houses for sale in Venezuela, Venezuela apartments for sale, Venezuela property listings",
        "h1": "Venezuela Homes for Sale",
        "intent": "Broad listing/search intent",
        "answer": "Start with comparable asking prices, then verify title, seller authority, building debts, and payment route before treating any Venezuelan listing as actionable inventory.",
        "sections": [
            ("Overview of homes for sale in Venezuela", "The market is fragmented across brokers, classifieds, WhatsApp circulation, and local portals. Caracas, Margarita Island, Valencia, and Lecheria are useful first markets because they represent business, vacation, industrial, and coastal demand."),
            ("What foreign buyers should know", "A listing is only a lead. Before paying a deposit, ask for the registered title document, seller identity documents, broker authorization, condominium fee status, utility debt status, and evidence of parking rights if parking is advertised."),
            ("How to evaluate Venezuelan property listings", "Normalize each property to price per square meter, then compare only against similar neighborhoods, building age, services, parking, water reliability, and security. Treat unusually cheap listings as diligence priorities, not automatic bargains."),
        ],
        "source_notes": GENERAL_SOURCE_NOTES + LEGAL_SOURCE_NOTES,
        "faqs": [
            ("Are Venezuela homes for sale reliable online?", "Online listings are useful leads, but they should be treated as unverified until title, seller identity, property condition, building debts, and payment terms are checked."),
            ("What cities should foreign buyers compare first?", "Caracas, Margarita Island, Valencia, and Lecheria give a practical first screen across business, vacation, industrial, and coastal/lifestyle markets."),
            ("What should I ask for before making an offer?", "Ask for the registered title document, seller identity documents, broker authorization, condominium-fee status, utility-debt status, parking documentation, and the proposed payment and closing route."),
        ],
    },
    "can-americans-buy-property-in-venezuela": {
        "path": "/real-estate/can-americans-buy-property-in-venezuela/",
        "title": "Can Americans Buy Property in Venezuela? Ownership, Sanctions & Diligence",
        "description": "Can Americans buy property in Venezuela? Practical guide to foreign ownership, SAREN/registry checks, OFAC screening, title diligence, travel risk, and closing steps.",
        "keywords": "can Americans buy property in Venezuela, can US citizens buy property in Venezuela, Venezuela foreign ownership, OFAC Venezuela real estate, Venezuela title diligence",
        "h1": "Can Americans Buy Property in Venezuela?",
        "answer": "Americans can generally evaluate Venezuelan property ownership, but the practical gating issues are SAREN/registry documentation, title verification, OFAC counterparty screening, travel risk, and a documented payment path.",
        "sections": [
            ("Foreign ownership considerations", "Foreign individuals can generally own and occupy real estate in Venezuela, but registries and notaries may require prior foreigner registration or verification through SAREN before a document can continue through the registration or notarization process. Confirm the exact requirement with local counsel before signing."),
            ("U.S. sanctions and counterparty screening", "Screen the seller, broker, beneficial owners, property company, building association if relevant, and any payment intermediary against OFAC's Venezuela-related sanctions framework. A private real estate deal can still create sanctions exposure if a blocked person or government-linked entity is involved."),
            ("Closing process overview", "A practical workflow is: collect title and identity documents, run registry/title review, confirm encumbrances and building debts, sign through the appropriate notary or registry process, record the transfer, and retain payment evidence that matches the closing documents."),
            ("Common mistakes", "Do not wire funds before title review, do not rely on screenshots or WhatsApp-only documents, do not assume the broker has authority to bind the seller, and do not ignore U.S. travel and consular-service limits if in-country verification is required."),
        ],
        "source_notes": GENERAL_SOURCE_NOTES + LEGAL_SOURCE_NOTES,
        "faqs": [
            ("Can U.S. citizens buy Venezuelan real estate?", "Generally yes, but a U.S. buyer should confirm local registry requirements, use Venezuelan counsel, verify title and seller authority, and screen all counterparties for sanctions exposure."),
            ("Do U.S. sanctions ban every Venezuela real estate deal?", "No. Sanctions do not automatically ban every private property transaction, but they can affect sellers, beneficial owners, banks, payment intermediaries, and government-linked entities connected to a deal."),
            ("Can Americans close a Venezuela property purchase remotely?", "Sometimes, but remote closing depends on properly drafted powers of attorney, local registry/notary requirements, document legalization, and a trusted local representative. Do not assume a remote purchase is safe without counsel."),
        ],
    },
    "can-canadians-buy-property-in-venezuela": {
        "path": "/real-estate/can-canadians-buy-property-in-venezuela/",
        "title": "Can Canadians Buy Property in Venezuela? Buyer Guide & Diligence",
        "description": "Can Canadians buy property in Venezuela? Ownership considerations, Canadian sanctions, travel risk, payment logistics, registry checks, and buyer diligence steps.",
        "keywords": "can Canadians buy property in Venezuela, Canadian buyers Venezuela real estate, Venezuela foreign ownership, Canada Venezuela sanctions property, Venezuela property diligence",
        "h1": "Can Canadians Buy Property in Venezuela?",
        "answer": "Canadians can generally evaluate Venezuelan property ownership, but the decision should be filtered through title diligence, local documentation, payment logistics, Canadian sanctions exposure, and Canada's current avoid-all-travel advisory.",
        "sections": [
            ("Ownership considerations", "Canadian buyers should confirm identity documents, tax or registry needs, SAREN foreigner-verification requirements, marital or family-status documentation, and whether a Venezuelan power of attorney is needed to close without being physically present."),
            ("Currency and payment concerns", "Many listings are advertised in U.S. dollars even when Venezuelan paperwork may reference local currency or registry values. Confirm the receiving party, banking route, payment evidence, and whether any Canadian financial prohibition or sanctions listing affects the transaction."),
            ("Diligence checklist", "Verify title, seller authority, liens, inheritance or succession issues, condominium fees, utility debt, property-tax status, and whether the broker or attorney is independent from the seller."),
        ],
        "source_notes": CANADA_SOURCE_NOTES + LEGAL_SOURCE_NOTES,
        "faqs": [
            ("Can Canadian citizens own real estate in Venezuela?", "Generally yes, but Canadian buyers should use local counsel, confirm Venezuelan registry requirements, verify title, and check whether Canadian sanctions or travel-risk issues affect the transaction."),
            ("Should Canadians pay a Venezuelan seller directly?", "Only after confirming seller identity, title authority, the receiving account, and a documented payment route that matches the closing documents."),
            ("Does Canada's Venezuela travel advice matter for property buyers?", "Yes. If Canada advises against travel, buyers should plan for remote document review, trusted local representation, and extra verification before relying on in-country inspections or meetings."),
        ],
    },
    "buy-property-in-venezuela": {
        "path": "/real-estate/buy-property-in-venezuela/",
        "title": "Buy Property in Venezuela: Step-by-Step Guide for Foreign Buyers",
        "description": "How to buy property in Venezuela as a foreign buyer: listing screen, document request, title review, seller verification, sanctions screening, inspection, payment, and closing.",
        "keywords": "buy property in Venezuela, how to buy property in Venezuela, Venezuela property purchase process, Venezuela real estate documents, Venezuela property closing",
        "h1": "Buy Property in Venezuela",
        "answer": "The useful sequence is listing screen, document request, title and encumbrance review, sanctions/counterparty screening, property inspection, negotiated closing terms, and only then payment and registration.",
        "sections": [
            ("Step-by-step buying overview", "Define city and budget, collect comparable listings, request documents, verify seller authority, screen counterparties, inspect the property, negotiate terms, and close only after counsel confirms the transfer path and registry requirements."),
            ("Documents to ask for", "Ask for the registered purchase-sale title or other title instrument, seller identity documents, tax and municipal references, condominium or building fee status, utility status, parking documentation, broker authorization, and any power of attorney."),
            ("Typical diligence workflow", "Use Venezuelan counsel for registry/title checks and encumbrance review, an independent inspection for condition and services, sanctions screening for counterparties, and a payment record that can be reconciled to the closing documents."),
            ("Common mistakes", "Avoid relying on translated summaries alone, paying reservation deposits without documents, accepting stale title copies, or assuming low prices compensate for weak title or unclear seller authority."),
        ],
        "source_notes": GENERAL_SOURCE_NOTES + LEGAL_SOURCE_NOTES,
        "faqs": [
            ("What is the first step to buy property in Venezuela?", "Start with city selection and comparable listings, then move quickly into document requests, seller verification, and title review before discussing deposits."),
            ("Do foreign buyers need a Venezuelan attorney?", "For any serious transaction, yes. Independent local counsel is central to title review, encumbrance checks, registry requirements, and closing documentation."),
            ("What documents should a buyer request first?", "Request the title instrument, seller identity documents, broker authorization, power of attorney if applicable, condominium-fee status, tax or municipal references, parking documents, and utility-debt status."),
        ],
    },
    "venezuela-real-estate-risks": {
        "path": "/real-estate/venezuela-real-estate-risks/",
        "title": "Venezuela Real Estate Risks: Title, Seller, Currency & Sanctions",
        "description": "Venezuela real estate risks for foreign buyers: title uncertainty, seller authority, liens, pricing opacity, infrastructure, payment friction, sanctions exposure, and scams.",
        "keywords": "Venezuela real estate risks, Venezuela property due diligence, Venezuela title risk, Venezuela real estate scams, Venezuela sanctions real estate",
        "h1": "Venezuela Real Estate Risks",
        "answer": "Before any deposit, verify registered title, seller authority, liens, building debts, sanctions exposure, payment route, and on-the-ground property condition. Cheap prices do not compensate for weak documents.",
        "sections": [
            ("Title and seller verification risk", "Confirm chain of title, registry status, encumbrances, family or succession claims, powers of attorney, and whether the person signing is legally able to transfer the property."),
            ("Pricing opacity", "Asking prices can be stale, duplicated, optimistic, or negotiated heavily. Compare price per square meter across several similar listings in the same neighborhood and building class."),
            ("Infrastructure and currency risk", "Water, power, elevator maintenance, building reserves, condominium finances, and hard-currency payment rails can determine whether an apparently attractive asset is actually usable."),
            ("Sanctions and scam risk", "Screen counterparties and avoid pressure to pay before documents are verified. Be cautious with remote-only sellers, unverifiable brokers, requests for deposits to personal third-party accounts, and unusually urgent discounts."),
        ],
        "source_notes": GENERAL_SOURCE_NOTES + CANADA_SOURCE_NOTES + LEGAL_SOURCE_NOTES,
        "faqs": [
            ("What is the biggest Venezuela property risk?", "For foreign buyers, weak title or unclear seller authority is usually the highest-impact risk because it can make a cheap property impossible or unsafe to close."),
            ("How do sanctions affect real estate?", "Sanctions can affect sellers, beneficial owners, banks, payment intermediaries, government-linked entities, or service providers connected to a deal."),
            ("How can buyers reduce scam risk?", "Avoid pressure deposits, insist on title and identity documents, verify broker authority, use independent counsel, compare price per square meter, and do not send funds to unrelated third-party accounts."),
        ],
    },
    "venezuela-real-estate-prices": {
        "path": "/real-estate/venezuela-real-estate-prices/",
        "title": "Venezuela Real Estate Prices: Sampled Listings by City",
        "description": "Venezuela real estate prices from sampled public listings: median asking price, median price per m2, city ranges for Caracas, Margarita Island, Valencia, and Lecheria.",
        "keywords": "Venezuela real estate prices, Venezuela property prices, Caracas real estate prices, Margarita Island real estate prices, Venezuela price per square meter",
        "h1": "Venezuela Real Estate Prices",
        "answer": "In the current Caracas Research sample, the median asking price is $135,000 and the median asking price per square meter is $1,534. Treat those figures as a directional screen, not an appraisal.",
        "sections": [
            ("Methodology", METHODOLOGY),
            ("Cheapest and premium markets", "In the current sample, Valencia and Margarita Island screen cheaper on a price-per-square-meter basis than prime Caracas. Lecheria and premium Caracas listings can command higher asking prices because of location, services, parking, and lifestyle demand."),
            ("How to use the figures", "Use medians to compare markets, then verify real transaction values through local brokers, counsel, registry records where available, and recent comparable sales. Asking prices are not closing prices."),
        ],
        "source_notes": GENERAL_SOURCE_NOTES,
        "faqs": [
            ("Are these Venezuela real estate prices appraisals?", "No. They are directional figures from sampled public listings and should not be treated as formal appraisals or confirmed transaction prices."),
            ("Why use price per square meter?", "Price per square meter helps normalize apartments and houses of different sizes, but it should only be compared within similar neighborhoods, building quality, services, and property types."),
            ("Why can asking prices differ so much in Venezuela?", "Prices vary because of neighborhood liquidity, building services, water and power reliability, parking, seller urgency, documentation quality, and whether the listing price is realistic or aspirational."),
        ],
    },
    "is-venezuela-real-estate-a-good-investment": {
        "path": "/real-estate/is-venezuela-real-estate-a-good-investment/",
        "title": "Is Venezuela Real Estate a Good Investment? Upside, Risks & Fit",
        "description": "Is Venezuela real estate a good investment? Balanced guide to distressed pricing, diaspora demand, tourism upside, title risk, sanctions, liquidity, and who should avoid it.",
        "keywords": "is Venezuela real estate a good investment, Venezuela property investment, Venezuela real estate investment risk, distressed real estate Venezuela, Venezuela investment property",
        "h1": "Is Venezuela Real Estate a Good Investment?",
        "answer": "Venezuela real estate may suit buyers with local verification capacity, a long time horizon, and tolerance for title, sanctions, travel, currency, and liquidity risk. It is a poor fit for passive buyers who need easy exits.",
        "sections": [
            ("Why investors look at Venezuela", "Potential upside comes from distressed pricing, diaspora demand, tourism optionality, premium urban scarcity, and a possible long-term normalization scenario."),
            ("Main downside risks", "Title risk, liquidity, sanctions, currency/payment friction, infrastructure issues, travel risk, and political/security uncertainty can overwhelm the headline discount."),
            ("Who it may suit", "It may suit diaspora buyers, long-horizon investors, and buyers with trusted local counsel, on-the-ground verification capacity, and no need for near-term liquidity."),
            ("Who should avoid it", "Avoid it if you cannot tolerate illiquidity, documentation complexity, local execution risk, travel constraints, or potential loss of deposits and time."),
        ],
        "source_notes": GENERAL_SOURCE_NOTES + CANADA_SOURCE_NOTES,
        "faqs": [
            ("Is Venezuelan property cheap?", "Some listings appear cheap versus North American markets, but risk-adjusted value depends on title verification, property condition, payment execution, and exit liquidity."),
            ("Is Venezuela real estate suitable for passive investors?", "Usually no. Passive foreign buyers face higher monitoring, documentation, security, payment, and maintenance risk than they would in a conventional North American market."),
            ("Who is Venezuela real estate best suited for?", "It is most suitable for diaspora buyers, long-horizon investors, and buyers with trusted local counsel, local verification capacity, and no need for a quick resale."),
        ],
    },
}


CITY_PAGES: dict[str, dict] = {
    "caracas": {
        "path": "/real-estate/caracas/",
        "title": "Caracas Real Estate: Prices, Listings & Buyer Guide",
        "h1": "Caracas Real Estate for Foreign Investors",
        "overview": "Caracas is the core business and premium apartment market for foreign buyers evaluating Venezuela. In the current sample, Altamira and Las Mercedes apartments screen from $185,000 to $320,000, with parking, security, water reliability, and building services driving much of the price difference.",
        "neighborhoods": ["Altamira", "Las Mercedes", "La Castellana", "Chacao", "Los Palos Grandes"],
        "keywords": "Caracas real estate, Caracas apartments for sale, Caracas Venezuela real estate",
    },
    "margarita-island": {
        "path": "/real-estate/margarita-island/",
        "title": "Margarita Island Real Estate: Beachfront & Vacation Property Guide",
        "h1": "Margarita Island Real Estate for Foreign Investors",
        "overview": "Margarita Island screens as a vacation and beachfront-property market. The current sample includes a Playa El Agua vacation apartment at $78,000, making it useful for buyers comparing lifestyle use, diaspora demand, and tourism recovery optionality.",
        "neighborhoods": ["Playa El Agua", "Pampatar", "Porlamar", "Costa Azul"],
        "keywords": "Margarita Island real estate, Isla Margarita real estate, beachfront property Venezuela",
    },
    "valencia": {
        "path": "/real-estate/valencia/",
        "title": "Valencia Venezuela Real Estate: Prices, Neighborhoods & Guide",
        "h1": "Valencia Venezuela Real Estate for Foreign Investors",
        "overview": "Valencia is a practical central-market screen for buyers comparing larger apartments, industrial-city demand, and lower headline prices than prime Caracas. The current sample includes an El Vinedo three-bedroom apartment at $95,000.",
        "neighborhoods": ["El Vinedo", "Prebo", "La Trigaleña", "Naguanagua"],
        "keywords": "Valencia Venezuela real estate, Valencia apartments for sale, property in Valencia Venezuela",
    },
    "lecheria": {
        "path": "/real-estate/lecheria/",
        "title": "Lecheria Real Estate: Coastal Property Guide for Foreign Buyers",
        "h1": "Lecheria Real Estate for Foreign Investors",
        "overview": "Lecheria is a premium coastal/lifestyle market in eastern Venezuela, often compared by buyers seeking canal-front apartments, marina access, and a more resort-like setting. The current sample includes an El Morro canal-front apartment at $135,000.",
        "neighborhoods": ["El Morro", "Casco Central", "Complejo Turistico El Morro"],
        "keywords": "Lecheria real estate, Lecheria Venezuela property, coastal property Venezuela",
    },
}


def all_listings() -> list[Listing]:
    return list(LISTINGS)


def get_listing(slug: str) -> Listing | None:
    return next((listing for listing in LISTINGS if listing.slug == slug), None)


def listings_for_city(city_slug: str) -> list[Listing]:
    return [listing for listing in LISTINGS if listing.city_slug == city_slug]


def market_stats(listings: list[Listing] | None = None) -> dict:
    rows = listings if listings is not None else all_listings()
    if not rows:
        return {
            "count": 0,
            "median_price": 0,
            "median_ppm2": 0,
            "low_price": 0,
            "high_price": 0,
            "common_types": [],
            "neighborhoods": [],
        }
    return {
        "count": len(rows),
        "median_price": round(median([r.price_usd for r in rows])),
        "median_ppm2": round(median([r.price_per_m2 for r in rows])),
        "low_price": min(r.price_usd for r in rows),
        "high_price": max(r.price_usd for r in rows),
        "common_types": sorted({r.property_type for r in rows}),
        "neighborhoods": sorted({r.neighborhood for r in rows}),
    }


def real_estate_paths() -> list[str]:
    paths = ["/real-estate/", "/real-estate/properties/", "/real-estate/buyers-guide/"]
    paths.extend(page["path"] for page in GUIDES.values())
    paths.extend(page["path"] for page in CITY_PAGES.values())
    paths.extend(f"/real-estate/property/{listing.slug}/" for listing in LISTINGS)
    return paths
