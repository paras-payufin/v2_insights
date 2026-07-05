"""
Utility Functions
Helper functions for API requests, text processing, etc.
"""
import re
import time
import requests
from config.settings import MAX_POLL_ATTEMPTS, POLL_INTERVAL, MIN_ANALYSIS_LENGTH, MIN_HTML_ANALYSIS_LENGTH, THINK_DONE_PATIENCE


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


def extract_post_think_content(text):
    """
    Toqan thinking models wrap reasoning in <think>...</think> tags.
    The real output comes after the closing </think> tag.

    - If </think> is present: strip everything up to and including it,
      return only the content after it (stripped)
    - If <think> is present but </think> is not: model is still thinking,
      return empty string so the poller keeps waiting
    - If no <think> at all: return text unchanged (non-thinking model)
    """
    stripped = text.strip()

    has_open  = bool(re.search(r'<think>', stripped, re.IGNORECASE))
    has_close = bool(re.search(r'</think>', stripped, re.IGNORECASE))

    if has_open and not has_close:
        print("  [think] model still in <think> phase — waiting...")
        return ""

    if has_open and has_close:
        after = re.split(r'</think>', stripped, maxsplit=1, flags=re.IGNORECASE)[-1].strip()
        print(f"  [think] <think> block stripped — real content: {len(after)} chars")
        if after:
            return after
        print("  [think] Generating — Toqan is writing the report "
              "(invisible until complete)")
        return ""

    return text  # no think tags — return as-is


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
    think_done_zero_streak = 0      # consecutive polls with think=done but 0 output chars

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
                post_think = extract_post_think_content(message_text)

                if post_think == "" and "<think>" in message_text.lower():
                    # Model still thinking or finished with no output — skip
                    continue

                cleaned = strip_thinking_preamble(post_think) if post_think else ""
                if cleaned and not is_thinking_message(cleaned):
                    analysis_messages.append(cleaned)

        # Check if analysis is complete
        elapsed = int(time.time() - poll_start)
        if analysis_messages:
            full_analysis = "\n\n".join(analysis_messages)
            think_done_zero_streak = 0  # chars appeared — reset patience counter

            is_html = full_analysis.lstrip()[:20].lower().startswith(
                ("<!doctype html", "<html")
            )

            # Compute think-tag status from all Toqan messages for the log
            all_toqan_text = " ".join(
                m.get("message", "") for m in messages
                if not any(m.get("author_id", "").startswith(p) for p in user_author_prefixes)
                and m.get("author_id", "") not in user_author_exact
            )
            think_done   = "</think>" in all_toqan_text.lower()
            think_active = "<think>" in all_toqan_text.lower() and not think_done

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
                    f"{elapsed}s elapsed | "
                    f"{len(full_analysis):,} chars | "
                    f"think={'done' if think_done else 'active' if think_active else 'none'} | "
                    f"closing tag={'found' if closing_found else 'not yet'}]"
                )
            else:
                if len(full_analysis) >= MIN_ANALYSIS_LENGTH:
                    print(
                        f"✓ Analysis complete — "
                        f"{len(full_analysis):,} chars | {elapsed}s elapsed"
                    )
                    return full_analysis
                closing_found = False
                print(
                    f"  [poll attempt {attempt + 1}/{MAX_POLL_ATTEMPTS} | "
                    f"{elapsed}s elapsed | "
                    f"{len(full_analysis):,} chars | "
                    f"think={'done' if think_done else 'active' if think_active else 'none'} | "
                    f"closing tag=n/a]"
                )
        else:
            # No qualifying messages — still compute think status for the log
            all_toqan_text = " ".join(
                m.get("message", "") for m in messages
                if not any(m.get("author_id", "").startswith(p) for p in user_author_prefixes)
                and m.get("author_id", "") not in user_author_exact
            )
            think_done   = "</think>" in all_toqan_text.lower()
            think_active = "<think>" in all_toqan_text.lower() and not think_done
            closing_found = False

            if think_done:
                think_done_zero_streak += 1
                print(
                    f"  [think] Generating — </think> done, report not yet returned "
                    f"(streak {think_done_zero_streak} — still writing)"
                )
            else:
                think_done_zero_streak = 0

            print(
                f"  [poll attempt {attempt + 1}/{MAX_POLL_ATTEMPTS} | "
                f"{elapsed}s elapsed | "
                f"0 chars | "
                f"think={'done' if think_done else 'active' if think_active else 'none'} | "
                f"closing tag=n/a]"
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
        # No output at all — if think completed, raise a clear retryable error
        if think_done_zero_streak > 0:
            raise RuntimeError(
                f"Toqan completed thinking but produced no HTML output "
                f"after {MAX_POLL_ATTEMPTS} polls ({elapsed}s). "
                f"The file may be too large or the model stalled. "
                f"Try re-running or check the Toqan conversation: "
                f"{conversation_id}"
            )
        print(f"⚠ Max poll attempts reached — no analysis content received after {elapsed}s")

    return full_analysis if analysis_messages else None
