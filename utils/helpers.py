"""
Utility Functions
Helper functions for API requests, text processing, etc.
"""
import re
import time
import requests
from config.settings import MAX_POLL_ATTEMPTS, POLL_INTERVAL, MIN_ANALYSIS_LENGTH, MIN_HTML_ANALYSIS_LENGTH


def remove_emojis(text):
    """Remove emoji characters from text."""
    emoji_pattern = re.compile("["
        u"\U0001F600-\U0001F64F"
        u"\U0001F300-\U0001F5FF"
        u"\U0001F680-\U0001F6FF"
        u"\U0001F1E0-\U0001F1FF"
        u"\U00002702-\U000027B0"
        u"\U000024C2-\U0001F251"
        u"\U0001F900-\U0001F9FF"
        "]+", flags=re.UNICODE)
    return emoji_pattern.sub(r'', text)


def strip_thinking_preamble(message_text):
    """
    Remove AI thinking/planning preamble from the start of a message and return
    only the actual analysis content.

    Handles two output types:
    - HTML output: looks for <!DOCTYPE or <html as the real start
    - Plain text output: looks for the first recognised section header

    Returns the original text unchanged if no preamble is detected.
    """
    import re

    stripped = message_text.strip()

    # ── HTML output ───────────────────────────────────────────────────────
    # If Toqan generated an HTML document, find where it actually starts.
    html_start = re.search(r'<!DOCTYPE\s+html|<html[\s>]', stripped, re.IGNORECASE)
    if html_start:
        extracted = stripped[html_start.start():]
        if len(extracted) > 200:
            return extracted

    # ── Plain text output ─────────────────────────────────────────────────
    # Section headers that mark the start of real analysis output.
    section_headers = [
        "overall", "top takeaway", "key takeaway", "positives", "key finding",
        "recommendation", "kpi summary", "psi analysis", "csi", "disbursal",
        "early warning", "score distribution", "variable stability",
        "summary", "status", "model health",
    ]
    pattern = r'(?:^|\n)\s*\*{0,2}(' + '|'.join(section_headers) + r')[:\s*]'
    match = re.search(pattern, message_text, re.IGNORECASE)
    if match:
        extracted = message_text[match.start():].lstrip('\n')
        if len(extracted) > 200:
            return extracted

    return message_text


def is_thinking_message(message_text):
    """
    Return True only when the ENTIRE message is a thinking/meta-commentary
    with no real analysis content embedded — i.e. the preamble stripper found
    nothing to salvage.
    """
    stripped = strip_thinking_preamble(message_text).strip()

    # If stripping moved us significantly into the text, real content exists
    original_stripped = message_text.strip()
    if len(original_stripped) > 0 and stripped != original_stripped:
        return False  # has real content after the preamble

    # Pure thinking-only message patterns (start of message)
    thinking_start_patterns = [
        "let me", "now let me", "i will", "i'll", "i need to", "i should",
        "i am going to", "to analyze", "to summarize", "in order to",
        "perfect", "great!", "sure,", "certainly", "of course", "absolutely",
        "here is my", "here are", "i have analyzed", "i've analyzed",
        "i've reviewed", "based on my analysis", "after reviewing",
        "looking at the data", "i'll now", "allow me", "i'll start",
        "i'll begin", "first, i", "now i'll", "now i will", "let's", "let us",
        "the user wants", "the user has", "they want", "they've provided",
    ]
    thinking_anywhere_patterns = [
        "here are the key deliverables", "i need to perform",
        "now let me perform", "let me perform", "let me analyze",
        "let me create", "let me generate", "let me examine",
    ]

    start_lower = stripped[:120].lower()
    full_lower = stripped.lower()

    for pattern in thinking_start_patterns:
        if start_lower.startswith(pattern):
            return True
    for pattern in thinking_anywhere_patterns:
        if pattern in full_lower:
            return True

    return len(stripped) < 100


def make_api_request(method, url, headers, json_data=None, files=None, max_retries=5):
    """
    Make API request with exponential backoff
    
    Args:
        method: HTTP method (POST, PUT, GET)
        url: API endpoint URL
        headers: Request headers
        json_data: JSON payload for POST requests
        files: Files payload for PUT requests
        max_retries: Maximum retry attempts
        
    Returns:
        Response object (HTTP 2xx only; raises requests.HTTPError otherwise, except 429 which is retried)
    """
    for attempt in range(max_retries):
        try:
            if method == 'POST':
                response = requests.post(url, headers=headers, json=json_data, timeout=30)
            elif method == 'PUT':
                response = requests.put(url, headers=headers, files=files, timeout=120)
            elif method == 'GET':
                response = requests.get(url, headers=headers, timeout=30)
            
            # Handle rate limiting
            if response.status_code == 429:
                wait_time = (2 ** attempt) * 5
                print(f"⏳ Rate limit hit. Waiting {wait_time}s...")
                time.sleep(wait_time)
                continue

            if not response.ok:
                response.raise_for_status()

            return response
            
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                print(f"⏳ Request timeout. Retrying ({attempt + 1}/{max_retries})...")
                time.sleep(5)
                continue
            raise
    
    raise Exception("Max retries reached")


