# GUI_V1/app/branding.py
"""
User-facing text never names the AI provider, its SDK or its endpoints.
scrub() is applied to everything the user can see: status / progress text, toasts,
message boxes, and the log files of the installed application.
"""
import re

_RULES = [
    (re.compile(r"https?://[\w.-]*googleapis\.com[^\s'\"]*", re.I), "the cloud service"),
    (re.compile(r"[\w.-]*googleapis\.com", re.I), "cloud service"),
    (re.compile(r"\bgoogle[._-]?genai\b", re.I), "cloud_sdk"),
    (re.compile(r"\bgenai\b", re.I), "cloud_sdk"),
    (re.compile(r"\bgemini[-_.]?\d[\w.-]*", re.I), "standard model"),     # model names
    (re.compile(r"\bGemini API\b"), "Cloud Engine"),
    (re.compile(r"\bGEMINI\b"), "CLOUD_ENGINE"),
    (re.compile(r"\bGemini"), "Cloud Engine"),
    (re.compile(r"gemini", re.I), "cloud_engine"),
    (re.compile(r"\bGoogle AI( Studio)?\b", re.I), "the cloud service"),
    (re.compile(r"\bVertex AI\b", re.I), "the cloud service"),
]


def scrub(text):
    if not isinstance(text, str) or not text:
        return text
    for rx, rep in _RULES:
        text = rx.sub(rep, text)
    return text
