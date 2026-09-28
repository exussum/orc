from urllib.parse import urlencode

MAX_CHARS = 200


def check_length(text: str, kind: str) -> None:
    if len(text) > MAX_CHARS:
        raise ValueError(f"{kind} text exceeds {MAX_CHARS} characters: {len(text)}")


def tts_url(text: str) -> str:
    return "https://translate.google.com/translate_tts?" + urlencode({"ie": "UTF-8", "q": text, "tl": "en", "client": "tw-ob"})
