"""Finding named values in a document.

The user says what they want, "invoice no", "bill no", "insured name", and
the app works out the value.

Two things make that work:

* the lines of the page are searched for the words of the field name, so the
  label of the value ("Policy Number :") is found first
* the model is then asked for the value, but only from the few lines around
  that label, because a small model reads a short piece of text far better
  than a whole page

Every answer is checked against the page: a value that does not appear in the
lines it came from is not reported, and the line it was found in is reported
with it, so the answer can be looked at.
"""
from __future__ import annotations

import re

import cactus

STOPWORDS = {"the", "of", "a", "an", "is", "are", "value", "please", "this",
             "that", "and", "to", "in", "for", "be", "give", "extract", "field"}

# words that mean the same thing in a form
ALIASES = {
    "no": ["no", "no.", "number", "num", "#"],
    "number": ["no", "no.", "number", "num", "#"],
    "name": ["name"],
    "date": ["date", "dated", "dt"],
    "amount": ["amount", "amt", "total", "value", "payable", "rs", "inr"],
    "total": ["total", "amount", "amt", "grand total"],
    "insured": ["insured", "policyholder", "customer", "name"],
    "invoice": ["invoice", "inv", "bill"],
    "bill": ["bill", "invoice", "inv"],
    "policy": ["policy", "policy no"],
    "phone": ["phone", "mobile", "contact", "tel"],
    "mobile": ["mobile", "phone", "contact"],
    "address": ["address", "addr"],
    "email": ["email", "e-mail", "mail"],
    "company": ["company", "firm", "name of insured"],
}

LABEL_SPLIT = re.compile(r"[:=\-–—]\s*")
SPLIT_NOISE = re.compile(r"[^a-z0-9]+")


def field_key(name: str) -> str:
    return SPLIT_NOISE.sub("_", name.strip().lower()).strip("_")


def normalise(text: str) -> str:
    return SPLIT_NOISE.sub("", text.lower())


def describe(spec) -> dict:
    """A field is either a sentence or a JSON schema."""
    if isinstance(spec, str):
        return {"type": "string", "description": spec.strip()}
    schema = dict(spec)
    schema.setdefault("type", "string")
    schema.setdefault("description", spec.get("description", ""))
    return schema


def build_schema(fields: dict) -> dict:
    """The tool the model is asked to fill."""
    properties = {}
    for name, spec in fields.items():
        properties[field_key(name)] = describe(spec)
    return {
        "name": "read_fields",
        "description": "Read the requested fields out of the document text.",
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
        },
    }


def keywords(name: str) -> list[tuple[str, float]]:
    """The words of a field name, with the ones that mean the same thing.

    The words of the field itself come first and weigh most, the aliases
    come after and weigh less, so "policy number" is not fooled by the
    "No." of a reference number.
    """
    own = [w for w in SPLIT_NOISE.split(name.lower()) if w and w not in STOPWORDS]
    weighted = []
    for position, word in enumerate(own):
        weighted.append((word, 2.0 if position == 0 else 1.5))
    for word in own:
        for alias in ALIASES.get(word, []):
            if all(alias != known for known, _ in weighted):
                weighted.append((alias, 0.5))
    return weighted


def _has_word(line: str, word: str) -> bool:
    """Is the word in the line as a word, and not inside another one?"""
    return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", line) is not None


def score_line(line: str, words: list[tuple[str, float]]) -> float:
    """How much a line looks like it holds the value of this field."""
    lowered = line.lower()
    score = 0.0
    for word, weight in words:
        if _has_word(lowered, word):
            score += weight
    if score and re.search(r"[:=]", line):
        score += 0.5  # a label and a value on one line
    return score


def candidate_lines(lines: list[str], name: str, limit: int = 2) -> list[int]:
    """The lines that look like they hold this field, best first."""
    words = keywords(name)
    scored = [(score_line(line, words), index) for index, line in enumerate(lines)]
    scored = [(score, index) for score, index in scored if score > 0]
    if not scored:
        return []
    scored.sort(reverse=True)
    picked = []
    for _, index in scored[:limit]:
        for neighbour in (index, index + 1):
            if 0 <= neighbour < len(lines) and neighbour not in picked:
                picked.append(neighbour)
    return picked


def context(lines: list[str], indexes: list[int]) -> str:
    return "\n".join(lines[index] for index in indexes)


LABEL_WORDS = {"no", "number", "name", "date", "total", "amount", "time", "value",
               "code", "id", "address", "policy", "invoice", "bill", "ref",
               "reference", "phone", "mobile", "email", "sum", "due", "from",
               "to", "period", "premium", "insured", "proposer", "holder"}

NEXT_LABEL = re.compile(r"\s+[A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*){0,2}\s*:")


def _cut_at_next_label(value: str) -> str:
    """A value runs until the next label starts.

    Only words that are used as labels end a value, so "Registered Office:"
    stays part of the value but "Policy Number:" does not.
    """
    for match in NEXT_LABEL.finditer(value):
        word = re.sub(r"[^A-Za-z]", "", match.group(0).split(":")[0].split()[-1]).lower()
        if word in LABEL_WORDS:
            return value[: match.start()].strip()
    return value.strip()


