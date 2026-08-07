from __future__ import annotations

import json
import re

from astrbot.api import logger

_OPENERS = frozenset("(（[【")
_CLOSERS = frozenset(")）]】")

_URL_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s，。！？；：、“”‘’（）()]+")
_ASCII_WORD_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_\-]*")


def _clean_for_tts(text: str) -> str:
    """去掉会污染中文 TTS 的英文/标识符/URL，避免英文被乱读。"""
    text = _URL_PATTERN.sub("", text)
    text = _ASCII_WORD_PATTERN.sub("", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text


def process_text(
    text: str,
    mode: str = "strip",
    emotion_keywords: dict | str | None = None,
    remove_english: bool = True,
) -> tuple[str, list[int] | None]:
    """Process text according to bracket_mode.

    Returns (spoken_text, emo_switch_or_none).
    Empty spoken_text signals the caller to skip TTS entirely.
    """
    if mode == "keep":
        logger.debug(
            "VocuTTS: text_processor mode=keep, passthrough (%d chars)", len(text)
        )
        return text, None

    spoken_text, brackets = _strip_brackets(text)
    if remove_english:
        spoken_text = _clean_for_tts(spoken_text)
    spoken_text = re.sub(r"\s{2,}", " ", spoken_text).strip()

    if not spoken_text:
        logger.debug(
            "VocuTTS: text_processor produced empty spoken text (brackets=%d)",
            len(brackets),
        )
        return "", None

    if mode == "emotion_hint" and brackets:
        emo = _extract_emotion(brackets, emotion_keywords)
        logger.debug(
            "VocuTTS: text_processor mode=emotion_hint, spoken=%d chars, brackets=%d, emo=%s",
            len(spoken_text),
            len(brackets),
            emo,
        )
        return spoken_text, emo

    logger.debug(
        "VocuTTS: text_processor mode=%s, spoken=%d chars, stripped %d bracket groups",
        mode,
        len(spoken_text),
        len(brackets),
    )
    return spoken_text, None


def _strip_brackets(text: str) -> tuple[str, list[str]]:
    """Remove all bracketed content with proper nesting support.

    Tracks bracket depth so nested structures like （外层（内层）继续）
    are fully stripped as a single unit.

    Returns (stripped_text, list_of_bracket_contents).
    """
    depth = 0
    result: list[str] = []
    current_bracket: list[str] = []
    brackets: list[str] = []

    for ch in text:
        if ch in _OPENERS:
            depth += 1
            current_bracket.append(ch)
        elif ch in _CLOSERS and depth > 0:
            current_bracket.append(ch)
            depth -= 1
            if depth == 0:
                brackets.append("".join(current_bracket))
                current_bracket = []
        elif depth > 0:
            current_bracket.append(ch)
        else:
            result.append(ch)

    return "".join(result), brackets


def _extract_emotion(
    brackets: list[str],
    raw: dict | str | None,
) -> list[int] | None:
    if not raw:
        return None

    if isinstance(raw, str):
        try:
            keyword_map = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("VocuTTS: emotion_keywords JSON parse failed")
            return None
    else:
        keyword_map = raw

    if not isinstance(keyword_map, dict):
        logger.warning(
            f"VocuTTS: emotion_keywords must be a dict, got {type(keyword_map).__name__}"
        )
        return None

    combined = " ".join(brackets)
    # [Anger, Happiness, Neutral, Sadness, ContextualMatch]
    result = [0, 0, 0, 0, 0]
    matched = False
    for keyword, values in keyword_map.items():
        if not isinstance(keyword, str) or keyword not in combined:
            continue
        if not isinstance(values, list) or len(values) != 5:
            continue
        if not all(isinstance(v, int | float) for v in values):
            logger.warning(
                f"VocuTTS: emotion value for '{keyword}' contains non-numeric elements, skipped"
            )
            continue
        result = [max(r, int(v)) for r, v in zip(result, values)]
        matched = True

    return result if matched else None
