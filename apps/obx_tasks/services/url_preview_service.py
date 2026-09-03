import asyncio
import html
import ipaddress
import json
import re
import socket
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Optional, Tuple, Dict, Any, List

from packages.shared.logging import get_logger

logger = get_logger("obx.tasks.services.url_preview")

# Configure SSL context with certifi
try:
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CONTEXT = ssl.create_default_context()

# SSRF Restricted IP Networks
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),     # Carrier-grade NAT
    ipaddress.ip_network("127.0.0.0/8"),       # Loopback
    ipaddress.ip_network("169.254.0.0/16"),    # Link-local / Cloud metadata (169.254.169.254)
    ipaddress.ip_network("172.16.0.0/12"),     # Private RFC 1918
    ipaddress.ip_network("192.0.0.0/24"),      # IETF protocol assignments
    ipaddress.ip_network("192.0.2.0/24"),      # Documentation
    ipaddress.ip_network("192.168.0.0/16"),    # Private RFC 1918
    ipaddress.ip_network("198.18.0.0/15"),     # Benchmark testing
    ipaddress.ip_network("198.51.100.0/24"),   # Documentation
    ipaddress.ip_network("203.0.113.0/24"),    # Documentation
    ipaddress.ip_network("224.0.0.0/4"),       # Multicast
    ipaddress.ip_network("240.0.0.0/4"),       # Reserved
    ipaddress.ip_network("255.255.255.255/32"),# Broadcast
    # IPv6 blocked ranges
    ipaddress.ip_network("::/128"),            # Unspecified
    ipaddress.ip_network("::1/128"),           # Loopback
    ipaddress.ip_network("fc00::/7"),          # Unique local address
    ipaddress.ip_network("fe80::/10"),         # Link-local
    ipaddress.ip_network("ff00::/8"),          # Multicast
]

# Blocked hostnames
_BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.aws.internal",
    "instance-data",
}


@dataclass
class URLPreviewMetadata:
    platform: str
    author: Optional[str] = None          # Display name (e.g. "BaconCheese")
    handle: Optional[str] = None          # Username handle (e.g. "@BaconCheese21")
    title: Optional[str] = None
    description: Optional[str] = None     # Actual post/page text snippet
    image_url: Optional[str] = None
    avatar_url: Optional[str] = None
    banner_url: Optional[str] = None
    source: str = "unknown"               # x_oembed, fxtwitter, fixupx_og, opengraph, failed
    status: str = "SUCCESS"               # SUCCESS, PARTIAL, FAILED
    fetched_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        author_repr = self.author
        if self.handle:
            if self.author and self.author != self.handle:
                author_repr = f"{self.author}\n   {self.handle}"
            else:
                author_repr = self.handle

        return {
            "preview_platform": self.platform,
            "preview_author": author_repr,
            "preview_title": self.title,
            "preview_description": self.description,
            "preview_image_url": self.image_url,
            "preview_source": self.source,
            "preview_status": self.status,
            "preview_fetched_at": self.fetched_at or datetime.now(timezone.utc),
        }