def _label_positions(line: str, words) -> list[tuple[int, str]]:
    """Where the words of the field name are, most specific word first."""
    lowered = line.lower()
    found = []
    for entry in words:
        word = entry[0] if isinstance(entry, tuple) else entry
        pattern = rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])"
        for match in re.finditer(pattern, lowered):
            found.append((match.start(), word))
    return found


def value_after_label(line: str, words: list[tuple[str, float]]) -> str | None:
    """The part of a line that follows its label, for the plain cases.

    "Ref. No.: REF-1234 Policy Number : 3005/O/4622/00" gives "REF-1234"
    for "ref no" and "3005/O/4622/00" for "policy number": the value runs
    until the next label. The most specific word of the field name wins, so "policy
    number" does not settle for the "No." of "Ref. No.".
    """
    positions = _label_positions(line, words)
    for position, word in positions:
        tail = line[position + len(word):]
        separator = tail.find(":")
        if 0 <= separator <= 30:
            value = _cut_at_next_label(tail[separator + 1:]).strip(" ,;.")
            if value:
                return value
    if not positions:
        return None
    position, word = positions[0]
    value = line[position + len(word):].strip(" :=")
    # the label may be spelled out in front of the value ("Policy No. 3397")
    for other in positions[1:]:
        if value.lower().startswith(other[1].lower()):
            value = value[len(other[1]):].strip(" :=")
            break
    value = _cut_at_next_label(value).strip(" ,;.")
    return value or None


def _grounded(value, context_text: str, lines: list[str]) -> tuple[bool, int | None]:
    """Is the value actually in the text, and in which line?"""
    if value is None:
        return False, None
    needle = normalise(str(value))
    if not needle:
        return False, None
    for index, line in enumerate(lines):
        if needle in normalise(line):
            return True, index
    return False, None


def _ask_model(schema: dict, key: str, context_text: str):
    """Ask the model for one field, from a short piece of text."""
    tool = {
        "name": "read_field",
        "description": "Read one field out of the document text.",
        "parameters": {
            "type": "object",
            "properties": {key: schema},
            "required": [key],
        },
    }
    try:
        answer = cactus.extract(context_text, tool)
    except cactus.ExtractionValidationError:
        answer = cactus.extract(context_text, tool, strict=False)
    except Exception:
        return None
    if isinstance(answer, dict):
        return answer.get(key)
    return None


def extract_field(name: str, spec, lines: list[str]) -> dict:
    """One field: find the value and where it came from.

    The plain "label : value" form is tried first, because it is exact. When
    the value is not there in that shape, the model is asked, but only from
    the line that looks like it holds the value and the line below it, and
    whatever it answers has to be in those lines.
    """
    key = field_key(name)
    schema = describe(spec)
    words = [word for word, _ in keywords(name)]
    result = {"value": None, "confidence": 0.0, "source": None}
    indexes = candidate_lines(lines, name)

    for index in indexes:
        line = lines[index]
        if line.rstrip().endswith(":"):
            following = index + 1
            if following < len(lines):
                value = _cut_at_next_label(lines[following]).strip(" ,;.")
                grounded, where = _grounded(value, lines[following], [lines[following]])
                if grounded and len(value) <= 40:
                    result["value"] = value
                    result["confidence"] = 0.7
                    result["source"] = following
                    return result
            continue
        candidate = value_after_label(line, words)
        if (candidate and len(candidate) <= 40 and not candidate.startswith("/")
                and " / " not in candidate):
            grounded, where = _grounded(candidate, line, [line])
            if grounded:
                result["value"] = candidate
                result["confidence"] = 0.85
                result["source"] = index
                return result

    for index in indexes:
        pair = [index] + ([index + 1] if index + 1 < len(lines) else [])
        context_text = "\n".join(lines[position] for position in pair)
        value = _ask_model(schema, key, context_text)
        grounded, where = _grounded(value, context_text, [lines[p] for p in pair])
        if grounded:
            result["value"] = value
            result["confidence"] = 0.7
            result["source"] = pair[where]
            return result

    return result


def extract_fields(pages: list[dict], fields: dict) -> dict:
    """Every requested field, over every page of the document.

    The first page that holds a value wins, so a field that appears on the
    summary page is not overwritten by a mention further down.
    """
    results = {}
    for name, spec in fields.items():
        key = field_key(name)
        results[key] = {"field": name, "value": None, "confidence": 0.0,
                        "source": None, "page": None}
        for number, page in enumerate(pages, start=1):
            lines = [line["text"].replace("\r\n", " ").replace("\r", " ").strip()
                     for line in page.get("lines", [])]
            if not lines:
                continue
            found = extract_field(name, spec, lines)
            if found["value"] is not None:
                results[key].update(found)
                results[key]["page"] = number
                results[key]["line"] = lines[found["source"]] if found["source"] is not None else None
                results[key]["bbox"] = (page["lines"][found["source"]]["bbox"]
                                        if found["source"] is not None else None)
                break
    return results
