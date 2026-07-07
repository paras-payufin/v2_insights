"""
Utility Functions
Helper functions for API requests, text processing, etc.
"""
import re
import logging
import time
import requests
from config.settings import MAX_POLL_ATTEMPTS, POLL_INTERVAL


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


def get_analysis(conversation_id, request_id, headers, base_url):
    """
    Poll GET /get_answer until status == "finished", then return the answer.

    Args:
        conversation_id: Conversation ID returned by /create_conversation
        request_id:      Request ID returned by /create_conversation
        headers:         API headers (must include X-Api-Key)
        base_url:        Toqan API base URL

    Returns:
        str: Completed answer text

    Raises:
        RuntimeError: On status==error, empty answer, or exhausted poll budget
    """
    url = f"{base_url}/get_answer"
    max_polls = MAX_POLL_ATTEMPTS
    poll_interval = POLL_INTERVAL
    backoff_wait = 0

    for attempt in range(1, max_polls + 1):
        if backoff_wait > 0:
            time.sleep(backoff_wait)
            backoff_wait = 0

        try:
            response = requests.get(
                url,
                headers=headers,
                params={
                    "conversation_id": conversation_id,
                    "request_id": request_id,
                },
                timeout=30,
            )
        except requests.exceptions.Timeout:
            logging.warning(f"[Poll {attempt}] Request timed out. Retrying.")
            time.sleep(poll_interval)
            continue

        if response.status_code == 429:
            backoff_wait = min(backoff_wait * 2 + 15, 60)
            logging.warning(f"[Poll {attempt}] Rate limited. Waiting {backoff_wait}s.")
            continue

        if response.status_code >= 500:
            logging.warning(
                f"[Poll {attempt}] Server error {response.status_code}. Retrying."
            )
            time.sleep(poll_interval)
            continue

        response.raise_for_status()

        try:
            data = response.json()
        except Exception:
            raise RuntimeError(
                f"[Poll {attempt}] Non-JSON response from /get_answer: "
                f"{response.text[:300]}"
            )

        status = data.get("status")
        logging.info(f"[Poll {attempt}/{max_polls}] status={status}")

        if status == "finished":
            answer = data.get("answer", "")
            if not answer or not answer.strip():
                raise RuntimeError(
                    "Toqan returned status=finished but answer is empty. "
                    f"conversation_id={conversation_id}"
                )
            logging.info(
                f"[Poll {attempt}] Completed. Answer length: {len(answer)} chars. "
                f"Preview: {answer[:200]!r}"
            )
            # Guard: if the answer looks like a file artifact reference instead of
            # actual content (e.g. "final_fraud_report.html"), the Toqan model
            # generated a downloadable file rather than returning HTML inline.
            # Failing loudly here is better than sending a broken template email.
            if len(answer.strip()) < 500 and not answer.strip().lower().lstrip().startswith(("<!doctype html", "<html")):
                raise RuntimeError(
                    f"Toqan returned status=finished but answer looks like a file "
                    f"reference or is too short to be a valid report "
                    f"({len(answer.strip())} chars). "
                    f"Answer: {answer.strip()!r}. "
                    f"The model may have generated a downloadable file artifact "
                    f"instead of returning the HTML inline. "
                    f"conversation_id={conversation_id}"
                )
            return answer

        elif status == "error":
            raise RuntimeError(
                f"Toqan returned status=error at poll {attempt}. "
                f"conversation_id={conversation_id}. "
                f"Full response: {data}"
            )

        elif status == "in_progress":
            time.sleep(poll_interval)

        else:
            logging.warning(
                f"[Poll {attempt}] Unexpected status value: '{status}'. "
                f"Treating as in_progress."
            )
            time.sleep(poll_interval)

    raise RuntimeError(
        f"Toqan did not return a finished response after {max_polls} polls "
        f"({max_polls * poll_interval}s). "
        f"conversation_id={conversation_id}, request_id={request_id}. "
        f"Consider increasing MAX_POLL_ATTEMPTS in config/settings.py."
    )
