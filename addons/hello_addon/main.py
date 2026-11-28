"""Reference add-on entrypoint. The collector never imports this file; a runtime does."""


def describe() -> dict:
    return {"id": "hello_addon", "message": "مرحباً من إضافة مرجعية"}
