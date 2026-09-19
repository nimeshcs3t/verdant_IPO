"""Promoter / management interviews and IPO roadshows from YouTube.

Search:      YouTube Data API v3 if `youtube_api_key` is set (reliable, 100 units/search),
             otherwise yt-dlp's `ytsearch` (no key needed).
Transcripts: youtube-transcript-api (auto-captions work for most Hindi/English business TV).
Analysis:    keyword scan for guidance statements and red-flag topics; optional LLM summary
             if `anthropic_api_key` is set.
"""
from __future__ import annotations

import re
from collections import Counter

from ..utils import Http, log

QUERIES = {
    "interview": ['"{name}" IPO interview', '"{name}" MD CEO interview'],
    "roadshow": ['"{name}" IPO roadshow', '"{name}" IPO management presentation'],
    "review": ['"{name}" IPO review'],
}

TRUSTED_CHANNELS = {"CNBC-TV18", "CNBC Awaaz", "ET NOW", "Zee Business", "NDTV Profit",
                    "Moneycontrol", "Business Standard", "ET Now Swadesh", "BQ Prime",
                    "The Economic Times", "Mint", "Chittorgarh.com", "Informed Investor"}

RED_FLAG_TERMS = [
    "related party", "pledge", "working capital", "receivable", "debtor days", "one-time",
    "one time", "subsidy", "customer concentration", "top customer", "litigation", "notice",
    "raid", "auditor", "write-off", "write off", "government order", "single client",
    "margin pressure", "competition", "offer for sale", "exit",
]
GUIDANCE_RE = re.compile(
    r"[^.]{0,120}\b(\d{1,3}(?:\.\d)?\s*(?:%|percent|per cent)|₹?\s*\d[\d,]*\s*(?:crore|cr))"
    r"[^.]{0,80}\b(growth|cagr|margin|revenue|order book|capacity|topline|ebitda|guidance|fy\s?\d{2})[^.]{0,80}",
    re.I)


def _kind(title: str) -> str:
    t = title.lower()
    if "roadshow" in t or "presentation" in t:
        return "roadshow"
    if any(w in t for w in ("interview", "ceo", "md ", "founder", "promoter", "chairman", "in conversation")):
        return "interview"
    if "review" in t or "apply or avoid" in t or "gmp" in t:
        return "review"
    return "other"


def _relevant(title: str, name: str) -> bool:
    words = [w for w in re.findall(r"[A-Za-z]+", name) if w.lower() not in
             {"ltd", "limited", "india", "the", "and", "of", "industries", "technologies"}]
    key = words[0].lower() if words else name.lower()
    return key in title.lower()


