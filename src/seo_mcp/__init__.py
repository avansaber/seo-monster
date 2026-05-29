"""seo-mcp: an MCP server exposing strictly SEO-focused tools over Google
Search Console, Google Analytics 4, PageSpeed Insights, and Cloudflare.

User-credential-driven: no auth is baked into the package. Read-first: only
Cloudflare cache-purge tools are gated behind SEO_MCP_ALLOW_DESTRUCTIVE.
"""

__version__ = "0.5.0"
