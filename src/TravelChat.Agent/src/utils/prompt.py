from html import escape


def tag(name: str, text: str) -> str:
    body = escape(text, quote=False)
    if "\n" in body:
        return f"<{name}>\n{body}\n</{name}>"
    return f"<{name}>{body}</{name}>"


def section(name: str, *parts: str) -> str:
    body = "\n".join(parts)
    return f"<{name}>\n{body}\n</{name}>"
