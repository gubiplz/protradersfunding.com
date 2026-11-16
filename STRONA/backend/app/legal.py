"""Legal documents: version, operator details, restricted countries, consent proof.

One place for everything the legal pages and the consent checkboxes share, so
the text a customer accepted, the version stored with that acceptance and the
operator named on the page cannot drift apart.

Operator details come from `LEGAL_*` environment variables and are EMPTY by
default: nothing here invents a company. Templates print each line only when
its value is set.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import countries
from .config import get_settings
from .models import ConsentRecord

settings = get_settings()

#: Version of the published legal documents. Stored with every acceptance, so
#: bump it whenever the wording of Terms / Refund / Risk / Privacy changes.
DOCS_VERSION = "2026-11-16"
DOCS_DATE = "16 November 2026"

#: Text of the three purchase declarations. The portal checkout, the /pay page
#: and the confirmation e-mail all print these exact sentences.
DECLARATIONS = (
    ("terms", "I agree to the Terms of Service and the Refund Policy."),
    ("risk", "I understand that evaluations are simulated, that rewards are not "
             "guaranteed, and I have read the Risk Disclosure."),
    ("immediate", "I request immediate commencement and, where applicable, lose the right "
                  "of withdrawal once the service is fully performed, which is when my MT5 "
                  "credentials are delivered."),
)

RESTRICTED_MESSAGE = ("We are unable to offer our services in your country. "
                      "See the list of restricted countries on our website.")


def restricted_codes() -> frozenset[str]:
    """ISO2 codes we do not serve (RESTRICTED_COUNTRIES, comma-separated)."""
    return frozenset(k.strip().upper() for k in settings.restricted_countries.split(",")
                     if len(k.strip()) == 2)


def is_restricted(iso2: str | None) -> bool:
    return bool(iso2) and iso2.strip().upper() in restricted_codes()


def is_restricted_name(country_name: str | None) -> bool:
    """Same check for a country given by its name (the KYC form sends names)."""
    return is_restricted(countries.iso_from_name(country_name))


def restricted_names() -> list[str]:
    """Names for the public page, alphabetical; unknown codes are shown as codes."""
    po_kodzie = {iso: nazwa for iso, nazwa, *_ in countries.COUNTRIES}
    return sorted(po_kodzie.get(kod, kod) for kod in restricted_codes())


def adult(dob: str | None, today: datetime | None = None) -> bool | None:
    """True/False for a parseable date of birth (YYYY-MM-DD), None when it is not one."""
    try:
        urodzony = datetime.strptime((dob or "").strip()[:10], "%Y-%m-%d")
    except ValueError:
        return None
    dzis = today or datetime.now(timezone.utc)
    wiek = dzis.year - urodzony.year - ((dzis.month, dzis.day) < (urodzony.month, urodzony.day))
    return wiek >= 18


def context() -> dict:
    """What every page template gets as `legal`."""
    s = settings
    return {
        "version": DOCS_VERSION,
        "date": DOCS_DATE,
        "entity_name": s.legal_entity_name,
        "reg_no": s.legal_reg_no,
        "tax_id": s.legal_tax_id,
        "address": s.legal_address,
        "governing_law": s.legal_governing_law,
        "courts": s.legal_courts,
        "adr_body": s.legal_adr_body,
        "supervisory_authority": s.legal_supervisory_authority,
        "privacy_email": s.privacy_email or s.support_email,
        "server_utc": f"UTC{s.server_utc_offset_hours:+d}",
        "restricted": restricted_names(),
        "declarations": [tekst for _, tekst in DECLARATIONS],
    }


def record_consent(session, *, kind: str, request=None, trader_id: int | None = None,
                   order_id: int | None = None, email: str | None = None,
                   version: str | None = None, detail: str = "") -> None:
    """Adds one row of proof that a declaration was made. The caller commits.

    Kept apart from telemetry on purpose: telemetry is best-effort analytics
    that swallows its own errors and is wiped with the customer's events,
    while this is the evidence of what was accepted, when and from where.
    """
    ip = ua = ""
    if request is not None:
        ip = (request.headers.get("x-forwarded-for")
              or (request.client.host if request.client else "")).split(",")[0].strip()
        ua = request.headers.get("user-agent") or ""
    session.add(ConsentRecord(
        trader_id=trader_id, order_id=order_id, email=(email or None), kind=kind[:32],
        doc_version=(version or DOCS_VERSION)[:40], ip=ip[:64], user_agent=ua[:200],
        detail=detail[:300]))
