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

MARKET_SOURCE_NOTES = (
    {
        "label": "Property.com.ve Caracas price index",
        "url": "https://property.com.ve/en/property-prices-in-caracas",
        "note": "April 2026 public index tracking 5,976 active Caracas listings, with median residential price, median price per square meter, and apartment/house breakdowns.",
    },
    {
        "label": "Property.com.ve Margarita price index",
        "url": "https://property.com.ve/en/property-prices-in-margarita",
        "note": "April 2026 public index tracking 299 active Margarita listings, including apartment and house medians.",
    },
    {
        "label": "Property.com.ve Valencia price index",
        "url": "https://property.com.ve/en/property-prices-in-valencia",
        "note": "April 2026 public index tracking 281 active Valencia listings, including apartment and house medians.",
    },
)

LEGAL_PRACTICE_SOURCE_NOTES = (
    {
        "label": "Baker McKenzie Venezuela real estate law guide",
        "url": "https://resourcehub.bakermckenzie.com/en/resources/global-corporate-real-estate-guide/latin-america/venezuela/topics/real-estate-law",
        "note": "Foreign ownership is generally permitted, subject to security-zone restrictions and written authorization requirements in sensitive areas.",
    },
    {
        "label": "Deloitte Legal: Venezuela real estate registry system",
        "url": "https://www.deloittelegal.de/dl/en/services/legal/about/real-estate-law-venezuela",
        "note": "Overview of SAREN subordinate registry offices and the registration steps for purchase-sale documents and other real estate acts.",
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
        slug="caracas-los-caobos-penthouse-320000-rah-24-8417",
        title="Los Caobos Penthouse Apartment With Avila Views",
        city="Caracas",
        city_slug="caracas",
        neighborhood="Los Caobos",
        property_type="Apartment",
        transaction="Sale",
        price_usd=320000,
        square_meters=450,
        bedrooms=4,
        bathrooms=6,
        parking=3,
        source_label="Rent-A-House Venezuela",
        source_url="https://rentahouse.com.ve/apartamento_en_venta_en_caracas_en_los-caobos_rah-24-8417.html",
        original_spanish="Exclusivo y Moderno PH. Tres plantas, ascensor privado, iluminación y ventilación natural en todas sus áreas, extenso salón principal con vista al Avila y la Gran Caracas, amplia cocina equipada con tope de granito, área de lavandero, 5 habitaciones con baño privado y la habitación principal con vestier. Salón estudio - biblioteca, 2 terrazas descubiertas al aire libre con vista de 360°.",
        english_summary="Three-level penthouse in Los Caobos with private elevator, Avila and Caracas views, large living areas, equipped kitchen, multiple terraces, five private-bath bedroom areas, three parking spaces, and one storage room.",
        main_image="https://cdn.resize.sparkplatform.com/ven/1024x768/true/20231016021658034267000000-o.jpg",
        first_seen="2026-05-01",
        last_seen="2026-05-01",
        quality_score=88,
    ),
    Listing(
        slug="caracas-los-ruices-2-bedroom-apartment-71000-rah-26-14269",
        title="Los Ruices 2-Bedroom Apartment Near Metro and Services",
        city="Caracas",
        city_slug="caracas",
        neighborhood="Los Ruices",
        property_type="Apartment",
        transaction="Sale",
        price_usd=71000,
        square_meters=56,
        bedrooms=2,
        bathrooms=1,
        parking=0,
        source_label="Rent-A-House Venezuela",
        source_url="https://rentahouse.com.ve/apartamento_en_venta_en_caracas_en_los-ruices_rah-26-14269.html",
        original_spanish="Venta de apto. En Los Ruices en edificio de fácil acceso cerca de supermercados, estación del metro, restaurantes, colegios, farmacias con diferentes vías de acceso, en piso medio. Se entrega sin muebles. Cuenta con dos habitaciones y un baño, línea de teléfono CANTV + ABA. El edificio cuenta con fibra óptica.",
        english_summary="Mid-floor Los Ruices apartment near supermarkets, metro access, restaurants, schools, pharmacies, and multiple road connections. The listing describes two bedrooms, one bathroom, CANTV/ABA line, and building fiber optic service.",
        main_image="https://cdn.resize.sparkplatform.com/ven/1024x768/true/20260217024354662467000000-o.jpg",
        first_seen="2026-05-01",
        last_seen="2026-05-01",
        quality_score=84,
    ),
    Listing(
        slug="caracas-el-rosal-3-bedroom-apartment-330000-rah-26-10558",
        title="El Rosal 3-Bedroom Apartment With Private Elevator",
        city="Caracas",
        city_slug="caracas",
        neighborhood="El Rosal",
        property_type="Apartment",
        transaction="Sale",
        price_usd=330000,
        square_meters=182,
        bedrooms=3,
        bathrooms=3,
        parking=2,
        source_label="Rent-A-House Venezuela",
        source_url="https://rentahouse.com.ve/apartamento_en_venta_en_caracas_en_el-rosal_rah-26-10558.html",
        original_spanish="Amplio y espléndido apartamento ubicado en una de las urbanizaciones más céntricas y mejor ubicada de la ciudad. Cómoda habitación principal con baño y closets, dos habitaciones secundarias que comparten baño, área de comedor y sala con una linda terraza techada, cocina con habitación de servicio y baño. Estudio con baño completo, ascensor privado, dos puestos de estacionamientos techados. Maletero.",
        english_summary="Spacious El Rosal apartment in a central Caracas location with primary bedroom suite, two secondary bedrooms, dining and living areas, covered terrace, service room, study with bathroom, private elevator, two covered parking spaces, and storage.",
        main_image="https://cdn.resize.sparkplatform.com/ven/1024x768/true/20251208220412024803000000-o.jpg",
        first_seen="2026-05-01",
        last_seen="2026-05-01",
        quality_score=86,
    ),
    Listing(
        slug="caracas-los-chorros-3-bedroom-apartment-550000-rah-26-6606",
        title="Los Chorros 3-Bedroom Apartment With Terrace and Four Parking Spaces",
        city="Caracas",
        city_slug="caracas",
        neighborhood="Los Chorros",
        property_type="Apartment",
        transaction="Sale",
        price_usd=550000,
        square_meters=238,
        bedrooms=3,
        bathrooms=5,
        parking=4,
        source_label="Rent-A-House Venezuela",
        source_url="https://rentahouse.com.ve/apartamento_en_venta_en_caracas_en_los-chorros_rah-26-6606.html",
        original_spanish="Disfruta de un estilo de vida exclusivo en un entorno seguro y tranquilo en este apartamento con hermosa vista y bellas áreas sociales. Cuenta con una terraza interna, 3 habitaciones con baños privados, cocina moderna totalmente equipada, que combinan elegancia y confort. Dispone de 4 puestos de estacionamiento, con fácil acceso a centros comerciales, restaurantes, colegios y principales vías.",
        english_summary="Los Chorros apartment marketed as an exclusive, quiet residential setting with views, social areas, internal terrace, three bedrooms with private bathrooms, modern equipped kitchen, four parking spaces, and access to shopping, restaurants, schools, and main roads.",
        main_image="https://cdn.resize.sparkplatform.com/ven/1024x768/true/20250929220510650571000000-o.jpg",
        first_seen="2026-05-01",
        last_seen="2026-05-01",
        quality_score=87,
    ),
    Listing(
        slug="caracas-el-marques-3-bedroom-apartment-150000-rah-26-11300",
        title="El Marques 3-Bedroom Apartment With Avila View",
        city="Caracas",
        city_slug="caracas",
        neighborhood="El Marques",
        property_type="Apartment",
        transaction="Sale",
        price_usd=150000,
        square_meters=208,
        bedrooms=3,
        bathrooms=3,
        parking=2,
        source_label="Rent-A-House Venezuela",
        source_url="https://rentahouse.com.ve/apartamento_en_venta_en_caracas_en_el-marques_rah-26-11300.html",
        original_spanish="Espectacular, acogedor y excelente distribución Pent House con amplios espacios, vista al Avila. El mismo consta de sala comedor, cocina, 3 habitaciones, 3 baños, 1 terraza cubierta, 2 puestos de estacionamientos.",
        english_summary="El Marques apartment described as a penthouse-style unit with generous spaces, Avila view, living-dining area, kitchen, three bedrooms, three bathrooms, one covered terrace, and two parking spaces.",
        main_image="https://cdn.resize.sparkplatform.com/ven/1024x768/true/20251230135130742868000000-o.jpg",
        first_seen="2026-05-01",
        last_seen="2026-05-01",
        quality_score=85,
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
        "answer": "Start with current public asking-price data, then verify title, seller authority, building debts, security-zone issues, sanctions exposure, and payment route before treating any Venezuelan listing as actionable.",
        "sections": [
            ("Overview of homes for sale in Venezuela", "Public listing supply is deepest in Caracas, but buyers also compare Margarita Island for vacation and coastal use, Valencia for lower headline prices, and Lecheria for eastern coastal lifestyle demand. April 2026 public price indexes show Caracas with 5,976 active listings tracked, Margarita with 299, and Valencia with 281."),
            ("Current public price context", "Property.com.ve's April 2026 city indexes report median residential price-per-square-meter figures of $914/m² in Caracas, $581/m² in Margarita, and $558/m² in Valencia. Within those same indexes, apartments screen higher: $1,069/m² in Caracas, $994/m² in Margarita, and $733/m² in Valencia."),
            ("What foreign buyers should know", "A listing is only a lead. Before paying a deposit, ask for the registered title document, seller identity documents, broker authorization, condominium fee status, utility debt status, evidence of parking rights, and confirmation that the property is not in a restricted security zone."),
            ("How to evaluate Venezuelan property listings", "Normalize each property to price per square meter, then compare only against similar neighborhoods, building age, services, parking, water reliability, and security. Treat unusually cheap listings as diligence priorities, not automatic bargains."),
        ],
        "source_notes": MARKET_SOURCE_NOTES + GENERAL_SOURCE_NOTES + LEGAL_SOURCE_NOTES,
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
        "answer": "Americans can generally evaluate Venezuelan property ownership, but the practical gating issues are SAREN/registry documentation, title verification, security-zone restrictions, OFAC counterparty screening, travel risk, and a documented payment path.",
        "sections": [
            ("Foreign ownership considerations", "Foreign individuals can generally own and occupy real estate in Venezuela. Current international legal guides also flag restrictions in security zones, including areas near borders, military facilities, certain basic industries, and other sensitive corridors. Registries and notaries may require prior foreigner registration or verification through SAREN before a document can continue through registration or notarization."),
            ("U.S. sanctions and counterparty screening", "OFAC's Venezuela program remains active and changes over time through general licenses, executive orders, and FAQ guidance. Screen the seller, broker, beneficial owners, property company, building association if relevant, and any payment intermediary against OFAC lists and the broader Venezuela-related sanctions framework."),
            ("Travel and consular context", "The U.S. State Department updated Venezuela to Level 3, Reconsider Travel, on March 19, 2026. The advisory still cites crime, kidnapping, terrorism, and poor health infrastructure, and notes that routine consular services remain limited while embassy operations resume gradually."),
            ("Closing process overview", "A practical workflow is: collect title and identity documents, run registry/title review, confirm encumbrances and building debts, check security-zone issues, sign through the appropriate notary or registry process, record the transfer, and retain payment evidence that matches the closing documents."),
            ("Common mistakes", "Do not wire funds before title review, do not rely on screenshots or WhatsApp-only documents, do not assume the broker has authority to bind the seller, and do not ignore U.S. travel and consular-service limits if in-country verification is required."),
        ],
        "source_notes": GENERAL_SOURCE_NOTES + LEGAL_SOURCE_NOTES + LEGAL_PRACTICE_SOURCE_NOTES,
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
        "source_notes": CANADA_SOURCE_NOTES + LEGAL_SOURCE_NOTES + LEGAL_PRACTICE_SOURCE_NOTES,
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
            ("Step-by-step buying overview", "Define city and budget, collect comparable listings, request documents, verify seller authority, screen counterparties, inspect the property, negotiate terms, and close only after counsel confirms the transfer path, registry requirements, and any security-zone restrictions."),
            ("Documents to ask for", "Ask for the registered purchase-sale title or other title instrument, seller identity documents, tax and municipal references, condominium or building fee status, utility status, parking documentation, broker authorization, cadastral information, and any power of attorney."),
            ("Public registry and title review", "Ownership is evidenced through registration in the corresponding Public Registry. A serious buyer should ask counsel to review the title instrument, chain of title, liens, encumbrances, condominium obligations, and whether the registry record matches the seller's authority to transfer."),
            ("Typical diligence workflow", "Use Venezuelan counsel for registry/title checks and encumbrance review, an independent inspection for condition and services, sanctions screening for counterparties, and a payment record that can be reconciled to the closing documents."),
            ("Common mistakes", "Avoid relying on translated summaries alone, paying reservation deposits without documents, accepting stale title copies, or assuming low prices compensate for weak title or unclear seller authority."),
        ],
        "source_notes": GENERAL_SOURCE_NOTES + LEGAL_SOURCE_NOTES + LEGAL_PRACTICE_SOURCE_NOTES,
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
        "answer": "Before any deposit, verify registered title, seller authority, liens, building debts, security-zone restrictions, sanctions exposure, payment route, travel risk, and on-the-ground property condition. Cheap prices do not compensate for weak documents.",
        "sections": [
            ("Title and seller verification risk", "Confirm chain of title, registry status, encumbrances, family or succession claims, powers of attorney, and whether the person signing is legally able to transfer the property. Public registry review matters because the registered title is the core evidence of ownership."),
            ("Pricing opacity", "Asking prices can be stale, duplicated, optimistic, or negotiated heavily. Compare price per square meter across several similar listings in the same neighborhood and building class."),
            ("Infrastructure and currency risk", "Water, power, elevator maintenance, building reserves, condominium finances, and hard-currency payment rails can determine whether an apparently attractive asset is actually usable. Building condition and service reliability often explain large price differences between similar-looking units."),
            ("Sanctions and scam risk", "Screen counterparties and avoid pressure to pay before documents are verified. Be cautious with remote-only sellers, unverifiable brokers, requests for deposits to personal third-party accounts, and unusually urgent discounts."),
            ("Travel and inspection risk", "U.S. and Canadian government travel advice remains a material diligence input. Remote buyers should plan for independent inspection, trusted local representation, and secure document handling rather than relying on seller-provided photos or informal messages."),
        ],
        "source_notes": GENERAL_SOURCE_NOTES + CANADA_SOURCE_NOTES + LEGAL_SOURCE_NOTES + LEGAL_PRACTICE_SOURCE_NOTES,
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
        "answer": "Current public price indexes show wide dispersion by city and property type. April 2026 residential medians from Property.com.ve report $914/m² in Caracas, $581/m² in Margarita, and $558/m² in Valencia; treat these as directional listing data, not appraisals.",
        "sections": [
            ("Methodology", METHODOLOGY),
            ("April 2026 city benchmarks", "Property.com.ve's April 2026 price indexes report Caracas at a $211,680 median residential asking price across 5,976 tracked listings, Margarita at $95,000 across 299 listings, and Valencia at $125,000 across 281 listings. Apartment-specific medians were $170,000 in Caracas, $107,500 in Margarita, and $105,000 in Valencia."),
            ("Cheapest and premium markets", "Valencia and Margarita generally screen lower on broad residential price-per-square-meter indexes than Caracas, while prime Caracas, waterfront Margarita, and Lecheria-style coastal assets can command premiums because of location, services, parking, and lifestyle demand."),
            ("How to use the figures", "Use medians to compare markets, then verify real transaction values through local brokers, counsel, registry records where available, and recent comparable sales. Asking prices are not closing prices."),
        ],
        "source_notes": MARKET_SOURCE_NOTES + GENERAL_SOURCE_NOTES,
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
        "source_notes": MARKET_SOURCE_NOTES + GENERAL_SOURCE_NOTES + CANADA_SOURCE_NOTES + LEGAL_PRACTICE_SOURCE_NOTES,
        "faqs": [
            ("Is Venezuelan property cheap?", "Some listings appear cheap versus North American markets, but risk-adjusted value depends on title verification, property condition, payment execution, and exit liquidity."),
            ("Is Venezuela real estate suitable for passive investors?", "Usually no. Passive foreign buyers face higher monitoring, documentation, security, payment, and maintenance risk than they would in a conventional North American market."),
            ("Who is Venezuela real estate best suited for?", "It is most suitable for diaspora buyers, long-horizon investors, and buyers with trusted local counsel, local verification capacity, and no need for a quick resale."),
        ],
    },
    "caracas-apartments-for-sale": {
        "path": "/caracas-apartments-for-sale/",
        "title": "Caracas Apartments for Sale: Prices, Listings & Buyer Checks",
        "description": "Caracas apartments for sale in English: sampled listings, price-per-m2 context, neighborhoods, building-service checks, title diligence, and foreign-buyer risks.",
        "keywords": "Caracas apartments for sale, Caracas real estate, apartments for sale in Caracas Venezuela, Caracas property listings",
        "h1": "Caracas Apartments for Sale",
        "answer": "Caracas apartment listings can be useful starting points, but buyers should compare price per square meter, building services, parking rights, water reliability, seller authority, sanctions exposure, and title documentation before treating a listing as actionable.",
        "sections": [
            ("Where foreign buyers usually start", "Premium and practical apartment searches often begin in eastern and central neighborhoods where services, security, parking, and access to offices or retail can materially affect value. Public 2026 listing indexes show Caracas as the deepest market in the country, with far more tracked inventory than Margarita or Valencia."),
            ("Current Caracas price context", "Property.com.ve's April 2026 Caracas price index reports 5,976 active residential listings, a $211,680 median residential asking price, and $914/m² median residential price per square meter. Its apartment subset reports 3,380 apartment listings, a $170,000 median apartment asking price, and $1,069/m²."),
            ("How to compare Caracas apartment prices", "Normalize asking prices by square meter, then compare only against similar building class, neighborhood, parking, water service, elevator condition, security, and maintenance quality."),
            ("Apartment diligence checklist", "Before discussing a deposit, ask for the registered title, seller identity and authority, condominium-fee status, utility-debt status, parking documentation, and evidence that the broker is authorized to represent the seller."),
        ],
        "source_notes": MARKET_SOURCE_NOTES + GENERAL_SOURCE_NOTES + LEGAL_SOURCE_NOTES,
        "faqs": [
            ("Are Caracas apartment listings reliable?", "They are useful leads, but buyers should verify title, seller identity, building debts, parking rights, and property condition before making an offer or sending funds."),
            ("What matters most in a Caracas apartment?", "Neighborhood, water reliability, elevator maintenance, parking, security, building reserves, documentation quality, and realistic price per square meter usually matter as much as headline price."),
            ("Should foreign buyers focus only on premium neighborhoods?", "Not always. Premium neighborhoods may offer better liquidity and services, while value areas can be cheaper but require careful checks on services, building finances, and resale demand."),
        ],
    },
    "venezuela-property-investment-guide": {
        "path": "/venezuela-property-investment-guide/",
        "title": "Venezuela Property Investment Guide: Upside, Risks & Buyer Fit",
        "description": "Venezuela property investment guide for foreign buyers: pricing upside, diaspora and tourism demand, title risk, sanctions, liquidity, and diligence workflow.",
        "keywords": "Venezuela property investment guide, Venezuela real estate investment, Venezuela investment property, Venezuela property risk",
        "h1": "Venezuela Property Investment Guide",
        "answer": "Venezuela property can screen as discounted, but the investment case depends on title quality, local execution, building services, liquidity, sanctions screening, travel constraints, and whether the buyer can tolerate a long holding period.",
        "sections": [
            ("Why investors evaluate Venezuela property", "Investors usually look at Venezuela because asking prices can appear low compared with North American markets, while diaspora demand, tourism recovery, and premium urban scarcity create possible long-term upside. April 2026 public listing indexes show Caracas apartment medians far below major U.S. and Canadian metros, but that headline gap must be adjusted for execution risk."),
            ("Where the investment case can break", "The apparent discount can disappear if the property has weak title, unclear seller authority, poor building services, unpaid condominium debts, payment friction, sanctions exposure, or limited resale liquidity."),
            ("How to compare markets", "Use Caracas as the liquidity and premium-apartment benchmark, Margarita as the vacation/coastal benchmark, Valencia as a lower-cost central-city benchmark, and Lecheria as a lifestyle/coastal benchmark that requires local broker validation because public English-language data is thinner."),
            ("Who this fits", "The market is better suited to diaspora buyers, local operators, and long-horizon investors with trusted Venezuelan counsel and on-the-ground verification capacity."),
            ("Who should avoid it", "Avoid Venezuelan property if you need predictable financing, remote-only execution, quick resale liquidity, or a low-friction purchase process."),
        ],
        "source_notes": MARKET_SOURCE_NOTES + GENERAL_SOURCE_NOTES + CANADA_SOURCE_NOTES + LEGAL_SOURCE_NOTES + LEGAL_PRACTICE_SOURCE_NOTES,
        "faqs": [
            ("Is Venezuela property a passive investment?", "Usually no. Buyers should expect active diligence, local verification, document review, and ongoing attention to building services and liquidity."),
            ("What is the main investment risk?", "The largest risk is often not price; it is whether the title, seller authority, payment path, and property condition support a clean, enforceable transaction."),
            ("Which cities should investors compare first?", "Caracas, Margarita Island, Valencia, and Lecheria give a practical first screen across business, vacation, value, and coastal lifestyle markets."),
        ],
    },
    "venezuela-real-estate-lawyer": {
        "path": "/venezuela-real-estate-lawyer/",
        "title": "Venezuela Real Estate Lawyer: What Foreign Buyers Should Ask",
        "description": "How foreign buyers should work with a Venezuela real estate lawyer: title review, seller authority, registry checks, powers of attorney, sanctions screening, and closing documents.",
        "keywords": "Venezuela real estate lawyer, Venezuela property lawyer, Venezuela title review, Venezuela real estate attorney, property due diligence Venezuela",
        "h1": "Venezuela Real Estate Lawyer",
        "answer": "A Venezuela real estate lawyer should help verify title, seller authority, registry status, security-zone issues, encumbrances, condominium debts, powers of attorney, sanctions-sensitive counterparties, and closing documentation before a buyer sends funds or signs binding documents.",
        "sections": [
            ("When to involve counsel", "Bring in independent Venezuelan counsel before paying a deposit, signing a reservation agreement, granting a power of attorney, or relying on seller-provided documents."),
            ("What a lawyer should review", "Core review items include the registered title instrument, chain of title, seller identity and authority, liens or encumbrances, inheritance or marital claims, condominium-fee status, utility debt, parking rights, SAREN requirements, and closing route."),
            ("Registry checks to expect", "The property should be checked at the Public Registry or SAREN office corresponding to its location. Counsel should confirm whether the registry record, cadastral information, encumbrance certificate, and seller documents align."),
            ("Questions to ask before hiring", "Ask whether the lawyer is independent from the seller and broker, which registry checks they will perform, how they document findings, what closing documents are required, and how they handle powers of attorney for foreign buyers."),
            ("What counsel cannot solve alone", "A lawyer cannot turn a weak listing into a good asset. Buyers still need property inspection, price comparison, sanctions screening, payment controls, and local judgment about building services and neighborhood liquidity."),
        ],
        "source_notes": GENERAL_SOURCE_NOTES + LEGAL_SOURCE_NOTES + LEGAL_PRACTICE_SOURCE_NOTES,
        "faqs": [
            ("Do foreign buyers need a Venezuela real estate lawyer?", "For any serious purchase, independent Venezuelan counsel is strongly recommended because title, registry, seller authority, and closing requirements need local review."),
            ("Should I use the seller's lawyer?", "Buyers should avoid relying only on seller-side counsel. Independent buyer counsel helps reduce conflicts of interest and verifies documents from the buyer's perspective."),
            ("Can a lawyer verify whether a listing is safe?", "A lawyer can review legal documents and seller authority, but buyers should also inspect condition, building services, price comparables, sanctions exposure, and payment logistics."),
        ],
    },
}


