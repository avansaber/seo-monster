"""Shared HTML content extraction (roadmap F1).

The existing ``onpage_tools._HeadParser`` only reads the document ``<head>``
surface (title, meta, canonical, og/twitter, h1 count). The roadmap tools that
reason about *body* content -- ``ai_citation_readiness`` now, ``content_brief``
and ``onpage_serp_gap`` later -- need headings text, visible body text + word
count, structural element counts, JSON-LD @types, and a server-vs-script signal
for the "is this page a blank shell to a non-JS AI crawler?" check.

Kept stdlib-only (``html.parser`` + ``re`` + ``json``) to preserve the flat
install footprint the project insists on. This is a content *extractor*, not a
DOM; it accumulates exactly the signals the tools score on.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_SKIP_TAGS = {"script", "style", "template"}
# Tokens that look like a statistic / number (incl. %, currency, decimals,
# thousands separators). A proxy for the GEO-paper "statistics" signal.
_NUMBER_RE = re.compile(r"(?<![\w.])(?:[$€£]\s?)?\d[\d,]*(?:\.\d+)?\s?%?")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]+")


class ContentParser(HTMLParser):
    """One-pass body+structure extractor."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self._in_title = False
        self.meta_description: str | None = None
        self.headings: list[dict[str, Any]] = []  # {level, text}
        self._heading_level = 0
        self._heading_buf: list[str] = []
        self._skip_depth = 0
        self._text_parts: list[str] = []
        self.paragraphs = 0
        self.list_items = 0
        self.tables = 0
        self.blockquotes = 0
        self.inline_quotes = 0
        self.images = 0
        self.script_count = 0
        self.has_noscript = False
        self.hrefs: list[dict[str, str]] = []  # {href, rel}
        self.jsonld_blocks: list[str] = []
        self._in_jsonld = False
        self._jsonld_buf: list[str] = []
        self.time_tags = 0  # <time> / datetime hints, a weak freshness signal

    # -- tags ---------------------------------------------------------------
    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {k.lower(): (v or "") for k, v in attrs_list}
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            if tag == "script":
                self.script_count += 1
                if attrs.get("type", "").lower() == "application/ld+json":
                    self._in_jsonld = True
                    self._jsonld_buf = []
            return
        if tag == "noscript":
            self.has_noscript = True
            return
        if tag == "title":
            self._in_title = True
            return
        if tag == "meta":
            if attrs.get("name", "").lower() == "description" and self.meta_description is None:
                self.meta_description = attrs.get("content", "")
            return
        if tag in _HEADING_TAGS:
            self._heading_level = int(tag[1])
            self._heading_buf = []
            return
        if tag == "p":
            self.paragraphs += 1
        elif tag == "li":
            self.list_items += 1
        elif tag == "table":
            self.tables += 1
        elif tag == "blockquote":
            self.blockquotes += 1
        elif tag == "q":
            self.inline_quotes += 1
        elif tag == "img":
            self.images += 1
        elif tag == "time":
            self.time_tags += 1
        elif tag == "a":
            href = attrs.get("href", "").strip()
            if href:
                self.hrefs.append({"href": href, "rel": attrs.get("rel", "")})

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Route void/self-closed tags (e.g. <img/>, <meta/>) through starttag.
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
            if tag == "script" and self._in_jsonld:
                self.jsonld_blocks.append("".join(self._jsonld_buf))
                self._in_jsonld = False
                self._jsonld_buf = []
            return
        if tag == "title":
            self._in_title = False
            return
        if tag in _HEADING_TAGS:
            text = " ".join("".join(self._heading_buf).split())
            if self._heading_level:
                self.headings.append({"level": self._heading_level, "text": text})
            self._heading_level = 0
            self._heading_buf = []

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._jsonld_buf.append(data)
            return
        if self._skip_depth > 0:
            return
        if self._in_title:
            self.title = (self.title or "") + data
            return
        if self._heading_level:
            self._heading_buf.append(data)
        self._text_parts.append(data)

    # -- derived ------------------------------------------------------------
    @property
    def text(self) -> str:
        return " ".join(" ".join(self._text_parts).split())

    @property
    def word_count(self) -> int:
        return len(_WORD_RE.findall(self.text))

    @property
    def number_count(self) -> int:
        return len(_NUMBER_RE.findall(self.text))

    def heading_levels(self) -> dict[int, int]:
        out: dict[int, int] = {}
        for h in self.headings:
            out[h["level"]] = out.get(h["level"], 0) + 1
        return out

    def jsonld_types(self) -> list[str]:
        return jsonld_types(self.jsonld_blocks)

    def outbound_links(self, base_url: str) -> int:
        """Count links that resolve to a different host than ``base_url`` -- a
        proxy for the GEO 'cite sources' signal."""
        base_host = urlparse(base_url).netloc.lower()
        n = 0
        for link in self.hrefs:
            href = link["href"]
            if href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            resolved = urljoin(base_url, href)
            host = urlparse(resolved).netloc.lower()
            if host and host != base_host:
                n += 1
        return n

    def top_token_ratio(self) -> tuple[str | None, float]:
        """Most frequent content word and its share of all content words -- a
        keyword-stuffing proxy. Stopwords excluded."""
        words = [w.lower() for w in _WORD_RE.findall(self.text) if w.lower() not in _STOPWORDS and len(w) > 2]
        if not words:
            return None, 0.0
        freq: dict[str, int] = {}
        for w in words:
            freq[w] = freq.get(w, 0) + 1
        top, count = max(freq.items(), key=lambda kv: kv[1])
        return top, count / len(words)