def get_analysis(conversation_id, headers, base_url):
    """
    Wait for and retrieve complete analysis from Toqan
    
    Args:
        conversation_id: Conversation ID from Toqan
        headers: API headers
        base_url: Toqan API base URL
        
    Returns:
        str: Complete analysis text
    """
    print(f"⏳ Waiting for analysis (min {MIN_ANALYSIS_LENGTH} chars)...")

    analysis_messages = []
    full_analysis = ""

    for attempt in range(MAX_POLL_ATTEMPTS):
        time.sleep(POLL_INTERVAL)

        response = requests.post(
            f"{base_url}/find_conversation",
            headers=headers,
            json={"conversation_id": conversation_id},
            timeout=30,
        )

        if response.status_code == 429:
            wait_time = min(60, (2 ** min(attempt, 4)) * 2)
            print(f"⏳ find_conversation rate limited (429). Waiting {wait_time}s...")
            time.sleep(wait_time)
            continue

        if 500 <= response.status_code < 600:
            print(
                f"⚠ find_conversation server error {response.status_code}; "
                f"will retry ({attempt + 1}/{MAX_POLL_ATTEMPTS})..."
            )
            continue

        if response.status_code != 200:
            response.raise_for_status()

        try:
            messages = response.json()
        except ValueError as e:
            snippet = (response.text or "")[:300]
            raise RuntimeError(
                f"find_conversation returned non-JSON (HTTP {response.status_code}): {snippet!r}"
            ) from e

        if not isinstance(messages, list):
            raise RuntimeError(
                f"find_conversation expected a JSON list, got {type(messages).__name__}"
            )

        analysis_messages = []

        # Debug: log message structure on first attempt so we can see what the API returns
        if attempt == 0 and messages:
            first = messages[0]
            print(f"  [debug] message keys: {list(first.keys())}")
            print(f"  [debug] first author_id: {first.get('author_id')!r}")
            print(f"  [debug] total messages: {len(messages)}")

        # Extract Toqan's response messages only.
        # Exclude the user/sender message — identified by "apikey_" prefix
        # (the API key used to send the prompt) or known user author labels.
        user_author_prefixes = ("apikey_",)
        user_author_exact = {"user", "human", "User", "Human"}
        for msg in messages:
            author = msg.get("author_id", "")
            message_text = msg.get("message", "")
            is_user_msg = (
                any(author.startswith(p) for p in user_author_prefixes)
                or author in user_author_exact
            )
            if not is_user_msg and message_text:
                cleaned = strip_thinking_preamble(message_text)
                if not is_thinking_message(cleaned):
                    analysis_messages.append(cleaned)

        # Check if analysis is complete
        if analysis_messages:
            full_analysis = "\n\n".join(analysis_messages)

            is_html = full_analysis.lstrip()[:20].lower().startswith(
                ("<!doctype html", "<html")
            )

            if is_html:
                # HTML reports are large (30-150 KB). Only accept when the
                # document is structurally complete AND meets the minimum size.
                html_done = (
                    "</html>" in full_analysis.lower()
                    and len(full_analysis) >= MIN_HTML_ANALYSIS_LENGTH
                )
                if html_done:
                    print(f"✓ HTML analysis complete ({len(full_analysis):,} chars)")
                    return full_analysis
                print(
                    f"⏳ HTML in progress... ({len(full_analysis):,} chars, "
                    f"complete={('</html>' in full_analysis.lower())})"
                )
            else:
                if len(full_analysis) >= MIN_ANALYSIS_LENGTH:
                    print(f"✓ Analysis complete ({len(full_analysis)} chars)")
                    return full_analysis
                print(f"⏳ Analysis in progress... ({len(full_analysis)} chars)")

    # Max attempts reached — return whatever we have.
    # For HTML: warn if document is incomplete (missing </html>).
    if full_analysis and full_analysis.lstrip()[:20].lower().startswith(
        ("<!doctype html", "<html")
    ):
        if "</html>" not in full_analysis.lower():
            print(
                f"⚠ Max poll attempts reached — HTML document is incomplete "
                f"({len(full_analysis):,} chars, no </html> closing tag). "
                f"Toqan may need more time. Consider increasing MAX_POLL_ATTEMPTS."
            )
    return full_analysis if analysis_messages else None