def is_safe_url(url: str) -> Tuple[bool, Optional[str]]:
    """Strict SSRF validator for target URLs.
    Allows only public http and https URLs.
    Rejects private IPs, loopback, link-local, cloud metadata, and internal hostnames.
    """
    if not url or not isinstance(url, str):
        return False, "Empty or invalid URL"

    try:
        parsed = urllib.parse.urlparse(url.strip())
    except Exception as exc:
        return False, f"Malformed URL: {exc}"

    if parsed.scheme.lower() not in ("http", "https"):
        return False, f"Disallowed scheme: {parsed.scheme} (must be http or https)"

    hostname = parsed.hostname
    if not hostname:
        return False, "Missing hostname"

    hostname_lower = hostname.lower()

    if hostname_lower in _BLOCKED_HOSTNAMES or hostname_lower.endswith(".local") or hostname_lower.endswith(".internal"):
        return False, f"Restricted hostname: {hostname}"

    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)

    try:
        addr_info = socket.getaddrinfo(hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return False, f"DNS resolution failed for {hostname}: {exc}"
    except Exception as exc:
        return False, f"Resolution error for {hostname}: {exc}"

    if not addr_info:
        return False, f"No IP addresses resolved for {hostname}"

    for _, _, _, _, sockaddr in addr_info:
        ip_str = sockaddr[0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            return False, f"Invalid resolved IP: {ip_str}"

        for net in _BLOCKED_NETWORKS:
            if ip_obj in net:
                return False, f"Resolved IP {ip_str} is in restricted range {net}"

    return True, None


class _OGParser(HTMLParser):
    """Lightweight HTML parser to extract OpenGraph & Meta tags."""
    def __init__(self):
        super().__init__()
        self.title: Optional[str] = None
        self.description: Optional[str] = None
        self.image_url: Optional[str] = None
        self.site_name: Optional[str] = None
        self._in_title_tag = False
        self._title_tag_content = []

    def handle_starttag(self, tag, attrs):
        attr_dict = {k.lower(): (v or "").strip() for k, v in attrs}
        if tag.lower() == "title" and not self.title:
            self._in_title_tag = True

        if tag.lower() == "meta":
            prop = attr_dict.get("property", "").lower()
            name = attr_dict.get("name", "").lower()
            content = attr_dict.get("content", "").strip()
            if not content:
                return

            if prop == "og:title" and not self.title:
                self.title = content
            elif prop == "og:description" and not self.description:
                self.description = content
            elif name == "description" and not self.description:
                self.description = content
            elif prop in ("og:image", "og:image:url") and not self.image_url:
                self.image_url = content
            elif prop == "og:site_name" and not self.site_name:
                self.site_name = content
            elif name == "twitter:title" and not self.title:
                self.title = content
            elif name == "twitter:description" and not self.description:
                self.description = content
            elif name in ("twitter:image", "twitter:image:src") and not self.image_url:
                self.image_url = content

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self._in_title_tag = False
            if not self.title and self._title_tag_content:
                self.title = "".join(self._title_tag_content).strip()

    def handle_data(self, data):
        if self._in_title_tag:
            self._title_tag_content.append(data)


def _safe_fetch_bytes(
    url: str,
    max_bytes: int = 512 * 1024,
    timeout: float = 2.5,
    custom_headers: Optional[Dict[str, str]] = None,
) -> Optional[bytes]:
    """Fetches up to max_bytes from url with SSRF protection on initial request and any redirects."""
    current_url = url
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; OBXBot/1.0; +https://obx.gg)",
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    }
    if custom_headers:
        headers.update(custom_headers)

    for _ in range(3):
        safe, reason = is_safe_url(current_url)
        if not safe:
            logger.warning("SSRF blocked URL %s: %s", current_url, reason)
            return None

        req = urllib.request.Request(current_url, headers=headers)
        try:
            class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    return None

            https_handler = urllib.request.HTTPSHandler(context=_SSL_CONTEXT)
            opener = urllib.request.build_opener(NoRedirectHandler, https_handler)
            with opener.open(req, timeout=timeout) as resp:
                data = resp.read(max_bytes)
                return data
        except urllib.error.HTTPError as err:
            if err.code in (301, 302, 303, 307, 308):
                redirect_url = err.headers.get("Location")
                if not redirect_url:
                    return None
                current_url = urllib.parse.urljoin(current_url, redirect_url)
                continue
            return None
        except Exception as exc:
            logger.debug("Fetch error for %s: %s", current_url, exc)
            return None
    return None


class UrlPreviewService:
    """Service for extracting and enriching URL preview metadata safely."""

    @staticmethod
    def detect_platform(url: str) -> str:
        url_lower = (url or "").lower()
        if "x.com/" in url_lower or "twitter.com/" in url_lower:
            return "X"
        if "youtube.com/" in url_lower or "youtu.be/" in url_lower:
            return "YouTube"
        if "discord.gg/" in url_lower or "discord.com/invite/" in url_lower:
            return "Discord"
        return "Web"

    @classmethod
    async def fetch_preview(cls, url: str, task_id: Optional[str] = None) -> URLPreviewMetadata:
        """Asynchronously extract preview metadata with timeouts and SSRF safety.
        Guaranteed never to throw unhandled exceptions.
        """
        if not url:
            return URLPreviewMetadata(
                platform="Web",
                source="fallback",
                status="FAILED",
                fetched_at=datetime.now(timezone.utc),
            )

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(cls._sync_fetch_preview, url, task_id),
                timeout=4.0,
            )
        except (asyncio.TimeoutError, Exception) as exc:
            logger.warning("[URL_PREVIEW_FETCH_TIMEOUT] Fetch timed out for %s (task_id=%s): %s", url, task_id, exc)
            return cls._fallback_metadata(url, task_id=task_id)

    @classmethod
    def _sync_fetch_preview(cls, url: str, task_id: Optional[str] = None) -> URLPreviewMetadata:
        platform = cls.detect_platform(url)

        if platform == "X":
            return cls._extract_x_preview(url, task_id=task_id)
        elif platform == "YouTube":
            return cls._extract_youtube_preview(url, task_id=task_id)
        elif platform == "Discord":
            return cls._extract_discord_preview(url, task_id=task_id)
        else:
            return cls._extract_web_preview(url, task_id=task_id)

    @classmethod
    def _fallback_metadata(cls, url: str, task_id: Optional[str] = None) -> URLPreviewMetadata:
        """Returns clean fallback metadata with status=FAILED and NO fake task instructions."""
        platform = cls.detect_platform(url)
        author = None
        handle = None
        if platform == "X":
            m = re.search(r"(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})", url, re.IGNORECASE)
            if m and m.group(1).lower() not in ("home", "explore", "messages", "notifications", "i"):
                handle = f"@{m.group(1)}"
                author = m.group(1)

        return URLPreviewMetadata(
            platform=platform,
            author=author,
            handle=handle,
            description=None,  # NEVER fake tweet text
            source="failed",
            status="FAILED",
            fetched_at=datetime.now(timezone.utc),
        )

    @classmethod
    def _extract_x_preview(cls, url: str, task_id: Optional[str] = None) -> URLPreviewMetadata:
        """Multi-provider extraction pipeline for public X/Twitter URLs.
        Chain:
        1. Twitter oEmbed API (first-party)
        2. FxTwitter API (JSON)
        3. FixupX OpenGraph (HTML parser with Discordbot UA)
        4. Generic page OpenGraph
        5. Clean fallback (status=FAILED, description=None)
        """
        # Parse handle and status ID
        m = re.search(r"(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,20})(?:/status/(\d+))?", url, re.IGNORECASE)
        user_handle = m.group(1) if m else None
        tweet_id = m.group(2) if m else None
        handle_tag = f"@{user_handle}" if user_handle else None

        # -------------------------------------------------------------
        # PROVIDER 0: Profile URL extraction (for Follow tasks / profile links)
        # -------------------------------------------------------------
        if user_handle and not tweet_id:
            try:
                prof_url = f"https://api.fxtwitter.com/{user_handle}"
                data = _safe_fetch_bytes(prof_url, max_bytes=64 * 1024, timeout=2.5)
                if data:
                    payload = json.loads(data.decode("utf-8", errors="ignore"))
                    if payload.get("code") == 200 and "user" in payload:
                        u_obj = payload["user"]
                        disp_name = u_obj.get("name") or user_handle
                        raw_bio = u_obj.get("description")
                        bio = raw_bio.strip() if raw_bio and str(raw_bio).strip() else None
                        avatar = u_obj.get("avatar_url")
                        banner = u_obj.get("banner_url")
                        img = banner or avatar
                        logger.info(
                            "[URL_PREVIEW_X_PROFILE_SUCCESS] Provider=fxtwitter_profile handle=@%s name='%s' has_bio=%s",
                            user_handle, disp_name, bool(bio),
                        )
                        return URLPreviewMetadata(
                            platform="X",
                            author=disp_name,
                            handle=f"@{user_handle}",
                            title=f"Account @{user_handle}",
                            description=bio,
                            image_url=img,
                            avatar_url=avatar,
                            banner_url=banner,
                            source="fxtwitter_profile",
                            status="SUCCESS" if bio else "PARTIAL",
                            fetched_at=datetime.now(timezone.utc),
                        )
            except Exception as prof_err:
                logger.debug("[URL_PREVIEW_X_PROFILE_FAILED] Profile extraction error: %s", prof_err)

            # Profile fallback if live fetch fails (offline/SSRF/rate-limit)
            return URLPreviewMetadata(
                platform="X",
                author=user_handle,
                handle=f"@{user_handle}",
                title=f"Account @{user_handle}",
                description=None,
                image_url=None,
                avatar_url=None,
                banner_url=None,
                source="profile_fallback",
                status="FAILED",
                fetched_at=datetime.now(timezone.utc),
            )

        # -------------------------------------------------------------
        # PROVIDER 1: FxTwitter JSON API (Primary for Status URLs)
        # -------------------------------------------------------------
        if user_handle and tweet_id:
            try:
                fx_url = f"https://api.fxtwitter.com/{user_handle}/status/{tweet_id}"
                data = _safe_fetch_bytes(fx_url, max_bytes=64 * 1024, timeout=2.5)
                if data:
                    payload = json.loads(data.decode("utf-8", errors="ignore"))
                    if payload.get("code") == 200:
                        tweet = payload.get("tweet", {})
                        author_obj = tweet.get("author", {})
                        display_name = author_obj.get("name") or user_handle
                        screen_name = author_obj.get("screen_name") or user_handle
                        raw_text = tweet.get("text", "").strip()

                        photos = tweet.get("media", {}).get("photos", [])
                        image_url = photos[0].get("url") if photos else None

                        if raw_text:
                            clean_text = re.sub(r"https://t\.co/\w+$", "", raw_text).strip()
                            clean_text = html.unescape(clean_text)
                            if len(clean_text) > 280:
                                clean_text = clean_text[:277] + "..."

                            logger.info(
                                "[URL_PREVIEW_X_PRIMARY_SUCCESS] Provider=fxtwitter (task_id=%s) author='%s' text_len=%d",
                                task_id, display_name, len(clean_text),
                            )
                            return URLPreviewMetadata(
                                platform="X",
                                author=display_name,
                                handle=f"@{screen_name}",
                                title=f"Post by {display_name}",
                                description=clean_text,
                                image_url=image_url,
                                source="fxtwitter",
                                status="SUCCESS",
                                fetched_at=datetime.now(timezone.utc),
                            )
            except Exception as fx_err:
                logger.debug("[URL_PREVIEW_X_PRIMARY_FAILED] Provider=fxtwitter error (task_id=%s): %s", task_id, fx_err)

        # -------------------------------------------------------------
        # PROVIDER 2: First-party Twitter oEmbed API
        # -------------------------------------------------------------
        try:
            oembed_url = f"https://publish.twitter.com/oembed?url={urllib.parse.quote(url)}&omit_script=true"
            data = _safe_fetch_bytes(oembed_url, max_bytes=64 * 1024, timeout=2.5)
            if data:
                payload = json.loads(data.decode("utf-8", errors="ignore"))
                author_name = payload.get("author_name") or user_handle
                raw_html = payload.get("html", "")

                # Extract <p>...</p> from oEmbed blockquote
                clean_text = None
                m_p = re.search(r"<p[^>]*>(.*?)</p>", raw_html, re.DOTALL)
                if m_p:
                    raw_content = m_p.group(1)
                    clean = re.sub(r"<[^>]+>", " ", raw_content)
                    clean = html.unescape(clean)
                    clean = re.sub(r"https?://t\.co/\w+", "", clean)
                    clean = re.sub(r"pic\.twitter\.com/\w+", "", clean)
                    clean_text = " ".join(clean.split()).strip()

                if clean_text:
                    if len(clean_text) > 280:
                        clean_text = clean_text[:277] + "..."

                    # Enrich with media photo from fxtwitter
                    image_url = cls._quick_fetch_x_photo(user_handle, tweet_id)

                    logger.info(
                        "[URL_PREVIEW_X_FALLBACK_SUCCESS] Provider=x_oembed (task_id=%s) author='%s' text_len=%d",
                        task_id, author_name, len(clean_text),
                    )
                    return URLPreviewMetadata(
                        platform="X",
                        author=author_name,
                        handle=handle_tag,
                        title=f"Post by {author_name}",
                        description=clean_text,
                        image_url=image_url,
                        source="x_oembed",
                        status="SUCCESS",
                        fetched_at=datetime.now(timezone.utc),
                    )
                else:
                    logger.info("[URL_PREVIEW_X_FALLBACK_FAILED] Provider=x_oembed missing <p> text (task_id=%s)", task_id)
            else:
                logger.info("[URL_PREVIEW_X_FALLBACK_FAILED] Provider=x_oembed empty response (task_id=%s)", task_id)
        except Exception as oembed_err:
            logger.info("[URL_PREVIEW_X_FALLBACK_FAILED] Provider=x_oembed error (task_id=%s): %s", task_id, oembed_err)

        # -------------------------------------------------------------
        # PROVIDER 3: FixupX OpenGraph HTML parser (Discordbot UA)
        # -------------------------------------------------------------
        if user_handle and tweet_id:
            try:
                fixup_url = f"https://fixupx.com/{user_handle}/status/{tweet_id}"
                data = _safe_fetch_bytes(
                    fixup_url,
                    max_bytes=128 * 1024,
                    timeout=2.5,
                    custom_headers={"User-Agent": "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)"},
                )
                if data:
                    html_str = data.decode("utf-8", errors="ignore")
                    m_desc = re.search(r"<meta property=[\"']og:description[\"'] content=[\"']([^\"']+)[\"']", html_str)
                    m_title = re.search(r"<meta property=[\"']og:title[\"'] content=[\"']([^\"']+)[\"']", html_str)
                    m_img = re.search(r"<meta property=[\"']og:image[\"'] content=[\"']([^\"']+)[\"']", html_str)

                    raw_desc = m_desc.group(1).strip() if m_desc else None
                    if raw_desc and "Sorry, that post doesn" not in raw_desc:
                        clean_desc = html.unescape(raw_desc)
                        if len(clean_desc) > 280:
                            clean_desc = clean_desc[:277] + "..."

                        author_name = user_handle
                        if m_title:
                            t_val = html.unescape(m_title.group(1).strip())
                            m_author = re.match(r"^(.*?)\s*\(@", t_val)
                            if m_author:
                                author_name = m_author.group(1).strip()

                        image_url = m_img.group(1).strip() if m_img else None

                        logger.info(
                            "[URL_PREVIEW_X_FALLBACK_SUCCESS] Provider=fixupx_og (task_id=%s) author='%s' text_len=%d",
                            task_id, author_name, len(clean_desc),
                        )
                        return URLPreviewMetadata(
                            platform="X",
                            author=author_name,
                            handle=handle_tag,
                            title=f"Post by {author_name}",
                            description=clean_desc,
                            image_url=image_url,
                            source="fixupx_og",
                            status="SUCCESS",
                            fetched_at=datetime.now(timezone.utc),
                        )
            except Exception as fix_err:
                logger.debug("[URL_PREVIEW_X_FALLBACK_FAILED] Provider=fixupx_og error (task_id=%s): %s", task_id, fix_err)

        # -------------------------------------------------------------
        # PROVIDER 4: Fallback metadata (Always returns clean object without faking tweet text)
        # -------------------------------------------------------------
        logger.warning(
            "[URL_PREVIEW_X_ALL_PROVIDERS_FAILED] All metadata providers failed for %s (task_id=%s)",
            url, task_id,
        )
        return URLPreviewMetadata(
            platform="X",
            author=user_handle,
            handle=handle_tag,
            title=f"Post by {handle_tag or 'X'}",
            description=None,  # NEVER fake tweet text with generic task instructions!
            image_url=None,
            source="failed",
            status="FAILED",
            fetched_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _quick_fetch_x_photo(user_handle: Optional[str], tweet_id: Optional[str]) -> Optional[str]:
        """Fast helper to check if a tweet has an image attachment."""
        if not user_handle or not tweet_id:
            return None
        try:
            fx_url = f"https://api.fxtwitter.com/{user_handle}/status/{tweet_id}"
            data = _safe_fetch_bytes(fx_url, max_bytes=32 * 1024, timeout=1.5)
            if data:
                payload = json.loads(data.decode("utf-8", errors="ignore"))
                photos = payload.get("tweet", {}).get("media", {}).get("photos", [])
                if photos:
                    return photos[0].get("url")
        except Exception:
            pass
        return None

    @classmethod
    def _extract_youtube_preview(cls, url: str, task_id: Optional[str] = None) -> URLPreviewMetadata:
        try:
            oembed_url = f"https://www.youtube.com/oembed?url={urllib.parse.quote(url)}&format=json"
            data = _safe_fetch_bytes(oembed_url, max_bytes=64 * 1024, timeout=2.0)
            if data:
                payload = json.loads(data.decode("utf-8", errors="ignore"))
                return URLPreviewMetadata(
                    platform="YouTube",
                    author=payload.get("author_name") or "YouTube",
                    title=payload.get("title"),
                    description=payload.get("title"),
                    image_url=payload.get("thumbnail_url"),
                    source="youtube_oembed",
                    status="SUCCESS",
                    fetched_at=datetime.now(timezone.utc),
                )
        except Exception as yt_err:
            logger.debug("YouTube oEmbed error: %s", yt_err)

        return URLPreviewMetadata(
            platform="YouTube",
            author="YouTube",
            title="YouTube Video",
            description=None,
            source="failed",
            status="FAILED",
            fetched_at=datetime.now(timezone.utc),
        )

    @classmethod
    def _extract_discord_preview(cls, url: str, task_id: Optional[str] = None) -> URLPreviewMetadata:
        m = re.search(r"(?:discord\.gg|discord\.com/invite)/([A-Za-z0-9_-]+)", url, re.IGNORECASE)
        code = m.group(1) if m else None
        if code:
            try:
                api_url = f"https://discord.com/api/v10/invites/{code}"
                data = _safe_fetch_bytes(api_url, max_bytes=64 * 1024, timeout=2.0)
                if data:
                    payload = json.loads(data.decode("utf-8", errors="ignore"))
                    guild = payload.get("guild", {})
                    g_name = guild.get("name") or "Discord Server"
                    g_desc = guild.get("description")
                    icon_hash = guild.get("icon")
                    icon_url = f"https://cdn.discordapp.com/icons/{guild.get('id')}/{icon_hash}.png" if icon_hash and guild.get("id") else None
                    return URLPreviewMetadata(
                        platform="Discord",
                        author=g_name,
                        title=f"Join {g_name}",
                        description=g_desc,
                        image_url=icon_url,
                        source="discord_api",
                        status="SUCCESS",
                        fetched_at=datetime.now(timezone.utc),
                    )
            except Exception as disc_err:
                logger.debug("Discord invite preview error: %s", disc_err)

        return URLPreviewMetadata(
            platform="Discord",
            author="Discord",
            title="Discord Server Invite",
            description=None,
            source="failed",
            status="FAILED",
            fetched_at=datetime.now(timezone.utc),
        )

    @classmethod
    def _extract_web_preview(cls, url: str, task_id: Optional[str] = None) -> URLPreviewMetadata:
        data = _safe_fetch_bytes(url, max_bytes=256 * 1024, timeout=2.5)
        if not data:
            return cls._fallback_metadata(url, task_id=task_id)

        try:
            parser = _OGParser()
            parser.feed(data.decode("utf-8", errors="ignore"))

            image_url = parser.image_url
            if image_url and not image_url.startswith("http"):
                image_url = urllib.parse.urljoin(url, image_url)

            if image_url:
                img_safe, _ = is_safe_url(image_url)
                if not img_safe:
                    image_url = None

            site_name = parser.site_name or "Web"
            desc = parser.description or parser.title

            return URLPreviewMetadata(
                platform=site_name,
                author=site_name,
                title=parser.title,
                description=desc,
                image_url=image_url,
                source="opengraph",
                status="SUCCESS" if desc else "PARTIAL",
                fetched_at=datetime.now(timezone.utc),
            )
        except Exception as parse_err:
            logger.debug("HTML parse error for %s: %s", url, parse_err)
            return cls._fallback_metadata(url, task_id=task_id)
