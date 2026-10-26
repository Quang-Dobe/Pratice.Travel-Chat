MAX_TITLE_LENGTH = 50
ELLIPSIS = "…"


def make_title(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= MAX_TITLE_LENGTH:
        return collapsed
    head = collapsed[:MAX_TITLE_LENGTH]
    if collapsed[MAX_TITLE_LENGTH] != " ":
        boundary = head.rfind(" ")
        if boundary > 0:
            head = head[:boundary]
    return head + ELLIPSIS
