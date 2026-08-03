"""Kraje: numer kierunkowy i dopuszczalna długość numeru telefonu.

Jedno źródło prawdy dla serwera i dla przeglądarki — ta sama tablica leci do
strony jako JSON (`pf-countries`), więc okno zakupu i walidacja na serwerze
liczą dokładnie to samo.

Tablica jest GENEROWANA, nie pisana ręcznie: długości numerów pochodzą z
metadanych libphonenumber, bo zgadywanie ich z pamięci kończy się odrzucaniem
prawdziwych numerów, a to gorsze niż przepuszczenie dziwnego. Regeneracja:
`python scripts/gen_countries.py`.

Kolumny: (ISO2, nazwa, kierunkowy, min cyfr numeru krajowego, max cyfr,
czy kraj jest GLOWNY dla swojego kierunkowego).

Ostatnia kolumna rozstrzyga kierunkowe dzielone przez kilka krajow: +44 nosi
Wielka Brytania, Guernsey, Jersey i Wyspa Man, a +1 ponad dwadziescia krajow.
Bez tego numer bez zapisanego kraju trafial pod pierwsza alfabetycznie flage —
o innych dopuszczalnych dlugosciach numeru.
"""
from __future__ import annotations

COUNTRIES: list[tuple[str, str, str, int, int, bool]] = [
    ("AF", "Afghanistan", "93", 9, 9, False),
    ("AL", "Albania", "355", 8, 9, False),
    ("DZ", "Algeria", "213", 8, 9, False),
    ("AS", "American Samoa", "1", 10, 10, False),
    ("AD", "Andorra", "376", 6, 9, False),
    ("AO", "Angola", "244", 9, 9, False),
    ("AI", "Anguilla", "1", 10, 10, False),
    ("AG", "Antigua and Barbuda", "1", 10, 10, False),
    ("AR", "Argentina", "54", 10, 11, False),
    ("AM", "Armenia", "374", 8, 8, False),
    ("AW", "Aruba", "297", 7, 7, False),
    ("AU", "Australia", "61", 9, 9, True),
    ("AT", "Austria", "43", 4, 13, False),
    ("AZ", "Azerbaijan", "994", 9, 9, False),
    ("BS", "Bahamas", "1", 10, 10, False),
    ("BH", "Bahrain", "973", 8, 8, False),
    ("BD", "Bangladesh", "880", 6, 10, False),
    ("BB", "Barbados", "1", 10, 10, False),
    ("BY", "Belarus", "375", 9, 9, False),
    ("BE", "Belgium", "32", 8, 9, False),
    ("BZ", "Belize", "501", 7, 7, False),
    ("BJ", "Benin", "229", 10, 10, False),
    ("BM", "Bermuda", "1", 10, 10, False),
    ("BT", "Bhutan", "975", 7, 8, False),
    ("BO", "Bolivia", "591", 8, 8, False),
    ("BA", "Bosnia and Herzegovina", "387", 8, 9, False),
    ("BW", "Botswana", "267", 7, 8, False),
    ("BR", "Brazil", "55", 10, 11, False),
    ("IO", "British Indian Ocean Territory", "246", 7, 7, False),
    ("VG", "British Virgin Islands", "1", 10, 10, False),
    ("BN", "Brunei", "673", 7, 7, False),
    ("BG", "Bulgaria", "359", 6, 9, False),
    ("BF", "Burkina Faso", "226", 8, 8, False),
    ("BI", "Burundi", "257", 8, 8, False),
    ("KH", "Cambodia", "855", 8, 9, False),
    ("CM", "Cameroon", "237", 9, 9, False),
    ("CA", "Canada", "1", 10, 10, False),
    ("CV", "Cape Verde", "238", 7, 7, False),
    ("BQ", "Caribbean Netherlands", "599", 7, 7, False),
    ("KY", "Cayman Islands", "1", 10, 10, False),
    ("CF", "Central African Republic", "236", 8, 8, False),
    ("TD", "Chad", "235", 8, 8, False),
    ("CL", "Chile", "56", 9, 9, False),
    ("CN", "China", "86", 7, 11, False),
    ("CX", "Christmas Island", "61", 9, 9, False),
    ("CC", "Cocos Islands", "61", 9, 9, False),
    ("CO", "Colombia", "57", 8, 10, False),
    ("KM", "Comoros", "269", 7, 7, False),
    ("CG", "Congo", "242", 9, 9, False),
    ("CK", "Cook Islands", "682", 5, 5, False),
    ("CR", "Costa Rica", "506", 8, 8, False),
    ("HR", "Croatia", "385", 8, 9, False),
    ("CU", "Cuba", "53", 6, 10, False),
    ("CW", "Curaçao", "599", 7, 8, True),
    ("CY", "Cyprus", "357", 8, 8, False),
    ("CZ", "Czechia", "420", 9, 9, False),
    ("CI", "Côte d'Ivoire", "225", 10, 10, False),
    ("CD", "DR Congo", "243", 7, 10, False),
    ("DK", "Denmark", "45", 8, 8, False),
    ("DJ", "Djibouti", "253", 8, 8, False),
    ("DM", "Dominica", "1", 10, 10, False),
    ("DO", "Dominican Republic", "1", 10, 10, False),
    ("EC", "Ecuador", "593", 8, 9, False),
    ("EG", "Egypt", "20", 8, 10, False),
    ("SV", "El Salvador", "503", 8, 8, False),
    ("GQ", "Equatorial Guinea", "240", 9, 9, False),
    ("ER", "Eritrea", "291", 7, 7, False),
    ("EE", "Estonia", "372", 7, 8, False),
    ("SZ", "Eswatini", "268", 8, 8, False),
    ("ET", "Ethiopia", "251", 9, 9, False),
    ("FK", "Falkland Islands", "500", 5, 5, False),
    ("FO", "Faroe Islands", "298", 6, 6, False),
    ("FJ", "Fiji", "679", 7, 7, False),
    ("FI", "Finland", "358", 5, 10, True),
    ("FR", "France", "33", 9, 9, False),
    ("GF", "French Guiana", "594", 9, 9, False),
    ("PF", "French Polynesia", "689", 8, 8, False),
    ("GA", "Gabon", "241", 7, 8, False),
    ("GM", "Gambia", "220", 7, 7, False),
    ("GE", "Georgia", "995", 9, 9, False),
    ("DE", "Germany", "49", 5, 15, False),
    ("GH", "Ghana", "233", 9, 9, False),
    ("GI", "Gibraltar", "350", 8, 8, False),
    ("GR", "Greece", "30", 10, 10, False),
    ("GL", "Greenland", "299", 6, 6, False),
    ("GD", "Grenada", "1", 10, 10, False),
    ("GP", "Guadeloupe", "590", 9, 9, True),
    ("GU", "Guam", "1", 10, 10, False),
    ("GT", "Guatemala", "502", 8, 8, False),
    ("GG", "Guernsey", "44", 10, 10, False),
    ("GN", "Guinea", "224", 8, 9, False),
    ("GW", "Guinea-Bissau", "245", 9, 9, False),
    ("GY", "Guyana", "592", 7, 7, False),
    ("HT", "Haiti", "509", 8, 8, False),
    ("HN", "Honduras", "504", 8, 8, False),
    ("HK", "Hong Kong", "852", 8, 8, False),
    ("HU", "Hungary", "36", 8, 9, False),
    ("IS", "Iceland", "354", 7, 9, False),
    ("IN", "India", "91", 10, 10, False),
    ("ID", "Indonesia", "62", 7, 12, False),
    ("IR", "Iran", "98", 6, 10, False),
    ("IQ", "Iraq", "964", 8, 10, False),
    ("IE", "Ireland", "353", 7, 10, False),
    ("IM", "Isle of Man", "44", 10, 10, False),
    ("IL", "Israel", "972", 8, 12, False),
    ("IT", "Italy", "39", 6, 12, True),
    ("JM", "Jamaica", "1", 10, 10, False),
    ("JP", "Japan", "81", 9, 10, False),
    ("JE", "Jersey", "44", 10, 10, False),
    ("JO", "Jordan", "962", 8, 9, False),
    ("KZ", "Kazakhstan", "7", 10, 10, False),
    ("KE", "Kenya", "254", 7, 9, False),
    ("KI", "Kiribati", "686", 5, 8, False),
    ("KW", "Kuwait", "965", 8, 8, False),
    ("KG", "Kyrgyzstan", "996", 9, 9, False),
    ("LA", "Laos", "856", 8, 10, False),
    ("LV", "Latvia", "371", 8, 8, False),
    ("LB", "Lebanon", "961", 7, 8, False),
    ("LS", "Lesotho", "266", 8, 8, False),
    ("LR", "Liberia", "231", 7, 9, False),
    ("LY", "Libya", "218", 9, 9, False),
    ("LI", "Liechtenstein", "423", 7, 9, False),
    ("LT", "Lithuania", "370", 8, 8, False),
    ("LU", "Luxembourg", "352", 4, 11, False),
    ("MO", "Macau", "853", 8, 8, False),
    ("MG", "Madagascar", "261", 9, 9, False),
    ("MW", "Malawi", "265", 7, 9, False),
    ("MY", "Malaysia", "60", 8, 10, False),
    ("MV", "Maldives", "960", 7, 7, False),
    ("ML", "Mali", "223", 8, 8, False),
    ("MT", "Malta", "356", 8, 8, False),
    ("MH", "Marshall Islands", "692", 7, 7, False),
    ("MQ", "Martinique", "596", 9, 9, False),
    ("MR", "Mauritania", "222", 8, 8, False),
    ("MU", "Mauritius", "230", 7, 8, False),
    ("YT", "Mayotte", "262", 9, 9, False),
    ("MX", "Mexico", "52", 10, 10, False),
    ("FM", "Micronesia", "691", 7, 7, False),
    ("MD", "Moldova", "373", 8, 8, False),
    ("MC", "Monaco", "377", 8, 9, False),
    ("MN", "Mongolia", "976", 8, 10, False),
    ("ME", "Montenegro", "382", 8, 8, False),
    ("MS", "Montserrat", "1", 10, 10, False),
    ("MA", "Morocco", "212", 9, 9, True),
    ("MZ", "Mozambique", "258", 8, 9, False),
    ("MM", "Myanmar", "95", 6, 10, False),
    ("NA", "Namibia", "264", 8, 9, False),
    ("NR", "Nauru", "674", 7, 7, False),
    ("NP", "Nepal", "977", 8, 10, False),
    ("NL", "Netherlands", "31", 9, 11, False),
    ("NC", "New Caledonia", "687", 6, 6, False),
    ("NZ", "New Zealand", "64", 8, 10, False),
    ("NI", "Nicaragua", "505", 8, 8, False),
    ("NE", "Niger", "227", 8, 8, False),
    ("NG", "Nigeria", "234", 10, 10, False),
    ("NU", "Niue", "683", 4, 7, False),
    ("NF", "Norfolk Island", "672", 6, 6, False),
    ("KP", "North Korea", "850", 8, 10, False),
    ("MK", "North Macedonia", "389", 8, 8, False),
    ("MP", "Northern Mariana Islands", "1", 10, 10, False),
    ("NO", "Norway", "47", 8, 8, True),
    ("OM", "Oman", "968", 8, 8, False),
    ("PK", "Pakistan", "92", 9, 10, False),
    ("PW", "Palau", "680", 7, 7, False),
    ("PS", "Palestine", "970", 8, 9, False),
    ("PA", "Panama", "507", 7, 8, False),
    ("PG", "Papua New Guinea", "675", 7, 8, False),
    ("PY", "Paraguay", "595", 7, 9, False),
    ("PE", "Peru", "51", 8, 9, False),
    ("PH", "Philippines", "63", 6, 10, False),
    ("PL", "Poland", "48", 7, 9, False),
    ("PT", "Portugal", "351", 9, 9, False),
    ("PR", "Puerto Rico", "1", 10, 10, False),
    ("QA", "Qatar", "974", 8, 8, False),
    ("RO", "Romania", "40", 6, 9, False),
    ("RU", "Russia", "7", 10, 10, True),
    ("RW", "Rwanda", "250", 8, 9, False),
    ("RE", "Réunion", "262", 9, 9, True),
    ("BL", "Saint Barthélemy", "590", 9, 9, False),
    ("SH", "Saint Helena", "290", 4, 5, True),
    ("KN", "Saint Kitts and Nevis", "1", 10, 10, False),
    ("LC", "Saint Lucia", "1", 10, 10, False),
    ("MF", "Saint Martin", "590", 9, 9, False),
    ("PM", "Saint Pierre & Miquelon", "508", 6, 9, False),
    ("VC", "Saint Vincent and the Grenadines", "1", 10, 10, False),
    ("WS", "Samoa", "685", 5, 10, False),
    ("SM", "San Marino", "378", 8, 10, False),
    ("ST", "Sao Tome and Principe", "239", 7, 7, False),
    ("SA", "Saudi Arabia", "966", 9, 9, False),
    ("SN", "Senegal", "221", 9, 9, False),
    ("RS", "Serbia", "381", 7, 12, False),
    ("SC", "Seychelles", "248", 7, 7, False),
    ("SL", "Sierra Leone", "232", 8, 8, False),
    ("SG", "Singapore", "65", 8, 8, False),
    ("SX", "Sint Maarten", "1", 10, 10, False),
    ("SK", "Slovakia", "421", 6, 9, False),
    ("SI", "Slovenia", "386", 8, 8, False),
    ("SB", "Solomon Islands", "677", 5, 7, False),
    ("SO", "Somalia", "252", 6, 9, False),
    ("ZA", "South Africa", "27", 5, 9, False),
    ("KR", "South Korea", "82", 5, 10, False),
    ("SS", "South Sudan", "211", 9, 9, False),
    ("ES", "Spain", "34", 9, 9, False),
    ("LK", "Sri Lanka", "94", 9, 9, False),
    ("SD", "Sudan", "249", 9, 9, False),
    ("SR", "Suriname", "597", 6, 7, False),
    ("SJ", "Svalbard and Jan Mayen", "47", 8, 8, False),
    ("SE", "Sweden", "46", 7, 9, False),
    ("CH", "Switzerland", "41", 9, 9, False),
    ("SY", "Syria", "963", 8, 9, False),
    ("TW", "Taiwan", "886", 8, 9, False),
    ("TJ", "Tajikistan", "992", 9, 9, False),
    ("TZ", "Tanzania", "255", 9, 9, False),
    ("TH", "Thailand", "66", 8, 9, False),
    ("TL", "Timor-Leste", "670", 7, 8, False),
    ("TG", "Togo", "228", 8, 8, False),
    ("TK", "Tokelau", "690", 4, 7, False),
    ("TO", "Tonga", "676", 5, 7, False),
    ("TT", "Trinidad and Tobago", "1", 10, 10, False),
    ("TN", "Tunisia", "216", 8, 8, False),
    ("TM", "Turkmenistan", "993", 8, 8, False),
    ("TC", "Turks and Caicos Islands", "1", 10, 10, False),
    ("TV", "Tuvalu", "688", 5, 7, False),
    ("TR", "Türkiye", "90", 10, 10, False),
    ("VI", "U.S. Virgin Islands", "1", 10, 10, False),
    ("UG", "Uganda", "256", 9, 9, False),
    ("UA", "Ukraine", "380", 9, 9, False),
    ("AE", "United Arab Emirates", "971", 8, 9, False),
    ("GB", "United Kingdom", "44", 9, 10, True),
    ("US", "United States", "1", 10, 10, True),
    ("UY", "Uruguay", "598", 8, 8, False),
    ("UZ", "Uzbekistan", "998", 9, 9, False),
    ("VU", "Vanuatu", "678", 5, 7, False),
    ("VA", "Vatican City", "39", 6, 11, False),
    ("VE", "Venezuela", "58", 10, 10, False),
    ("VN", "Vietnam", "84", 9, 10, False),
    ("WF", "Wallis & Futuna", "681", 6, 6, False),
    ("EH", "Western Sahara", "212", 9, 9, False),
    ("YE", "Yemen", "967", 7, 9, False),
    ("ZM", "Zambia", "260", 9, 9, False),
    ("ZW", "Zimbabwe", "263", 5, 10, False),
    ("AX", "Åland Islands", "358", 6, 10, False),
]

