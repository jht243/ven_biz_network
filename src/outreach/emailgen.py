"""Generate and send backlink outreach emails."""

from __future__ import annotations

import html
import logging
from datetime import datetime

from src.config import settings
from src.models import (
    EmailStatus,
    OutreachEmail,
    OutreachStatus,
    Prospect,
    SessionLocal,
    init_db,
)
from src.newsletter import send_email as send_provider_email

logger = logging.getLogger(__name__)

TEMPLATES: dict[str, dict[str, str]] = {
    "en": {
        "subject": "Cross-promotion opportunity — {site_name} + Caracas Research",
        "body": (
            "Hi {greeting},\n\n"
            "I came across your page on {topic} and noticed you cover "
            "Venezuela-related topics.\n\n"
            "I run Caracas Research ({our_url}), which publishes Venezuela-focused "
            "research on travel, investment, sanctions, and business risk.\n\n"
            "I'd love to propose a cross-promotion: we would add a link to your "
            "site from our relevant page, and in return you would link to ours "
            "from yours.\n\n"
            "Your page: {source_url}\n"
            "Our page: {target_url}\n\n"
            "This way both of our audiences benefit from the additional resource. "
            "Would you be interested?\n\n"
            "Best,\nJonathan"
        ),
        "followup_1": (
            "Hi {greeting},\n\n"
            "Just following up on my cross-promotion idea. Since your readers "
            "already look for information on {topic}, linking to each other's "
            "content could be valuable for both sites.\n\n"
            "Your page: {source_url}\n"
            "Our page: {target_url}\n\n"
            "Happy to discuss details.\n\n"
            "Best,\nJonathan"
        ),
        "followup_2": (
            "Hi {greeting},\n\n"
            "Last note from me. If a link exchange between {site_name} and "
            "Caracas Research makes sense for your readers, I'm happy to set it "
            "up on our end first.\n\n"
            "Our page: {target_url}\n\n"
            "No worries if it's not a fit.\n\n"
            "Best,\nJonathan"
        ),
    },
    "es": {
        "subject": "Promoción cruzada — {site_name} + Caracas Research",
        "body": (
            "Hola {greeting},\n\n"
            "Encontré su página sobre {topic} y noté que cubren "
            "temas relacionados con Venezuela.\n\n"
            "Dirijo Caracas Research ({our_url}), que publica investigación "
            "enfocada en Venezuela sobre viajes, inversión, sanciones y riesgo "
            "empresarial.\n\n"
            "Me gustaría proponer una promoción cruzada: nosotros agregaríamos "
            "un enlace a su sitio desde nuestra página relevante, y a cambio "
            "ustedes enlazarían a la nuestra desde la suya.\n\n"
            "Su página: {source_url}\n"
            "Nuestra página: {target_url}\n\n"
            "De esta forma ambas audiencias se benefician del recurso adicional. "
            "¿Les interesaría?\n\n"
            "Saludos,\nJonathan"
        ),
        "followup_1": (
            "Hola {greeting},\n\n"
            "Solo doy seguimiento a mi propuesta de promoción cruzada. Ya que "
            "sus lectores buscan información sobre {topic}, enlazar mutuamente "
            "nuestro contenido podría ser valioso para ambos sitios.\n\n"
            "Su página: {source_url}\n"
            "Nuestra página: {target_url}\n\n"
            "Con gusto conversamos los detalles.\n\n"
            "Saludos,\nJonathan"
        ),
        "followup_2": (
            "Hola {greeting},\n\n"
            "Última nota de mi parte. Si un intercambio de enlaces entre "
            "{site_name} y Caracas Research tiene sentido para sus lectores, "
            "con gusto lo configuro primero de nuestro lado.\n\n"
            "Nuestra página: {target_url}\n\n"
            "Sin problema si no es un buen momento.\n\n"
            "Saludos,\nJonathan"
        ),
    },
    "it": {
        "subject": "Promozione incrociata — {site_name} + Caracas Research",
        "body": (
            "Ciao {greeting},\n\n"
            "Ho trovato la vostra pagina su {topic} e ho notato che trattate "
            "argomenti legati al Venezuela.\n\n"
            "Gestisco Caracas Research ({our_url}), che pubblica ricerche sul "
            "Venezuela riguardanti viaggi, investimenti, sanzioni e rischio "
            "d'impresa.\n\n"
            "Vorrei proporre una promozione incrociata: noi aggiungeremmo un "
            "link al vostro sito dalla nostra pagina pertinente, e in cambio "
            "voi fareste lo stesso con la nostra.\n\n"
            "La vostra pagina: {source_url}\n"
            "La nostra pagina: {target_url}\n\n"
            "In questo modo entrambi i nostri lettori beneficerebbero della "
            "risorsa aggiuntiva. Vi interesserebbe?\n\n"
            "Cordiali saluti,\nJonathan"
        ),
        "followup_1": (
            "Ciao {greeting},\n\n"
            "Torno sulla mia proposta di promozione incrociata. Dato che i "
            "vostri lettori cercano informazioni su {topic}, collegare "
            "reciprocamente i nostri contenuti potrebbe essere utile per entrambi.\n\n"
            "La vostra pagina: {source_url}\n"
            "La nostra pagina: {target_url}\n\n"
            "Sono disponibile a discutere i dettagli.\n\n"
            "Cordiali saluti,\nJonathan"
        ),
        "followup_2": (
            "Ciao {greeting},\n\n"
            "Ultima nota da parte mia. Se uno scambio di link tra {site_name} "
            "e Caracas Research ha senso per i vostri lettori, sono felice di "
            "configurarlo prima dal nostro lato.\n\n"
            "La nostra pagina: {target_url}\n\n"
            "Nessun problema se non è il momento giusto.\n\n"
            "Cordiali saluti,\nJonathan"
        ),
    },
    "pt": {
        "subject": "Promoção cruzada — {site_name} + Caracas Research",
        "body": (
            "Olá {greeting},\n\n"
            "Encontrei sua página sobre {topic} e notei que vocês cobrem "
            "temas relacionados à Venezuela.\n\n"
            "Eu administro o Caracas Research ({our_url}), que publica pesquisas "
            "focadas na Venezuela sobre viagens, investimento, sanções e risco "
            "empresarial.\n\n"
            "Gostaria de propor uma promoção cruzada: nós adicionaríamos um "
            "link para o seu site na nossa página relevante, e em troca vocês "
            "fariam o mesmo com a nossa.\n\n"
            "Sua página: {source_url}\n"
            "Nossa página: {target_url}\n\n"
            "Dessa forma, ambas as audiências se beneficiam do recurso "
            "adicional. Teriam interesse?\n\n"
            "Abraços,\nJonathan"
        ),
        "followup_1": (
            "Olá {greeting},\n\n"
            "Só passando para dar seguimento à minha proposta de promoção "
            "cruzada. Como seus leitores já buscam informações sobre {topic}, "
            "vincular mutuamente nosso conteúdo pode ser valioso para ambos.\n\n"
            "Sua página: {source_url}\n"
            "Nossa página: {target_url}\n\n"
            "Fico à disposição para discutir detalhes.\n\n"
            "Abraços,\nJonathan"
        ),
        "followup_2": (
            "Olá {greeting},\n\n"
            "Última nota da minha parte. Se uma troca de links entre "
            "{site_name} e Caracas Research faz sentido para seus leitores, "
            "fico feliz em configurar do nosso lado primeiro.\n\n"
            "Nossa página: {target_url}\n\n"
            "Sem problemas se não for o momento.\n\n"
            "Abraços,\nJonathan"
        ),
    },
    "fr": {
        "subject": "Promotion croisée — {site_name} + Caracas Research",
        "body": (
            "Bonjour {greeting},\n\n"
            "J'ai trouvé votre page sur {topic} et j'ai remarqué que vous "
            "traitez de sujets liés au Venezuela.\n\n"
            "Je dirige Caracas Research ({our_url}), qui publie des recherches "
            "sur le Venezuela dans les domaines du voyage, de l'investissement, "
            "des sanctions et du risque commercial.\n\n"
            "J'aimerais proposer une promotion croisée : nous ajouterions un "
            "lien vers votre site depuis notre page pertinente, et en retour "
            "vous feriez de même avec la nôtre.\n\n"
            "Votre page : {source_url}\n"
            "Notre page : {target_url}\n\n"
            "Ainsi, nos deux audiences bénéficieraient de la ressource "
            "supplémentaire. Cela vous intéresserait ?\n\n"
            "Cordialement,\nJonathan"
        ),
        "followup_1": (
            "Bonjour {greeting},\n\n"
            "Je reviens sur ma proposition de promotion croisée. Puisque vos "
            "lecteurs cherchent déjà des informations sur {topic}, un lien "
            "réciproque pourrait profiter à nos deux sites.\n\n"
            "Votre page : {source_url}\n"
            "Notre page : {target_url}\n\n"
            "Je reste disponible pour en discuter.\n\n"
            "Cordialement,\nJonathan"
        ),
        "followup_2": (
            "Bonjour {greeting},\n\n"
            "Dernier message de ma part. Si un échange de liens entre "
            "{site_name} et Caracas Research convient à vos lecteurs, je suis "
            "prêt à le mettre en place de notre côté en premier.\n\n"
            "Notre page : {target_url}\n\n"
            "Pas de souci si ce n'est pas le bon moment.\n\n"
            "Cordialement,\nJonathan"
        ),
    },
    "de": {
        "subject": "Cross-Promotion — {site_name} + Caracas Research",
        "body": (
            "Hallo {greeting},\n\n"
            "ich bin auf Ihre Seite zum Thema {topic} gestoßen und habe "
            "bemerkt, dass Sie Venezuela-bezogene Themen behandeln.\n\n"
            "Ich leite Caracas Research ({our_url}), das Venezuela-fokussierte "
            "Forschung zu Reisen, Investitionen, Sanktionen und "
            "Geschäftsrisiken veröffentlicht.\n\n"
            "Ich möchte eine Cross-Promotion vorschlagen: Wir würden einen "
            "Link zu Ihrer Seite auf unserer relevanten Seite setzen, und im "
            "Gegenzug würden Sie dasselbe mit unserer tun.\n\n"
            "Ihre Seite: {source_url}\n"
            "Unsere Seite: {target_url}\n\n"
            "So profitieren beide Lesergruppen von der zusätzlichen Ressource. "
            "Hätten Sie Interesse?\n\n"
            "Mit freundlichen Grüßen,\nJonathan"
        ),
        "followup_1": (
            "Hallo {greeting},\n\n"
            "ich komme auf meinen Cross-Promotion-Vorschlag zurück. Da Ihre "
            "Leser bereits nach Informationen zu {topic} suchen, könnte eine "
            "gegenseitige Verlinkung für beide Seiten wertvoll sein.\n\n"
            "Ihre Seite: {source_url}\n"
            "Unsere Seite: {target_url}\n\n"
            "Gerne besprechen wir die Details.\n\n"
            "Mit freundlichen Grüßen,\nJonathan"
        ),
        "followup_2": (
            "Hallo {greeting},\n\n"
            "letzte Nachricht von mir. Falls ein Linktausch zwischen "
            "{site_name} und Caracas Research für Ihre Leser sinnvoll ist, "
            "richte ich ihn gerne zuerst auf unserer Seite ein.\n\n"
            "Unsere Seite: {target_url}\n\n"
            "Kein Problem, falls es gerade nicht passt.\n\n"
            "Mit freundlichen Grüßen,\nJonathan"
        ),
    },
    "sv": {
        "subject": "Korskampanj — {site_name} + Caracas Research",
        "body": (
            "Hej {greeting},\n\n"
            "Jag hittade er sida om {topic} och noterade att ni behandlar "
            "Venezuela-relaterade ämnen.\n\n"
            "Jag driver Caracas Research ({our_url}), som publicerar "
            "Venezuela-fokuserad forskning om resor, investeringar, sanktioner "
            "och affärsrisker.\n\n"
            "Jag vill gärna föreslå en korskampanj: vi lägger till en länk "
            "till er sajt från vår relevanta sida, och i utbyte gör ni "
            "detsamma med vår.\n\n"
            "Er sida: {source_url}\n"
            "Vår sida: {target_url}\n\n"
            "På så sätt gynnas båda våra läsargrupper av den extra resursen. "
            "Skulle ni vara intresserade?\n\n"
            "Vänliga hälsningar,\nJonathan"
        ),
        "followup_1": (
            "Hej {greeting},\n\n"
            "Jag återkommer angående mitt förslag om korskampanj. Eftersom "
            "era läsare redan söker information om {topic} kan ömsesidig "
            "länkning vara värdefullt för båda sajterna.\n\n"
            "Er sida: {source_url}\n"
            "Vår sida: {target_url}\n\n"
            "Jag diskuterar gärna detaljerna.\n\n"
            "Vänliga hälsningar,\nJonathan"
        ),
        "followup_2": (
            "Hej {greeting},\n\n"
            "Sista meddelandet från mig. Om ett länkutbyte mellan "
            "{site_name} och Caracas Research är relevant för era läsare "
            "sätter jag gärna upp det på vår sida först.\n\n"
            "Vår sida: {target_url}\n\n"
            "Inga problem om det inte passar just nu.\n\n"
            "Vänliga hälsningar,\nJonathan"
        ),
    },
}

