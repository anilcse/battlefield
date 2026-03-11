"""
Rule-based market category classifier.
Runs on title + description text to assign a category tag.
"""
import re

CATEGORIES: dict[str, list[str]] = {
    "crypto": [
        r"\bcrypto\b", r"\bbitcoin\b", r"\bbtc\b", r"\bethereum\b", r"\beth\b",
        r"\bsolana\b", r"\bsol\b", r"\btoken\b", r"\bdefi\b", r"\bnft\b",
        r"\bstablecoin\b", r"\bblockchain\b", r"\bcoinbase\b", r"\bbinance\b",
        r"\bcrypto\s*price\b", r"\bmarket\s*cap\b", r"\baltcoin\b",
        r"\b\$150k\b", r"\b\$100k\b", r"\b\$1m\b", r"\bpump\.fun\b", r"\bairdrop\b",
    ],
    "crypto_short_term": [
        r"\b\d+\s*min(ute)?\b", r"\b5m\b", r"\b15m\b", r"\b1h\b", r"\b4h\b",
        r"\b24\s*hour\b", r"\btoday\b.*price", r"\bprice.*today\b",
        r"\bby\s*(midnight|noon|eod)\b",
    ],
    "sports": [
        r"\bnfl\b", r"\bnba\b", r"\bmlb\b", r"\bnhl\b", r"\bsoccer\b",
        r"\bfootball\b", r"\bbasketball\b", r"\btennis\b", r"\bgolf\b",
        r"\bf1\b", r"\bformula\s*1\b", r"\bufc\b", r"\bmma\b", r"\bboxing\b",
        r"\bolympic\b", r"\bworld\s*cup\b", r"\bsuper\s*bowl\b", r"\bplayoff\b",
        r"\bchampion(ship)?\b", r"\bmatch\b", r"\bgame\s*\d\b",
        r"\bpistons?\b", r"\blakers?\b", r"\bwarriors?\b", r"\bceltic\b", r"\bheat\b",
        r"\bla\s*liga\b", r"\bpremier\s*league\b", r"\bchampions\s*league\b",
        r"\bfifa\b", r"\brelegat", r"\barsenal\b", r"\bchelsea\b", r"\bliverpool\b",
        r"\bwin\s*the\s*\d{4}\b", r"\bwin\s*the\s*nba\b", r"\bwin\s*the\s*super\b",
    ],
    "politics": [
        r"\belection\b", r"\bpresident\b", r"\bsenate\b", r"\bcongress\b",
        r"\bvote\b", r"\bpoll\b", r"\bdemocrat\b", r"\brepublican\b",
        r"\btrump\b", r"\bbiden\b", r"\bgop\b", r"\bparliament\b",
        r"\bprime\s*minister\b", r"\bpolicy\b", r"\blegislat\b",
    ],
    "celebrities": [
        r"\belon\b", r"\bmusk\b", r"\btweet\b", r"\bx\.com\b",
        r"\bcelebrit\b", r"\bkanye\b", r"\btaylor\s*swift\b",
        r"\binstagram\b", r"\btiktok\b", r"\binfluencer\b",
    ],
    "weather": [
        r"\bweather\b", r"\btemperature\b", r"\bhurricane\b", r"\btornado\b",
        r"\bflood\b", r"\bsnow\b", r"\brain\b", r"\bheat\s*wave\b",
        r"\bwildfire\b", r"\bclimate\b", r"\bforecast\b.*\b(rain|snow|temp)\b",
        r"\bdegrees?\b", r"\bcelsius\b", r"\bfahrenheit\b", r"\bstorm\b",
    ],
    "science_tech": [
        r"\bai\b", r"\bartificial\s*intelligence\b", r"\bmachine\s*learning\b",
        r"\bspacex\b", r"\bnasa\b", r"\brocket\b", r"\blaunch\b",
        r"\bfda\b", r"\bdrug\s*approval\b", r"\bvaccine\b", r"\bclinical\s*trial\b",
    ],
    "economics": [
        r"\bfed\b", r"\binterest\s*rate\b", r"\binflation\b", r"\bgdp\b",
        r"\bunemployment\b", r"\brecession\b", r"\bstock\s*market\b",
        r"\bs&p\s*500\b", r"\bnasdaq\b", r"\bearnings\b", r"\btariff\b",
    ],
    "entertainment": [
        r"\boscar\b", r"\bemmy\b", r"\bgrammy\b", r"\bbox\s*office\b",
        r"\bmovie\b", r"\bfilm\b", r"\btv\s*show\b", r"\bnetflix\b",
        r"\bstreaming\b", r"\balbum\b",
    ],
}

DURATION_SHORT_PATTERNS = [
    r"\b\d+\s*min(ute)?s?\b", r"\b5m\b", r"\b15m\b", r"\b1h\b", r"\b4h\b",
    r"\btoday\b", r"\bby\s*(midnight|noon|eod)\b", r"\bnext\s*hour\b",
]


def classify_market(title: str, description: str = "") -> str:
    text = f"{title} {description}".lower()
    scores: dict[str, int] = {}
    for category, patterns in CATEGORIES.items():
        for pattern in patterns:
            if re.search(pattern, text):
                scores[category] = scores.get(category, 0) + 1

    if not scores:
        return "other"

    best = max(scores, key=lambda k: scores[k])

    if best == "crypto":
        for pattern in DURATION_SHORT_PATTERNS:
            if re.search(pattern, text):
                return "crypto_short_term"

    return best


def market_duration_tag(title: str, description: str = "", end_date: str = "") -> str:
    text = f"{title} {description} {end_date}".lower()
    for pattern in DURATION_SHORT_PATTERNS:
        if re.search(pattern, text):
            return "short_term"
    long_patterns = [r"\b(year|annual|202[5-9]|203\d)\b", r"\bby\s*(end\s*of\s*)?20\d\d\b"]
    for pattern in long_patterns:
        if re.search(pattern, text):
            return "long_term"
    return "medium_term"
