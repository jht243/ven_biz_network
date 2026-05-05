"""
SEO landing pages for travelers who search the exact ministry document names
(e.g. "planilla de solicitud de visa", "declaracion jurada visa venezolana").

Rendered by templates/visa_document_landing.html.j2 + server routes.
"""

from __future__ import annotations

# User-facing line for the planilla guide, hub cards, and cross-links.
PLANILLA_HERO_LINE = "Planilla de Solicitud de Visa Venezuela"


PLANILLA_DE_SOLICITUD_DE_VISA: dict = {
    "canonical_path": "/planilla-de-solicitud-de-visa",
    "title": "Planilla de Solicitud de Visa Venezuela: Free PDF Generator",
    "description": (
        "Free planilla de solicitud de visa Venezuela PDF generator. Learn what "
        "the form asks for, fill it with English prompts, print Spanish labels, "
        "and upload it to Cancillería Digital."
    ),
    "keywords": (
        "planilla de solicitud de visa, planilla de solicitud de visa Venezuela, "
        "planilla visa Venezuela, MPPRE planilla, Cancillería Digital planilla, "
        "solicitud de visa Venezuela formulario"
    ),
    "kicker": "Venezuela e-visa · MPPRE paperwork",
    "h1": PLANILLA_HERO_LINE,
    "lead": (
        "When Cancillería Digital asks for the official "
        "<strong>planilla de solicitud de visa</strong>, use this page as your map. "
        "The free generator opens with English prompts on screen and prints a "
        "Spanish-labelled PDF for your Venezuela visa file."
    ),
    "disclaimer": (
        "Independent preparation aid: this is not an official Venezuelan government "
        "form. Verify the current Cancillería Digital or consulate instructions "
        "before uploading any document."
    ),
    "preview": {
        "src": "/static/visa-planilla-de-solicitud-de-visa-preview.svg",
        "alt": "Anonymized preview of a generated Planilla de Solicitud de Visa Venezuela PDF",
        "caption": "Anonymized preview of the printable Spanish-labelled PDF.",
    },
    "sections": [
        {
            "h2": "What the planilla de solicitud de visa is",
            "paragraphs": [
                (
                    "The planilla groups your identity, passport, address, trip "
                    "dates, lodging in Venezuela, and similar fields into numbered "
                    "sections so a consular officer or the online portal can scan "
                    "the file quickly. Wording can change when MPPRE updates forms, "
                    "so compare your PDF to the latest instructions in the live portal "
                    "if something looks different."
                ),
            ],
        },
        {
            "h2": "Who needs it",
            "paragraphs": [
                (
                    "Use it whenever Cancillería Digital, an embassy, or a consulate "
                    "asks for a planilla, visa application form, solicitud de visa, "
                    "or MPPRE application form. Tourist and business applicants most "
                    "often see it next to passport, itinerary, lodging, and proof-of-funds uploads."
                ),
            ],
        },
        {
            "h2": "What fields appear on the form",
            "paragraphs": [
                (
                    "Expect sections for visa type, application date, full name, nationality, "
                    "birth details, passport number and dates, home address, travel purpose, "
                    "arrival and departure dates, airline or flight details, lodging in "
                    "Venezuela, financial guarantor, local contact, prior visits, and signature."
                ),
            ],
        },
        {
            "h2": "How to fill it in English and print Spanish labels",
            "paragraphs": [
                (
                    "Gather your passport scan, itinerary, hotel or invitation letter, "
                    "and the other items your visa class requires before you sit down "
                    "to type the planilla because you will copy those details in."
                ),
                (
                    "Use a desktop browser. Fill the fields, then choose "
                    "<strong>Print</strong> -> <strong>Save as PDF</strong> in Chrome, "
                    "Edge, or Safari. Upload that PDF where the portal asks for the "
                    "planilla attachment."
                ),
            ],
        },
        {
            "h2": "Common mistakes to avoid",
            "paragraphs": [
                (
                    "Match your passport exactly: names, passport number, nationality, "
                    "and dates should not drift from your scan. Do not leave obvious "
                    "fields blank, choose the right visa type, and make lodging and "
                    "travel dates line up with the reservation documents you upload."
                ),
            ],
        },
        {
            "h2": "Related documents",
            "paragraphs": [
                (
                    "Most tourist and business files also need a "
                    "<a href=\"/declaracion-jurada-visa-venezolana\">declaración jurada</a> "
                    "(short sworn statement in Spanish). The "
                    "<a href=\"/apply-for-venezuelan-visa\">full visa application guide (2026)</a> "
                    "walks the online process end-to-end."
                ),
            ],
        },
    ],
    "tool_url": "/apply-for-venezuelan-visa/planilla",
    "tool_label": "Generate the Planilla PDF",
    "faq": [
        {
            "q": "What is the planilla de solicitud de visa?",
            "a": (
                "The planilla de solicitud de visa is the structured Venezuela visa application "
                "form that collects your identity, passport, travel, lodging, contact, and "
                "signature details. Applicants upload or present it when Cancillería Digital "
                "or a Venezuelan consulate asks for the visa application form."
            ),
        },
        {
            "q": "How do I fill out the Venezuela visa application form?",
            "a": (
                "Copy the details exactly from your passport and supporting documents, then "
                "print or save the completed form as a PDF. Our generator lets you type with "
                "English prompts while the printable PDF uses Spanish field labels."
            ),
        },
        {
            "q": "Is this an official Venezuelan government form?",
            "a": (
                "No. Caracas Research provides an independent preparation aid that mirrors "
                "the information travelers are commonly asked to provide. Always verify the "
                "current official Cancillería Digital or consulate instructions before uploading."
            ),
        },
    ],
    "nav_links": [
        {
            "href": "/declaracion-jurada-visa-venezolana",
            "label": "Declaración jurada (visa venezolana)",
        },
        {
            "href": "/apply-for-venezuelan-visa",
            "label": "How To Apply For A Venezuelan Visa (2026)",
        },
    ],
}

