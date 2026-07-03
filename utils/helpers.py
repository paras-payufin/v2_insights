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

    Uses structural pattern detection — no hardcoded section names — so it works
    for any prompt format.

    Returns the original text unchanged if no preamble is detected.
    """
    stripped = message_text.strip()

    # ── HTML output ───────────────────────────────────────────────────────
    # Find where the actual HTML document starts.
    html_start = re.search(r'<!DOCTYPE\s+html|<html[\s>]', stripped, re.IGNORECASE)
    if html_start:
        extracted = stripped[html_start.start():]
        if len(extracted) > 200:
            return extracted

    # ── Plain text: bold markdown header at line start ────────────────────
    # Matches **Anything:** or **Anything —** or **Anything** on its own line.
    # Works generically for any prompt that uses bold section headers.
    bold_header = re.search(
        r'(?:^|\n)\s*\*\*[^*\n]{2,80}\*\*\s*[:\-]?\s*\n',
        stripped,
    )
    if bold_header:
        extracted = stripped[bold_header.start():].lstrip('\n')
        if len(extracted) > 200:
            return extracted

    # ── Plain text: numbered sections ────────────────────────────────────
    # Matches "1. Title" or "1) Title" at line start.
    numbered = re.search(
        r'(?:^|\n)\s*\d+[\.\)]\s+[A-Z][^\n]{5,}',
        stripped,
    )
    if numbered:
        extracted = stripped[numbered.start():].lstrip('\n')
        if len(extracted) > 200:
            return extracted

    return message_text


def is_thinking_message(message_text):
    """
    Return True only when the ENTIRE message is a thinking/meta-commentary
    with no real analysis content embedded — i.e. the preamble stripper found
    nothing to salvage.
    """
    # A complete HTML document is always real content — never thinking.
    if re.search(r'<!DOCTYPE\s+html|<html[\s>]', message_text, re.IGNORECASE) \
            and '</html>' in message_text.lower():
        return False

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
        "the user wants", "the user has", "the user is", "they want",
        "they've provided", "the task is", "the request is",
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
    poll_start = time.time()

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

        # On first attempt log every message in full detail
        if attempt == 0:
            print(f"  [poll] attempt 0 — total messages: {len(messages)}")
            for i, m in enumerate(messages):
                print(
                    f"  [poll] msg[{i}] "
                    f"author={m.get('author_id')!r} "
                    f"type={m.get('type')!r} "
                    f"len={len(m.get('message', ''))} "
                    f"preview={m.get('message', '')[:400]!r}"
                )

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
        elapsed = int(time.time() - poll_start)
        if analysis_messages:
            full_analysis = "\n\n".join(analysis_messages)

            is_html = full_analysis.lstrip()[:20].lower().startswith(
                ("<!doctype html", "<html")
            )

            if is_html:
                html_complete = (
                    "</html>" in full_analysis.lower()
                    and len(full_analysis) >= MIN_HTML_ANALYSIS_LENGTH
                )
                if html_complete:
                    print(
                        f"✓ HTML analysis complete — "
                        f"{len(full_analysis):,} chars | {elapsed}s elapsed"
                    )
                    return full_analysis
                closing_found = "</html>" in full_analysis.lower()
                print(
                    f"  [poll attempt {attempt + 1}/{MAX_POLL_ATTEMPTS} | "
                    f"{elapsed}s elapsed | {len(full_analysis):,} chars | "
                    f"closing tag={'found' if closing_found else 'not yet'}]"
                )
            else:
                if len(full_analysis) >= MIN_ANALYSIS_LENGTH:
                    print(
                        f"✓ Analysis complete — "
                        f"{len(full_analysis):,} chars | {elapsed}s elapsed"
                    )
                    return full_analysis
                print(
                    f"  [poll attempt {attempt + 1}/{MAX_POLL_ATTEMPTS} | "
                    f"{elapsed}s elapsed | {len(full_analysis):,} chars]"
                )
        else:
            print(
                f"  [poll attempt {attempt + 1}/{MAX_POLL_ATTEMPTS} | "
                f"{elapsed}s elapsed | no qualifying messages yet]"
            )

    # Max attempts reached
    elapsed = int(time.time() - poll_start)
    is_html_fallback = full_analysis.lstrip()[:20].lower().startswith(
        ("<!doctype html", "<html")
    ) if full_analysis else False
    closing_present = "</html>" in full_analysis.lower() if full_analysis else False

    if full_analysis:
        print(
            f"⚠ Max poll attempts reached — "
            f"{len(full_analysis):,} chars | {elapsed}s elapsed | "
            f"HTML={is_html_fallback} | closing tag={'present' if closing_present else 'MISSING'}"
        )
        print(f"  [fallback] first 500 chars: {full_analysis[:500]!r}")
        print(f"  [fallback] last 200 chars : {full_analysis[-200:]!r}")
    else:
        print(f"⚠ Max poll attempts reached — no analysis content received after {elapsed}s")

    return full_analysis if analysis_messages else None