BY_ISO: dict[str, tuple[str, str, str, int, int, bool]] = {k[0]: k for k in COUNTRIES}

# Kraj glowny dla danego kierunkowego (US dla +1, GB dla +44).
MAIN_BY_DIAL: dict[str, str] = {k[2]: k[0] for k in COUNTRIES if k[5]}

# Twardy limit E.164: kierunkowy razem z numerem krajowym nie przekracza 15 cyfr.
E164_MAX = 15


def payload() -> list[dict]:
    """Lista dla przeglądarki. Nazwy kluczy krótkie, bo idzie w każdym HTML-u."""
    return [{"i": i, "n": n, "d": d, "mn": mn, "mx": mx, "m": glowny}
            for i, n, d, mn, mx, glowny in COUNTRIES]


def check_phone(iso2: str, national: str) -> str:
    """Składa numer w E.164 albo mówi, co jest z nim nie tak.

    Rzuca ValueError z komunikatem gotowym do pokazania klientowi.

    Zasada: ZERO zgadywania. Numer zapisany międzynarodowo (`+` albo `00`) musi
    zaczynać się kierunkowym wybranego kraju i reszta jest numerem krajowym;
    bez tego przedrostka cała treść JEST numerem krajowym. Wcześniejsza wersja
    obcinała wiodące cyfry, gdy „wyglądały" na kierunkowy, i przez to `+4851206`
    (za krótki numer polski) przechodziło jako poprawny numer 7-cyfrowy.
    """
    kraj = BY_ISO.get((iso2 or "").strip().upper())
    if kraj is None:
        raise ValueError("Pick your country from the list.")
    _, nazwa, kierunkowy, mn, mx, _glowny = kraj
    tekst = (national or "").strip()
    cyfry = "".join(c for c in tekst if c.isdigit())
    if not cyfry:
        raise ValueError("Enter your phone number.")

    miedzynarodowy = tekst.startswith("+") or cyfry.startswith("00")
    if cyfry.startswith("00"):
        cyfry = cyfry[2:]
    if miedzynarodowy:
        if not cyfry.startswith(kierunkowy):
            obcy = next((k for k in sorted(BY_ISO.values(), key=lambda k: -len(k[2]))
                         if cyfry.startswith(k[2])), None)   # noqa: E501
            raise ValueError(
                f"That number starts with +{obcy[2]}, not +{kierunkowy} ({nazwa}). "
                "Pick the matching country." if obcy else
                f"That does not look like a {nazwa} number (+{kierunkowy}).")
        cyfry = cyfry[len(kierunkowy):]
        if not cyfry:
            raise ValueError("Enter your phone number.")

    def pasuje(n: str) -> bool:
        return mn <= len(n) <= mx

    # Zero wiodące zdejmujemy WYŁĄCZNIE jako naprawę: gdy z nim numer nie pasuje,
    # a bez niego pasuje. Są kraje (np. Włochy), gdzie zero NALEŻY do numeru
    # krajowego i bezwarunkowe obcinanie psułoby poprawne numery.
    if not pasuje(cyfry) and cyfry.startswith("0") and pasuje(cyfry[1:]):
        cyfry = cyfry[1:]

    if not pasuje(cyfry):
        ile = str(mn) if mn == mx else f"{mn}–{mx}"
        raise ValueError(
            f"A phone number in {nazwa} has {ile} digits after +{kierunkowy} "
            f"— you entered {len(cyfry)}.")
    if len(kierunkowy) + len(cyfry) > E164_MAX:
        raise ValueError("That phone number is too long.")
    return f"+{kierunkowy}{cyfry}"