def jsonld_types(blocks: list[str]) -> list[str]:
    """Extract the set of schema.org @type values from raw JSON-LD blocks,
    handling arrays and ``@graph``. Best-effort: malformed blocks are skipped."""
    types: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            t = node.get("@type")
            if isinstance(t, str):
                types.append(t)
            elif isinstance(t, list):
                types.extend(str(x) for x in t)
            for key in ("@graph", "mainEntity", "itemListElement"):
                if key in node:
                    walk(node[key])
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for raw in blocks:
        raw = raw.strip()
        if not raw:
            continue
        try:
            walk(json.loads(raw))
        except (ValueError, TypeError):
            continue
    # de-dupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for t in types:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def parse_content(html: str) -> ContentParser:
    p = ContentParser()
    p.feed(html)
    return p


# Small English stopword set for the stuffing proxy + lexical relevance. Kept
# short on purpose (stdlib-only, no NLTK); good enough for ratio signals.
_STOPWORDS = frozenset(
    """a an and are as at be by for from has have how in is it its of on or that the
    to was were what when where which who will with your you this these those they them
    their our we us i me my he she his her not but if then than so such can could would
    should may might do does did done about into over under more most some any all each
    one two three new best top how-to guide vs via per""".split()
)

# Broader stopword set for surfaced "entities to cover" (tester F2): the raw
# document-frequency tokens leaked filler like "good / just / same / thank /
# main / others / thought". Entities use this stricter list + a >=4 length floor.
_ENTITY_STOPWORDS = _STOPWORDS | frozenset(
    """good just same thank thanks main other others thought think thing things like
    also even much many well way ways get got make made use used uses using need needs
    want wants know knows see look looks find finds help helps great easy simple free
    less very really actually basically simply first last next back around across within
    without before after while because however therefore example examples including
    include includes etc lots people person time times day days year years today here
    there everything anything something nothing someone everyone able available based
    given plus minus into onto from with what when where which while only ever never
    always often sometimes maybe perhaps quite rather still yet else also both either
    neither each every another such same own""".split()
)

# --- chrome / boilerplate heading filter (tester F1) ----------------------
# Nav / CTA / subscribe / footer / tagline headings leak into heading extraction
# and become nonsense gap actions ("Add a section covering: 'Table of contents'").
# We filter by pattern CLASSES (not a tuned string list -- tester Round 3), so the
# filter generalizes past the specific strings seen. Conservative on ambiguous
# CONTENT headings: tutorial/how-to imperatives (Create/Build/Install/Configure/
# Set up), and real sections (Pricing, Features, How it works, FAQ) survive.

# Class 1 -- subscribe / newsletter / share / cookie / legal / related-content /
# nav-module phrases (substring, case-insensitive).
_CHROME_PHRASES = (
    # table-of-contents / in-page nav
    "table of contents", "on this page", "in this article", "in this guide",
    "jump to", "skip to", "back to top",
    # subscribe / newsletter
    "newsletter", "subscribe", "sign up", "sign-up", "sign in", "log in", "login",
    "stay updated", "stay in the loop", "get updates", "get the latest",
    "latest news", "weekly news", "news & analysis", "news and analysis",
    "listen for", "to your inbox", "in your inbox", "join our", "follow us",
    "follow along", "you are subscribed",
    # related-content modules
    "related article", "related post", "related product", "related service",
    "related reading", "related content", "similar article", "similar post",
    "you might also", "you may also", "recommended for you", "more from",
    "more stories", "read next", "up next", "popular post", "trending",
    "featured", "see also", "explore more", "keep reading",
    # CTA / contact / demo
    "free trial", "start free", "get started", "book a demo", "request a demo",
    "get a demo", "contact us", "contact sales", "talk to sales", "talk to us",
    "schedule a", "ways to work", "you were promised",
    # social proof / footer / legal
    "trusted by", "loved by", "rated", "share this", "share on", "leave a comment",
    "privacy policy", "terms of service", "terms and conditions",
    "all rights reserved", "cookie", "products and pricing", "products & pricing",
    "solutions and services", "products and services",
)