OUR_URL = "https://www.caracasresearch.com"


def _site_name(domain: str) -> str:
    name = (domain or "your site").removeprefix("www.")
    parts = name.split(".")
    if parts[0] in ("blog", "www", "news", "info", "web") and len(parts) > 1:
        name = parts[1]
    else:
        name = parts[0]
    return name.replace("-", " ").title() or "your site"


def _topic(prospect: Prospect | dict) -> str:
    if isinstance(prospect, dict):
        return prospect.get("source_page_topic") or prospect.get("link_opportunity") or prospect.get("category") or "Venezuela"
    return (prospect.source_page_topic or prospect.link_opportunity or prospect.category.value).replace("_", " ")


def _get(obj: Prospect | dict, name: str, default: str = "") -> str:
    if isinstance(obj, dict):
        return str(obj.get(name) or default)
    value = getattr(obj, name, default)
    return str(value or default)


def generate_email(prospect: Prospect | dict) -> dict[str, str]:
    """Generate a cross-promotion email sequence in the prospect's language."""
    lang = _get(prospect, "site_language", "en") or "en"
    tpl = TEMPLATES.get(lang, TEMPLATES["en"])

    domain = _get(prospect, "domain", "the site")
    site_name = _site_name(domain)
    source_url = _get(prospect, "source_url", "")
    target_url = _get(prospect, "recommended_target_url", OUR_URL)
    topic = _topic(prospect)
    greeting = f"{site_name} team"

    fmt = {
        "greeting": greeting,
        "site_name": site_name,
        "topic": topic,
        "source_url": source_url,
        "target_url": target_url,
        "our_url": OUR_URL,
    }

    return {
        "subject": tpl["subject"].format(**fmt),
        "body": tpl["body"].format(**fmt),
        "followup_1": tpl["followup_1"].format(**fmt),
        "followup_2": tpl["followup_2"].format(**fmt),
    }


