from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from urllib.parse import urlparse

import aiohttp

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

from .models import (
    AUDIO_CONTENT_TYPES,
    AUDIO_HOST_ALLOWLIST,
    DOWNLOAD_TIMEOUT,
    MAX_DOWNLOAD_BYTES,
    MAX_TTS_TEXT_LENGTH,
)


class VocuClient:
    """Async client wrapping the Vocu TTS API."""

    def __init__(self) -> None:
        self._http: aiohttp.ClientSession | None = None

    async def ensure_session(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession()
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.closed:
            await self._http.close()
            self._http = None

    def _build_audio_host_allowlist(self, api_base_url: str) -> set[str]:
        parsed = urlparse(api_base_url)
        hosts = set(AUDIO_HOST_ALLOWLIST)
        if parsed.hostname:
            hosts.add(parsed.hostname)
        return hosts

    async def generate_voice(
        self,
        text: str,
        *,
        api_key: str,
        voice_id: str,
        prompt_id: str,
        preset: str,
        api_base_url: str = "https://v1.vocu.ai",
        break_clone: bool = True,
        language: str = "auto",
        speech_rate: float = 1.0,
        vivid: bool = False,
        flash: bool = False,
        emo_switch: list[int] | None = None,
        proxy: str = "",
    ) -> str | None:
        """Call Vocu synchronous TTS API. Returns local file path or None."""
        base_url = api_base_url.rstrip("/")

        if len(text) > MAX_TTS_TEXT_LENGTH:
            logger.warning(
                "VocuTTS: text truncated from %d to %d chars",
                len(text),
                MAX_TTS_TEXT_LENGTH,
            )
            text = text[:MAX_TTS_TEXT_LENGTH]

        payload: dict = {
            "voiceId": voice_id,
            "text": text,
            "promptId": prompt_id,
            "preset": preset,
            "break_clone": break_clone,
            "language": language,
            "speechRate": speech_rate,
            "vivid": vivid,
            "flash": flash,
        }
        if emo_switch:
            payload["emo_switch"] = emo_switch

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            logger.debug(
                "VocuTTS: calling API (voice=%s, prompt=%s, preset=%s, text=%d chars)",
                voice_id,
                prompt_id,
                preset,
                len(text),
            )
            http = await self.ensure_session()
            async with http.post(
                f"{base_url}/api/tts/simple-generate",
                json=payload,
                headers=headers,
                proxy=proxy or None,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error("VocuTTS: API returned %d: %s", resp.status, body)
                    return None
                data = await resp.json()

            audio_url = data.get("data", {}).get("audio")
            if not audio_url:
                logger.error("VocuTTS: no audio URL in response: %s", data)
                return None

            logger.debug("VocuTTS: API returned audio URL, downloading")
            return await self._download_audio(audio_url, api_base_url, proxy)
        except Exception:
            logger.error("VocuTTS: voice generation failed", exc_info=True)
            return None

    async def _download_audio(self, url: str, api_base_url: str, proxy: str = "") -> str | None:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            logger.error(f"VocuTTS: refusing non-HTTP audio URL: {url}")
            return None

        if not parsed.hostname:
            logger.error(f"VocuTTS: audio URL has no hostname: {url}")
            return None

        allowed_hosts = self._build_audio_host_allowlist(api_base_url)
        if parsed.hostname not in allowed_hosts:
            logger.error(
                f"VocuTTS: audio host '{parsed.hostname}' not in allowlist {allowed_hosts}"
            )
            return None

        temp_dir = get_astrbot_temp_path()
        os.makedirs(temp_dir, exist_ok=True)
        path = os.path.join(temp_dir, f"vocutts_{uuid.uuid4()}.mp3")

        try:
            http = await self.ensure_session()
            async with http.get(
                url,
                proxy=proxy or None,
                timeout=DOWNLOAD_TIMEOUT,
                allow_redirects=False,
            ) as resp:
                if resp.status != 200:
                    logger.error(f"VocuTTS: audio download failed: {resp.status}")
                    return None

                content_type = resp.content_type or ""
                if content_type and content_type not in AUDIO_CONTENT_TYPES:
                    logger.error(
                        f"VocuTTS: unexpected Content-Type '{content_type}', expected audio"
                    )
                    return None

                downloaded = 0
                with open(path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        downloaded += len(chunk)
                        if downloaded > MAX_DOWNLOAD_BYTES:
                            logger.error(
                                f"VocuTTS: audio exceeds {MAX_DOWNLOAD_BYTES} bytes limit, aborted"
                            )
                            break
                        f.write(chunk)

                if downloaded > MAX_DOWNLOAD_BYTES:
                    try_remove_file(path)
                    return None

            logger.info("VocuTTS: audio downloaded (%d bytes) -> %s", downloaded, path)
            return path
        except Exception:
            logger.error("VocuTTS: audio download failed", exc_info=True)
            try_remove_file(path)
            return None

    async def enhance_audio(self, input_path: str, slow_factor: float = 0.88) -> str | None:
        """用 ffmpeg 放慢 + 响度归一化，改善 QQ 压缩后的听感。

        成功返回新的音频路径，失败返回 None（调用方应继续用原文件）。
        """
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.warning("VocuTTS: ffmpeg not found, skipping audio enhancement")
            return None

        out_path = os.path.join(
            get_astrbot_temp_path(),
            f"vocutts_{uuid.uuid4()}_enhanced.wav",
        )
        factor = max(0.5, min(float(slow_factor), 1.0))
        filt = f"atempo={factor},loudnorm=I=-16:TP=-1.5:LRA=11"

        try:
            proc = await asyncio.create_subprocess_exec(
                ffmpeg,
                "-y",
                "-i",
                input_path,
                "-af",
                filt,
                "-ar",
                "44100",
                "-ac",
                "1",
                out_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=60)
            if proc.returncode != 0 or not os.path.exists(out_path):
                logger.warning(
                    "VocuTTS: audio enhancement failed (rc=%s)", proc.returncode
                )
                try_remove_file(out_path)
                return None
            size = os.path.getsize(out_path)
            logger.info("VocuTTS: audio enhanced (%d bytes) -> %s", size, out_path)
            return out_path
        except Exception:
            logger.error("VocuTTS: audio enhancement failed", exc_info=True)
            try_remove_file(out_path)
            return None

    async def list_voices(
        self, *, api_key: str, api_base_url: str = "https://v1.vocu.ai", proxy: str = ""
    ) -> tuple[list[dict] | None, str]:
        """Returns (voice_list, error_message). error_message is empty on success."""
        base_url = api_base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {api_key}"}

        try:
            logger.debug("VocuTTS: fetching voice list from %s", base_url)
            http = await self.ensure_session()
            async with http.get(
                f"{base_url}/api/voice",
                headers=headers,
                proxy=proxy or None,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status in (401, 403):
                    logger.warning(
                        "VocuTTS: voice list auth failed (HTTP %d)", resp.status
                    )
                    return None, "API Key 认证失败，请检查 Key 是否正确。"
                if resp.status != 200:
                    logger.error("VocuTTS: voice list API returned %d", resp.status)
                    return None, f"Vocu API 返回错误 (HTTP {resp.status})。"
                data = await resp.json()
                voices = data.get("data", [])
                logger.info("VocuTTS: fetched %d voices", len(voices))
                return voices, ""
        except aiohttp.ClientError:
            logger.warning("VocuTTS: voice list network error", exc_info=True)
            return None, "网络连接失败，请检查网络或 API 地址配置。"
        except Exception:
            logger.error("VocuTTS: list voices failed", exc_info=True)
            return None, "获取声音列表时发生未知错误。"


def try_remove_file(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