# Class 2 -- generic nav/footer labels (exact match after normalization). Kept to
# vague nav words that are essentially never a content subtopic; deliberately
# EXCLUDES ambiguous real sections (pricing, features, how it works, faq).
_CHROME_EXACT = frozenset({
    "resources", "engage", "menu", "navigation", "search", "company", "careers",
    "legal", "sitemap", "newsletter", "subscribe", "share", "follow", "support",
    "community", "partners", "about", "contact", "overview", "solutions",
    "products", "platform", "use cases", "customers", "developers", "integrations",
    "industries", "company news", "what's new", "get the app",
})

# Class 3 -- marketing-CTA / tagline verbs at the START of the heading. Curated to
# PROMOTIONAL verbs only, so tutorial-step imperatives (create/build/install/
# configure/add/set up/run/deploy) are NOT filtered.
_MARKETING_VERBS = frozenset({
    "meet", "discover", "solve", "transform", "unlock", "boost", "grow", "scale",
    "supercharge", "reimagine", "simplify", "streamline", "accelerate", "empower",
    "achieve", "elevate", "introducing", "announcing", "experience", "imagine",
    "unleash",
})
_CTA_STARTS = (
    "sign up", "sign-up", "get started", "connect to", "connect with",
    "book a demo", "request a demo", "start free", "start your", "try ",
    "download the", "download our", "join the", "join our", "join ", "subscribe",
    "ready to", "listen ", "watch the", "see how", "learn how", "find out",
)
_LISTICLE_RE = re.compile(r"^\s*\d+\s+(best|top|alternatives|ways|tips|tools|reasons)\b", re.IGNORECASE)
# Class 4 -- "Why <Brand>" nav (a single Capitalized proper-noun token after Why).
_WHY_BRAND_RE = re.compile(r"^[Ww]hy\s+[A-Z][A-Za-z0-9.]+(?:\s+[A-Z][A-Za-z0-9.]+)?$")


def is_chrome_heading(text: str) -> bool:
    """True for nav/CTA/subscribe/footer/tagline headings that are not real
    content subtopics. Pattern-class based so it generalizes past specific
    strings; conservative on real content + tutorial-step headings."""
    t = " ".join((text or "").split())
    if not t:
        return True
    tl = t.lower()
    words = t.split()
    if tl in _CHROME_EXACT:
        return True
    if any(p in tl for p in _CHROME_PHRASES):
        return True
    if any(tl.startswith(v) for v in _CTA_STARTS):
        return True
    if words and words[0].lower() in _MARKETING_VERBS:   # marketing/tagline CTA
        return True
    if _WHY_BRAND_RE.match(t):
        return True
    if _LISTICLE_RE.match(t):
        return True
    if t.endswith("!"):
        return True
    # A heading that ends in a sentence period is a tagline, not a section head
    # (real headings are not punctuated as sentences).
    if t.endswith(".") and not t.endswith("..") and len(words) >= 3:
        return True
    if len(words) > 12:                 # a sentence/tagline, not a heading
        return True
    if "," in t and len(words) >= 7:    # comma-y marketing tagline
        return True
    return False


def content_terms(text: str) -> set[str]:
    """Lowercased content-word set (>=3 chars, stopwords removed). Used for the
    lexical relevance matching in internal_link_recommend and gsc_keyword_expand
    (the free, no-LLM relevance tier)."""
    return {w.lower() for w in _WORD_RE.findall(text) if len(w) >= 3 and w.lower() not in _STOPWORDS}


def content_entities(text: str) -> set[str]:
    """Stricter token set for surfaced 'entities to cover' (tester F2): >=4 chars
    and a broader stopword list, so the host is not handed 'cover the entity:
    good'. Still lexical (not NER) -- the caveat discloses that."""
    return {w.lower() for w in _WORD_RE.findall(text) if len(w) >= 4 and w.lower() not in _ENTITY_STOPWORDS}
