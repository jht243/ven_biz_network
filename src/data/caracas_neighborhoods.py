"""
Curated Caracas neighborhood safety, accommodation, and business-suitability
dataset. Scores are 1 (highest concern) to 5 (most stable for foreign
visitors and businesses), based on published State Department / FCDO
context, OSAC reporting trends, and aggregated expat-community feedback.

This is NOT a real-time crime feed — it is a one-page reference for
investors and business travellers planning a trip. Always confirm with
local security advisors before travel.

Update when local on-the-ground sources signal a sustained shift.

Coordinates are anchored to a recognisable landmark inside each
neighborhood (plaza, metro station, or town centre) so the marker
on the safety map sits in the place a reader would expect, not on
the geometric centroid of an irregular polygon. Sources verified
against Wikipedia, OpenStreetMap (mapcarta), and Wikimapia.
"""

from __future__ import annotations


CARACAS_NEIGHBORHOODS: list[dict] = [
    {
        "name": "Las Mercedes",
        "municipality": "Baruta",
        "safety_score": 4,
        "category": "Business / dining hub",
        "summary": "The closest thing Caracas has to an international business district. Concentration of corporate offices, embassies, banks, and upscale restaurants. Reasonable security infrastructure during business hours.",
        "business_use": "Default district for foreign-investor meetings, corporate offices, and after-hours dining. Most major hotels routinely send drivers here.",
        "what_to_avoid": "Avoid walking alone after dark; use authorised hotel transport.",
        # Anchor: Av. Principal de Las Mercedes, between Río Guaire and
        # Av. Río de Janeiro (OSM / mapcarta). Old value (10.4688) was the
        # Wikipedia stub coordinate and landed ~1.6 km south in Santa Rosa de Lima.
        "lat": 10.4824,
        "lng": -66.8602,
    },
    {
        "name": "Altamira",
        "municipality": "Chacao",
        "safety_score": 4,
        "category": "Business / residential",
        "summary": "Long-standing diplomatic and residential district with a relatively visible private-security presence. Plaza Altamira has been a focal point of historical political demonstrations.",
        "business_use": "Common location for law firms, advisory shops, and family-office representatives. Well served by quality residential rentals.",
        "what_to_avoid": "Stay alert during politically charged periods; demonstrations have flared at Plaza Altamira.",
        # Anchor: Plaza Francia / Plaza Altamira (the obelisk), per Wikipedia.
        "lat": 10.4964,
        "lng": -66.8490,
    },
    {
        "name": "Chacao",
        "municipality": "Chacao",
        "safety_score": 4,
        "category": "Commercial / mixed-use",
        "summary": "Active commercial municipality covering several adjoining neighborhoods (El Rosal, La Castellana, Country Club). Generally considered the most operationally functional part of Caracas.",
        "business_use": "Banking sector hub. Main branches of national and international banks operating in Venezuela. Many fintechs and consultancies headquartered here.",
        "what_to_avoid": "Express kidnapping risk in evening hours; do not flag street taxis.",
        # Anchor: Plaza Bolívar de Chacao (old town / Iglesia San José),
        # west of Altamira so the two markers don't sit on top of each other.
        "lat": 10.4956,
        "lng": -66.8553,
    },
    {
        "name": "La Castellana",
        "municipality": "Chacao",
        "safety_score": 4,
        "category": "Upscale residential",
        "summary": "Upscale residential area with a notable concentration of business-class hotels. Good walkability for the area in daylight hours.",
        "business_use": "Hotels here host most foreign-investor delegations. Good choice for short-stay accommodation.",
        "what_to_avoid": "Use only authorised transport between hotel and meetings.",
        # Anchor: Av. Principal de La Castellana (north-west of Plaza Altamira).
        "lat": 10.5043,
        "lng": -66.8562,
    },
    {
        "name": "El Hatillo",
        "municipality": "El Hatillo",
        "safety_score": 4,
        "category": "Tourist / colonial",
        "summary": "Colonial-era historic town centre on the eastern edge of metropolitan Caracas. Popular for daytime tourism, restaurants, and craft markets.",
        "business_use": "Limited business use; pleasant for off-day excursions for visitors.",
        "what_to_avoid": "Roads connecting to El Hatillo can be congested; avoid driving at night.",
        # Anchor: Plaza Bolívar de El Hatillo (colonial casco).
        "lat": 10.4252,
        "lng": -66.8257,
    },
    {
        "name": "Los Palos Grandes",
        "municipality": "Chacao",
        "safety_score": 3,
        "category": "Residential / mixed",
        "summary": "Established residential area popular with the diplomatic community and middle-class professionals. Generally functional services.",
        "business_use": "Practical for medium-term rentals; some boutique offices and consultancies.",
        "what_to_avoid": "Do not display electronics or valuables in public.",
        # Anchor: Plaza Los Palos Grandes (4ta Transversal).
        "lat": 10.5005,
        "lng": -66.8451,
    },
    {
        "name": "Sabana Grande",
        "municipality": "Libertador",
        "safety_score": 2,
        "category": "Commercial / dense urban",
        "summary": "Historic commercial spine with mixed economic activity. Has experienced significant decline; pickpocketing and street crime are persistent.",
        "business_use": "Limited investor relevance; some legacy retail interests.",
        "what_to_avoid": "Avoid all but essential daytime visits; keep valuables out of sight.",
        # Anchor: Boulevard de Sabana Grande (Wikipedia centroid).
        "lat": 10.4893,
        "lng": -66.8737,
    },
    {
        "name": "El Centro / Catedral",
        "municipality": "Libertador",
        "safety_score": 2,
        "category": "Government / historic",
        "summary": "Historic colonial centre and seat of national government (Capitolio, Miraflores, Asamblea Nacional). Heavy security presence around government buildings, but petty crime risk elevated.",
        "business_use": "Necessary visits to the National Assembly, ministries, and Gaceta Oficial. Always with local fixer.",
        "what_to_avoid": "No casual sightseeing; visits should be purpose-driven and chaperoned.",
        # Anchor: Plaza Bolívar de Caracas (next to the Catedral).
        "lat": 10.5061,
        "lng": -66.9146,
    },
    {
        "name": "Petare",
        "municipality": "Sucre",
        "safety_score": 1,
        "category": "Dense informal settlement",
        "summary": "One of Latin America's largest informal settlements. High homicide rates, active gang presence; not safe for foreign visitors at any time.",
        "business_use": "None.",
        "what_to_avoid": "Do not enter — including by metro or taxi pass-through.",
        # Anchor: Plaza Sucre / casco histórico de Petare. Old value put the
        # marker ~1.3 km west into Macaracuay; corrected per Wikipedia/OSM.
        "lat": 10.4768,
        "lng": -66.8079,
    },
    {
        "name": "Catia",
        "municipality": "Libertador",
        "safety_score": 1,
        "category": "Dense urban / informal",
        "summary": "Western Caracas working-class district with persistent gang activity and historic security challenges.",
        "business_use": "None.",
        "what_to_avoid": "Do not enter except with vetted local security on a documented purpose.",
        # Anchor: Pérez Bonalde / Boulevard de Catia (Wikimapia centroid).
        "lat": 10.5172,
        "lng": -66.9550,
    },
    {
        "name": "23 de Enero",
        "municipality": "Libertador",
        "safety_score": 1,
        "category": "Politicised housing project",
        "summary": "Dense housing project with active organised political collectives ('colectivos') and elevated crime metrics. High politicisation.",
        "business_use": "None.",
        "what_to_avoid": "No visits.",
        # Anchor: Parroquia 23 de Enero centroid (OSM / Wikidata).
        "lat": 10.5076,
        "lng": -66.9312,
    },
    {
        "name": "Maiquetía / Catia La Mar",
        "municipality": "Vargas (La Guaira)",
        "safety_score": 2,
        "category": "Airport / coastal",
        "summary": "Coastal corridor connecting Simón Bolívar International Airport to Caracas. Highway robbery risk on the airport-to-city road, especially at night.",
        "business_use": "Unavoidable transit on arrival/departure.",
        "what_to_avoid": "Never drive yourself between airport and city. Pre-arrange a vetted driver and travel during daylight when possible.",
        # Anchor: Maiquetía town centre (just east of SVMI airport).
        "lat": 10.6033,
        "lng": -66.9914,
    },
]


def list_caracas_neighborhoods() -> list[dict]:
    return CARACAS_NEIGHBORHOODS
