
def trim_str_by_len(text: str, width: int, placeholder: str = "...") -> str:
    return (
        text[: (width - len(placeholder))] + placeholder if len(text) >= width else text
    )
