"""
Utility Functions
Helper functions for API requests, text processing, etc.
"""
import re
import time
import requests
from config.settings import MAX_POLL_ATTEMPTS, POLL_INTERVAL, MIN_ANALYSIS_LENGTH


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


def is_thinking_message(message_text):
    """
    Detect AI meta-commentary vs actual analysis
    
    Args:
        message_text: Message text to check
        
    Returns:
        bool: True if message is thinking/meta-commentary
    """
    thinking_patterns = [
        "I need to", "I'll analyze", "Let me start", "I've analyzed",
        "Perfect! Now", "I should", "I will", "Here are the key deliverables"
    ]
    message_lower = message_text.lower()
    for pattern in thinking_patterns:
        if pattern.lower() in message_lower:
            return True
    return len(message_text.strip()) < 100


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

        # Extract non-thinking messages from Toqan.
        # Check both "Toqan" and any non-user/non-human author as fallback.
        user_authors = {"user", "human", "User", "Human"}
        for msg in messages:
            author = msg.get("author_id", "")
            message_text = msg.get("message", "")
            is_toqan = (author == "Toqan") or (author and author not in user_authors)
            if is_toqan and message_text:
                if not is_thinking_message(message_text):
                    analysis_messages.append(message_text)

        # Check if analysis is complete
        if analysis_messages:
            full_analysis = "\n\n".join(analysis_messages)
            if len(full_analysis) >= MIN_ANALYSIS_LENGTH:
                print(f"✓ Analysis complete ({len(full_analysis)} chars)")
                return full_analysis
            print(f"⏳ Analysis in progress... ({len(full_analysis)} chars)")

    # Return whatever we have if max attempts reached
    return full_analysis if analysis_messages else None