CITY_PAGES: dict[str, dict] = {
    "caracas": {
        "path": "/real-estate/caracas/",
        "title": "Caracas Real Estate: Prices, Listings & Buyer Guide",
        "h1": "Caracas Real Estate for Foreign Investors",
        "overview": "Caracas is the core business and premium apartment market for foreign buyers evaluating Venezuela. April 2026 public price indexes show Caracas as the country's deepest listed market, with 5,976 tracked residential listings, a $211,680 median residential asking price, and a $170,000 median apartment asking price; building services, parking, water reliability, security, and title quality drive much of the spread.",
        "neighborhoods": ["Los Caobos", "Los Ruices", "El Rosal", "Los Chorros", "El Marques", "Altamira", "Las Mercedes"],
        "keywords": "Caracas real estate, Caracas apartments for sale, Caracas Venezuela real estate",
    },
    "margarita-island": {
        "path": "/real-estate/margarita-island/",
        "title": "Margarita Island Real Estate: Beachfront & Vacation Property Guide",
        "h1": "Margarita Island Real Estate for Foreign Investors",
        "overview": "Margarita Island screens as a vacation, beachfront, and lifestyle-property market. April 2026 public price indexes report 299 active residential listings, a $95,000 median residential asking price, and an apartment subset with a $107,500 median asking price; buyers should separate beachfront and marina-style assets from non-beachfront Porlamar or inland island inventory.",
        "neighborhoods": ["Costa Azul", "Pampatar", "Porlamar", "Playa El Angel", "Playa Parguito", "El Morro", "Playa El Agua"],
        "keywords": "Margarita Island real estate, Isla Margarita real estate, beachfront property Venezuela",
    },
    "valencia": {
        "path": "/real-estate/valencia/",
        "title": "Valencia Venezuela Real Estate: Prices, Neighborhoods & Guide",
        "h1": "Valencia Venezuela Real Estate for Foreign Investors",
        "overview": "Valencia is a practical central-market screen for buyers comparing larger apartments, industrial-city demand, and lower headline prices than prime Caracas. April 2026 public price indexes report 281 active residential listings, a $125,000 median asking price, and an apartment subset with a $105,000 median asking price.",
        "neighborhoods": ["Prebo", "La Trigaleña", "El Parral", "Naguanagua", "Las Chimeneas", "Valle de Camoruco"],
        "keywords": "Valencia Venezuela real estate, Valencia apartments for sale, property in Valencia Venezuela",
    },
    "lecheria": {
        "path": "/real-estate/lecheria/",
        "title": "Lecheria Real Estate: Coastal Property Guide for Foreign Buyers",
        "h1": "Lecheria Real Estate for Foreign Investors",
        "overview": "Lecheria is a premium coastal/lifestyle market in eastern Venezuela, often compared by buyers seeking canal-front apartments, marina access, and a resort-like setting. Public English-language market data is thinner than Caracas, Margarita, or Valencia, so buyers should validate any Lecheria comparison with local broker quotes, title review, building-service checks, and recent comparable listings.",
        "neighborhoods": ["Av Diego Bautista Urbaneja", "El Morro", "Casco Central", "Complejo Turistico El Morro", "Venecia"],
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
    paths.extend(CORE_SEO_PATHS)
    paths.extend(page["path"] for page in GUIDES.values())
    paths.extend(page["path"] for page in CITY_PAGES.values())
    paths.extend(f"/real-estate/property/{listing.slug}/" for listing in LISTINGS)
    return list(dict.fromkeys(paths))


CORE_SEO_PATHS = (
    "/venezuela-real-estate/",
    "/venezuela-homes-for-sale/",
    "/caracas-real-estate/",
    "/caracas-apartments-for-sale/",
    "/margarita-island-real-estate/",
    "/buy-property-in-venezuela/",
    "/can-americans-buy-property-in-venezuela/",
    "/can-canadians-buy-property-in-venezuela/",
    "/venezuela-real-estate-prices/",
    "/venezuela-property-investment-guide/",
    "/venezuela-real-estate-risks/",
    "/venezuela-real-estate-lawyer/",
)