DECLARACION_JURADA_VISA_VENEZOLANA: dict = {
    "canonical_path": "/declaracion-jurada-visa-venezolana",
    "title": "Declaración Jurada Visa Venezolana: Free PDF Generator",
    "description": (
        "Free declaración jurada visa venezolana PDF generator. Learn what the "
        "sworn no-criminal-record statement means, fill it with English prompts, "
        "and print a Spanish PDF."
    ),
    "keywords": (
        "declaracion jurada visa venezolana, declaración jurada Venezuela visa, "
        "declaración jurada visa venezuelana, sworn statement Venezuela visa, "
        "antecedentes penales declaración Venezuela"
    ),
    "kicker": "Venezuela e-visa · Sworn statement",
    "h1": "Declaración jurada (visa venezolana)",
    "lead": (
        "A <strong>declaración jurada</strong> for a Venezuelan visa is a sworn "
        "statement in <strong>Spanish</strong> that says you do not have a "
        "criminal record in your home country or elsewhere. Portals and consulates "
        "attach it next to your passport copy and planilla."
    ),
    "disclaimer": (
        "Independent preparation aid: this is not an official government form or "
        "legal advice. Do not sign a false statement, and verify whether your "
        "visa class also requires a separate police or criminal-record certificate."
    ),
    "preview": {
        "src": "/static/declaracion-jurada-visa-venezolana-preview.svg",
        "alt": "Anonymized preview of a generated Declaración Jurada Visa Venezolana PDF",
        "caption": "Anonymized preview of the printable sworn-statement PDF.",
    },
    "sections": [
        {
            "h2": "When you need it",
            "paragraphs": [
                (
                    "Requirements vary by visa class and channel, including online filing "
                    "versus an in-person consulate appointment. If Cancillería Digital or "
                    "your consulate checklist lists declaración jurada, assume you must "
                    "upload a signed PDF unless they explicitly waive it."
                ),
            ],
        },
        {
            "h2": "Does it replace a criminal-record certificate?",
            "paragraphs": [
                (
                    "Usually, no. A declaración jurada is your sworn statement. "
                    "A police certificate or antecedentes penales is a separate "
                    "government-issued record. If your checklist asks for both, "
                    "upload both; if it asks only for one, follow the checklist."
                ),
            ],
        },
        {
            "h2": "What the no-criminal-record wording means",
            "paragraphs": [
                (
                    "The standard wording says, under oath, that you do not have "
                    "criminal records in your home country or any other country, "
                    "and that the statement is provided for a Venezuela visa application. "
                    "Read it before signing, because false declarations can carry legal consequences."
                ),
            ],
        },
        {
            "h2": "How to complete it safely",
            "paragraphs": [
                (
                    "Read the Spanish body once so you understand what you are signing. "
                    "Fill your name, nationality, and passport number carefully so "
                    "they match your passport scan character-for-character."
                ),
                (
                    "You may type your signature as your full legal name. Our tool "
                    "renders it in cursive for PDF. You can also print blank, sign "
                    "in ink, and scan. Do not alter the core legal wording unless a "
                    "lawyer tells you to."
                ),
            ],
        },
        {
            "h2": "When to notarize or ask for legal help",
            "paragraphs": [
                (
                    "If a consulate specifically asks for a notarized sworn statement, "
                    "use the consulate's instruction rather than a plain typed PDF. "
                    "Ask a lawyer or consular officer before signing if you have any "
                    "prior arrest, conviction, expungement, pending case, or uncertainty "
                    "about the statement."
                ),
            ],
        },
        {
            "h2": "Related documents",
            "paragraphs": [
                (
                    "Pair this with "
                    f"<a href=\"/planilla-de-solicitud-de-visa\">{PLANILLA_HERO_LINE}</a> "
                    "and follow the full "
                    "<a href=\"/apply-for-venezuelan-visa\">How To Apply For A Venezuelan Visa (2026)</a> "
                    "guide for fees, timeline, and portal steps."
                ),
            ],
        },
    ],
    "tool_url": "/apply-for-venezuelan-visa/declaracion-jurada",
    "tool_label": "Generate the Declaración Jurada PDF",
    "faq": [
        {
            "q": "What is a declaración jurada for a Venezuelan visa?",
            "a": (
                "A declaración jurada is a sworn statement, usually in Spanish, that "
                "supports your Venezuela visa file. For this use case it commonly "
                "states that you do not have a criminal record in your home country "
                "or elsewhere."
            ),
        },
        {
            "q": "Can I use a typed signature?",
            "a": (
                "A typed signature may be acceptable when the portal asks for a simple "
                "printable PDF, but some consulates may require a wet signature or "
                "notarization. If the checklist specifies how to sign, follow that instruction."
            ),
        },
        {
            "q": "Is this an official Venezuelan government form?",
            "a": (
                "No. This generator is an independent preparation aid, not an official "
                "government form or legal advice. Verify the current requirement and "
                "do not sign any statement that is untrue."
            ),
        },
    ],
    "nav_links": [
        {
            "href": "/planilla-de-solicitud-de-visa",
            "label": PLANILLA_HERO_LINE,
        },
        {
            "href": "/apply-for-venezuelan-visa",
            "label": "How To Apply For A Venezuelan Visa (2026)",
        },
    ],
}


def get_planilla_landing() -> dict:
    return PLANILLA_DE_SOLICITUD_DE_VISA


def get_declaracion_landing() -> dict:
    return DECLARACION_JURADA_VISA_VENEZOLANA
