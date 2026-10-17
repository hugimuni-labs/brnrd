"""Real DOM regression for the browser reader, without a logged-in profile.

Run with an interpreter that has playwright installed:
    python scripts/check_x_post_read.py
CHROMIUM_PATH optionally selects an already installed headless Chromium.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from brr import envoy_x_browser
from playwright.sync_api import sync_playwright


def article(post_id, author, text, extra=""):
    return f'''<article data-testid="tweet">
      <div data-testid="User-Name">{author}</div>
      <a href="/someone/status/{post_id}"><time datetime="2026-10-02T04:12:29Z">now</time></a>
      <div data-testid="tweetText">{text}</div>{extra}
      <div role="group"><button data-testid="reply" aria-label="1 reply"></button></div>
    </article>'''


def main():
    with sync_playwright() as pw, tempfile.TemporaryDirectory() as temp:
        options = {"headless": True}
        if os.environ.get("CHROMIUM_PATH"):
            options["executable_path"] = os.environ["CHROMIUM_PATH"]
        browser = pw.chromium.launch(**options)
        try:
            page = browser.new_page()
            driver = envoy_x_browser._PlaywrightDriver(
                envoy_x_browser.Paths.in_dir(temp), headless=True, sync_playwright=sync_playwright,
            )
            driver._page = page
            wanted = "2105873529963229452"
            focal = article(wanted, "focal author", "focal words")
            ancestor = article("123", "ancestor", "wrong words")
            quote = f'<a href="/quoted/status/{wanted}"><time>quote</time></a>'
            for html in [ancestor + focal, article("123", "ancestor", "wrong words", quote) + focal]:
                driver._goto = lambda url, html=html: page.set_content(html)
                result = driver.read_url(f"https://x.com/i/status/{wanted}")
                assert result["post_id"] == wanted
                assert result["author"] == "focal author" and result["text"] == "focal words"
                assert result["resolved_url"].endswith(f"/status/{wanted}")
                assert result["metrics"] == {"reply": "1 reply"}
            # A quote can arrive before the actual focal article hydrates.
            html = article("123", "ancestor", "wrong words", quote)
            driver._goto = lambda url: page.set_content(html)
            page_script = "setTimeout(() => document.body.insertAdjacentHTML('beforeend', html), 200)"
            original_goto = driver._goto
            def delayed(url):
                original_goto(url)
                page.evaluate("html => " + page_script, focal)
            driver._goto = delayed
            assert driver.read_url(f"https://x.com/i/status/{wanted}")["text"] == "focal words"
            # Never label an ancestor/quote as the absent requested post.
            envoy_x_browser.COMPOSER_TIMEOUT_MS = 200
            driver._goto = lambda url: page.set_content(html)
            try:
                driver.read_url(f"https://x.com/i/status/{wanted}")
            except RuntimeError as exc:
                assert "no article owns" in str(exc)
            else:
                raise AssertionError("a quoted id is not the article's own identity")
            print("PASS: ancestor, nested quote, delayed focal, missing own article; exact id/text/author/metrics")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
