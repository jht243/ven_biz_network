"""
Curated Caracas points of interest (landmarks) used as map overlays on
the safety-by-neighborhood tool. Categories are deliberately limited to
items that matter for foreign investors and business travellers:

  * hospital  — major private hospitals routinely used by expats
  * embassy   — currently-operating foreign missions
  * police    — primary municipal police HQs, plus national CICPC
  * airport   — Simón Bolívar International (SVMI / CCS)

Coordinates are anchored to the building or street intersection named in
the source. Each entry carries an inline citation so a reviewer can see
where the location came from. NOT a directory — this is a small, curated
overlay sized to be useful on a map without becoming visual noise.

If you add to this file, prefer institutions with stable, public
addresses (official ministry / embassy pages, Wikipedia, OSM via
mapcarta / wikimapia). Avoid copying coordinates from third-party
listings that don't cite a source.
"""

from __future__ import annotations


CARACAS_LANDMARKS: list[dict] = [
    # ---- Hospitals (major private, English-capable) ----
    {
        "name": "Hospital de Clínicas Caracas",
        "category": "hospital",
        "area": "San Bernardino",
        "note": "Large private hospital. 24/7 emergency. Commonly used by foreign visitors and embassies for referrals.",
        # Source: mapcarta (OSM way 227368315). Av. Panteón con Av. Alameda.
        "lat": 10.5102,
        "lng": -66.8993,
    },
    {
        "name": "Centro Médico de Caracas",
        "category": "hospital",
        "area": "San Bernardino",
        "note": "Long-established private hospital. 24/7 emergency. Often paired with HCC as a fallback.",
        # Source: official site + Wikimapia. Same San Bernardino cluster.
        "lat": 10.5119,
        "lng": -66.8970,
    },
    {
        "name": "Policlínica Metropolitana",
        "category": "hospital",
        "area": "Caurimare",
        "note": "Private hospital in eastern Caracas, more accessible from Las Mercedes / Altamira than the San Bernardino cluster.",
        # Source: WorldPlaces GPS. Calle A-1, Urb. Caurimare.
        "lat": 10.4770,
        "lng": -66.8330,
    },
    {
        "name": "Centro Médico Docente La Trinidad",
        "category": "hospital",
        "area": "La Trinidad / El Hatillo",
        "note": "Tertiary-care private hospital and teaching facility in southeast Caracas. Long drive from central business districts.",
        # Source: official site (cmdlt.edu.ve) + mapcarta. Av. Intercomunal La Trinidad – El Hatillo.
        "lat": 10.4220,
        "lng": -66.8540,
    },

    # ---- Embassies (curated subset of Caracas-resident missions; not exhaustive) ----
    # Coordinates come from each ministry's official site, Wikidata, or
    # the named street intersection. Most embassies cluster across just
    # four neighborhoods (La Castellana, Campo Alegre, Las Mercedes,
    # El Rosal); markers within ~150 m of the building are sufficient
    # at the map's default zoom.
    {
        "name": "Embassy of the United States",
        "category": "embassy",
        "area": "Colinas de Valle Arriba",
        "note": "Calle F con Calle Suapure, Urb. Colinas de Valle Arriba. Operations were suspended in March 2019; the American flag was raised again on 15 March 2026 (per ABC News / Wikipedia). Consular services may still be ramping back up — verify status before relying on in-person services.",
        # Source: Wikidata Q20994812 (precise: 10°28′41″N 66°52′16″W).
        "lat": 10.4781,
        "lng": -66.8714,
    },
    {
        "name": "Embassy of the United Kingdom",
        "category": "embassy",
        "area": "La Castellana",
        "note": "Torre La Castellana, Piso 11, Av. Principal de la Castellana (Av. Eugenio Mendoza). Public access by appointment only.",
        # Source: gov.uk/world/organisations/british-embassy-venezuela.
        "lat": 10.5045,
        "lng": -66.8550,
    },
    {
        "name": "Embassy of Germany",
        "category": "embassy",
        "area": "La Castellana",
        "note": "Edif. La Castellana, Piso 10, Av. Eugenio Mendoza con Av. José Ángel Lamas.",
        # Source: caracas.diplo.de.
        "lat": 10.5050,
        "lng": -66.8552,
    },
    {
        "name": "Embassy of Spain",
        "category": "embassy",
        "area": "La Castellana",
        "note": "Av. Mohedano, between 1ra and 2da Transversal, Quinta Embajada de España. Consulate General is separately at Edif. Bancaracas, Plaza La Castellana.",
        # Source: exteriores.gob.es (Spanish foreign ministry).
        "lat": 10.5042,
        "lng": -66.8533,
    },
    {
        "name": "Embassy of Italy",
        "category": "embassy",
        "area": "El Rosal",
        "note": "Edif. Atrium, P.H., Calle Sorocaima, between Avenidas Tamanaco and Venezuela. Consulate General is at a separate address in La Castellana.",
        # Source: ambcaracas.esteri.it.
        "lat": 10.4920,
        "lng": -66.8590,
    },
    {
        "name": "Embassy of France",
        "category": "embassy",
        "area": "Las Mercedes",
        "note": "Calle Madrid con Calle Trinidad, Las Mercedes.",
        # Source: ve.diplomatie.gouv.fr.
        "lat": 10.4855,
        "lng": -66.8584,
    },
    {
        "name": "Embassy of Brazil",
        "category": "embassy",
        "area": "La Castellana",
        "note": "Centro Gerencial Mohedano, Piso 6, Av. Mohedano con Calle Los Chaguaramos. Brazil also acts as protecting power for several missions whose home countries have suspended Caracas operations.",
        # Source: gov.br/mre/embaixada-caracas.
        "lat": 10.5046,
        "lng": -66.8541,
    },
    {
        "name": "Embassy of Mexico",
        "category": "embassy",
        "area": "Las Mercedes",
        "note": "Edif. Centro Río de Janeiro, Nivel P.H., Av. Río de Janeiro con Calle Trinidad, Las Mercedes.",
        # Source: embamex.sre.gob.mx/venezuela.
        "lat": 10.4858,
        "lng": -66.8585,
    },
    {
        "name": "Embassy of Colombia",
        "category": "embassy",
        "area": "Campo Alegre",
        "note": "Torre Credival, Piso 11, 2da Avenida de Campo Alegre. Consulate General is at a separate address in El Rosal.",
        # Source: Wikipedia / cancilleria.gov.co.
        "lat": 10.4995,
        "lng": -66.8585,
    },
    {
        "name": "Embassy of China (P.R.C.)",
        "category": "embassy",
        "area": "Las Mercedes",
        "note": "Av. Orinoco con Calle Monterrey, Urb. Las Mercedes (Baruta).",
        # Source: ve.china-embassy.gov.cn.
        "lat": 10.4838,
        "lng": -66.8617,
    },

    # ---- Police ----
    {
        "name": "Polichacao — Sede Principal",
        "category": "police",
        "area": "Chacao",
        "note": "Municipal police HQ, Calle Pantín, Chacao Industrial Zone (behind C.C. Sambil). Generally considered the most professional municipal force in Caracas.",
        # Source: Polichacao official site + Waze listing.
        "lat": 10.4960,
        "lng": -66.8595,
    },
    {
        "name": "CICPC — Sede Principal",
        "category": "police",
        "area": "Parque Carabobo",
        "note": "Cuerpo de Investigaciones Científicas, Penales y Criminalísticas. National investigative police; first stop for filing a denuncia after a serious incident.",
        # Source: Av. Urdaneta, Parque Carabobo (publicly known HQ location).
        "lat": 10.5070,
        "lng": -66.9083,
    },

    # ---- Airport ----
    {
        "name": "Aeropuerto Internacional Simón Bolívar (SVMI / CCS)",
        "category": "airport",
        "area": "Maiquetía, La Guaira",
        "note": "Sole international gateway to Caracas. ~30 km from city centre; pre-arrange a vetted driver and travel during daylight when possible.",
        # Source: ICAO/IATA published. Standard SVMI ARP.
        "lat": 10.6011,
        "lng": -66.9911,
    },
]


def list_caracas_landmarks() -> list[dict]:
    return CARACAS_LANDMARKS