def _plain_to_html(body: str) -> str:
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    return "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in paragraphs)


def upsert_email_sequence(db, prospect: Prospect) -> list[OutreachEmail]:
    """Persist generated initial/follow-up emails for a prospect."""
    generated = generate_email(prospect)
    rows: list[OutreachEmail] = []
    payloads = [
        (1, generated["subject"], generated["body"]),
        (2, f"Re: {generated['subject']}", generated["followup_1"]),
        (3, f"Re: {generated['subject']}", generated["followup_2"]),
    ]
    for sequence_num, subject, body in payloads:
        row = (
            db.query(OutreachEmail)
            .filter(
                OutreachEmail.prospect_id == prospect.id,
                OutreachEmail.sequence_num == sequence_num,
            )
            .one_or_none()
        )
        if row is None:
            row = OutreachEmail(
                prospect_id=prospect.id,
                sequence_num=sequence_num,
                subject=subject,
                body=body,
            )
            db.add(row)
        else:
            row.subject = subject
            row.body = body
        rows.append(row)
    return rows


def send_email(prospect_id: str, sequence_num: int = 1, *, dry_run: bool = False) -> bool:
    """Send one outreach email through Resend and update database status."""
    init_db()
    db = SessionLocal()
    try:
        prospect = db.query(Prospect).filter(Prospect.id == prospect_id).one_or_none()
        if prospect is None or not prospect.contact_email:
            return False
        email = (
            db.query(OutreachEmail)
            .filter(
                OutreachEmail.prospect_id == prospect_id,
                OutreachEmail.sequence_num == sequence_num,
            )
            .one_or_none()
        )
        if email is None:
            upsert_email_sequence(db, prospect)
            db.flush()
            email = (
                db.query(OutreachEmail)
                .filter(
                    OutreachEmail.prospect_id == prospect_id,
                    OutreachEmail.sequence_num == sequence_num,
                )
                .one()
            )

        result = send_provider_email(
            to=prospect.contact_email,
            subject=email.subject,
            html_body=_plain_to_html(email.body),
            provider_name="resend",
            dry_run=dry_run,
            from_override=settings.resend_outreach_from,
            reply_to=settings.resend_outreach_reply_to or None,
        )
        ok = bool(result.get("success"))
        if ok:
            email.sent_at = datetime.utcnow()
            prospect.email_status = EmailStatus.VERIFIED
            prospect.outreach_status = OutreachStatus.SENT
            db.commit()
        else:
            db.rollback()
        return ok
    except Exception as exc:
        db.rollback()
        logger.exception("Outreach send failed for %s: %s", prospect_id, exc)
        return False
    finally:
        db.close()