class YouTube:
    def __init__(self, http: Http, api_key: str | None = None, anthropic_key: str | None = None,
                 llm_model: str = "claude-sonnet-5", max_per_query: int = 5):
        self.http, self.api_key, self.anthropic_key = http, api_key, anthropic_key
        self.llm_model, self.max = llm_model, max_per_query

    # ---- search ---------------------------------------------------------
    def _search_api(self, q: str) -> list[dict]:
        r = self.http.get("https://www.googleapis.com/youtube/v3/search", use_cache=False, params={
            "part": "snippet", "q": q, "type": "video", "maxResults": self.max,
            "regionCode": "IN", "order": "relevance", "key": self.api_key})
        if not r:
            return []
        return [{"video_id": i["id"]["videoId"], "title": i["snippet"]["title"],
                 "channel": i["snippet"]["channelTitle"], "published": i["snippet"]["publishedAt"][:10]}
                for i in r.json().get("items", [])]

    def _search_ytdlp(self, q: str) -> list[dict]:
        try:
            import yt_dlp
        except ImportError:
            log.info("yt-dlp not installed and no YouTube API key; skipping video search")
            return []
        with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": True, "skip_download": True}) as ydl:
            res = ydl.extract_info(f"ytsearch{self.max}:{q}", download=False) or {}
        out = []
        for e in res.get("entries", []) or []:
            ud = e.get("upload_date")
            out.append({"video_id": e.get("id"), "title": e.get("title", ""),
                        "channel": e.get("channel") or e.get("uploader") or "",
                        "published": f"{ud[:4]}-{ud[4:6]}-{ud[6:]}" if ud else None})
        return out

    def find(self, slug: str, name: str) -> list[dict]:
        found: dict[str, dict] = {}
        clean = re.sub(r"\b(Ltd|Limited)\b\.?", "", name).strip()
        for kind, qs in QUERIES.items():
            for q in qs:
                q = q.format(name=clean)
                hits = self._search_api(q) if self.api_key else self._search_ytdlp(q)
                for h in hits:
                    if not h.get("video_id") or not _relevant(h["title"], clean):
                        continue
                    h.update(slug=slug, kind=_kind(h["title"]) if _kind(h["title"]) != "other" else kind,
                             url=f"https://www.youtube.com/watch?v={h['video_id']}",
                             trusted=h.get("channel") in TRUSTED_CHANNELS)
                    found.setdefault(h["video_id"], h)
        # trusted business channels and interviews/roadshows first
        order = {"interview": 0, "roadshow": 1, "review": 2, "other": 3}
        return sorted(found.values(), key=lambda v: (not v["trusted"], order[v["kind"]]))

    # ---- transcript + analysis -----------------------------------------
    @staticmethod
    def transcript(video_id: str) -> str | None:
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
        except ImportError:
            return None
        langs = ["en", "en-IN", "hi"]
        try:
            if hasattr(YouTubeTranscriptApi, "fetch") and not hasattr(YouTubeTranscriptApi, "get_transcript"):
                parts = YouTubeTranscriptApi().fetch(video_id, languages=langs)   # >= 1.0
                return " ".join(p.text for p in parts)
            parts = YouTubeTranscriptApi.get_transcript(video_id, languages=langs)  # < 1.0
            return " ".join(p["text"] for p in parts)
        except Exception as e:
            log.debug("no transcript for %s: %s", video_id, e)
            return None

    @staticmethod
    def scan(text: str) -> dict:
        low = text.lower()
        counts = Counter({t: low.count(t) for t in RED_FLAG_TERMS if t in low})
        flags = []
        for term, n in counts.most_common(8):
            i = low.find(term)
            flags.append({"term": term, "count": n, "snippet": text[max(0, i - 100): i + 140].strip()})
        guidance = list(dict.fromkeys(m.group(0).strip() for m in GUIDANCE_RE.finditer(text)))[:10]
        return {"red_flags": flags, "guidance": guidance, "words": len(text.split())}

    def summarize(self, name: str, text: str) -> str | None:
        if not self.anthropic_key:
            return None
        prompt = (f"This is an auto-generated transcript of a video about the {name} IPO in India. "
                  "In under 150 words: (1) what management claims about growth, margins and use of IPO "
                  "money, with any numbers they give, (2) anything evasive or worth verifying in the RHP. "
                  "Say plainly if the transcript is too garbled to judge.\n\n" + text[:60000])
        try:
            r = self.http.s.post("https://api.anthropic.com/v1/messages", timeout=90, headers={
                "x-api-key": self.anthropic_key, "anthropic-version": "2023-06-01",
                "content-type": "application/json"},
                json={"model": self.llm_model, "max_tokens": 400,
                      "messages": [{"role": "user", "content": prompt}]})
            r.raise_for_status()
            return "".join(b.get("text", "") for b in r.json()["content"])
        except Exception as e:
            log.warning("LLM summary failed: %s", e)
            return None

    def collect(self, slug: str, name: str, analyse_top: int = 3) -> list[dict]:
        vids = self.find(slug, name)
        for v in vids[:analyse_top]:
            if v["kind"] in ("interview", "roadshow"):
                text = self.transcript(v["video_id"])
                if text:
                    v["transcript_flags"] = self.scan(text)
                    v["summary"] = self.summarize(name, text)
        for v in vids:
            v.pop("trusted", None)
        return vids
