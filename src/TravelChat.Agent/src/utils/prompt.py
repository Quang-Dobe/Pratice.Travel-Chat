from html import escape

WARM_TONE = (
    "Sound warm and encouraging, like a friendly travel companion, while staying concise. "
    "Write plain prose with no emoji."
)


def tag(name: str, text: str) -> str:
    body = escape(text, quote=False)
    if "\n" in body:
        return f"<{name}>\n{body}\n</{name}>"
    return f"<{name}>{body}</{name}>"


def section(name: str, *parts: str) -> str:
    body = "\n".join(parts)
    return f"<{name}>\n{body}\n</{name}>"
