"""Tests for the shared content parser + helpers (roadmap F1)."""

from __future__ import annotations

from seo_mcp.tools._html import (
    content_entities,
    content_terms,
    is_chrome_heading,
    jsonld_types,
    parse_content,
)

_DOC = """
<html><head><title>Blue Widgets Guide</title>
<meta name="description" content="All about widgets">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Article","headline":"Widgets"}</script>
</head>
<body>
<h1>Blue Widgets</h1>
<p>In 2026, 45% of buyers preferred blue widgets, up from 1,200 units.</p>
<h2>Why blue</h2>
<blockquote>"Widgets matter," said an expert.</blockquote>
<p>See <a href="https://other.com/study">the study</a> and
<a href="https://ref.org/data">the data</a> and <a href="/internal">internal</a>.</p>
<h3>Details</h3>
<ul><li>one</li><li>two</li></ul>
<script>var x = 1;</script>
</body></html>
"""


def test_parses_title_headings_and_text():
    p = parse_content(_DOC)
    assert p.title == "Blue Widgets Guide"
    assert p.meta_description == "All about widgets"
    assert {h["level"] for h in p.headings} == {1, 2, 3}
    assert any(h["text"] == "Blue Widgets" for h in p.headings)
    assert p.heading_levels()[2] == 1
    assert p.word_count > 10
    # JSON-LD script content must NOT leak into body text.
    assert "schema.org" not in p.text
    assert "var x" not in p.text


def test_counts_numbers_quotes_lists():
    p = parse_content(_DOC)
    assert p.number_count >= 2  # "2026", "45%", "1,200"
    assert p.blockquotes == 1
    assert p.list_items == 2
    assert p.script_count == 2  # ld+json + the var script


def test_outbound_links_external_only():
    p = parse_content(_DOC)
    # two external (other.com, ref.org); the /internal one resolves to base host
    assert p.outbound_links("https://example.com/page") == 2


def test_jsonld_types_from_blocks_and_graph():
    blocks = [
        '{"@context":"https://schema.org","@type":"Article"}',
        '{"@graph":[{"@type":"Organization"},{"@type":["WebPage","FAQPage"]}]}',
        "not json{",
    ]
    types = jsonld_types(blocks)
    assert "Article" in types
    assert "Organization" in types
    assert "FAQPage" in types


def test_top_token_ratio_detects_stuffing():
    stuffed = "<html><body><p>" + ("widgets " * 50) + "and a few other words here</p></body></html>"
    p = parse_content(stuffed)
    top, ratio = p.top_token_ratio()
    assert top == "widgets"
    assert ratio > 0.5


def test_content_terms_drops_stopwords_and_short():
    terms = content_terms("The best Blue Widgets for you")
    assert "blue" in terms and "widgets" in terms
    assert "the" not in terms and "for" not in terms and "you" not in terms


# --- F1: chrome/boilerplate heading filter --------------------------------

def test_is_chrome_heading_filters_boilerplate():
    chrome = [
        # Round 2 set
        "Table of contents", "Connect to 1,000+ apps",
        "Trusted by teams moving beyond just tickets",
        "Thank you! You are subscribed.",
        "The latest tech news, backed by expert insights",
        "Resources", "Engage", "Subscribe to our newsletter",
        "7 best Budibase alternatives I've tested in 2026",
        "Privacy Policy", "Sign up for free",
        # Round 3 leaks -- must be caught by pattern CLASSES, not tuned strings
        "Listen for weekly AI news & analysis",  # newsletter CTA (reached actions)
        "Similar Articles", "Related products and services",
        "Meet your personal intelligence.",      # marketing tagline
        "The Personal AI you were promised",
        "Discover better ways to work",
        "Solve your business challenges with Google Cloud",
        "Products and pricing", "Solutions", "Why Google",
    ]
    for h in chrome:
        assert is_chrome_heading(h), h


def test_is_chrome_heading_keeps_real_sections():
    real = [
        # real content sections
        "Pricing", "How it works", "Why blue widgets", "Features comparison",
        "Installation steps", "What is an MCP server", "Frequently asked questions",
        # tutorial-step imperatives must survive (not marketing verbs)
        "Create a new project", "Build your first app", "Install the SDK",
        "Configure authentication", "Set up your workspace", "Add a code block",
        "Explore the API reference",
    ]
    for h in real:
        assert not is_chrome_heading(h), h


# --- F2: stricter entity tokens -------------------------------------------

def test_content_entities_drops_filler_keeps_real():
    e = content_entities("good just same thank main widgets automation pricing")
    assert {"widgets", "automation", "pricing"} <= e
    for junk in ("good", "just", "same", "thank", "main"):
        assert junk not in e
