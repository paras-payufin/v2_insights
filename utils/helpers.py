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


def extract_html_document(text):
    """
    Return only the <!DOCTYPE html>...</html> document contained in `text`,
    discarding any preamble or trailing commentary the model may have leaked
    (e.g. "I'll now generate the HTML..." before the actual markup).

    Falls back to the original text unchanged if no HTML document is found,
    so plain-text answers (e.g. from non-HTML prompts) are left untouched.
    """
    lower = text.lower()
    start_idx = None
    for marker in ("<!doctype html", "<html"):
        idx = lower.find(marker)
        if idx != -1 and (start_idx is None or idx < start_idx):
            start_idx = idx

    if start_idx is None:
        return text

    end_idx = lower.rfind("</html>")
    if end_idx == -1:
        return text[start_idx:].strip()

    end_idx += len("</html>")
    return text[start_idx:end_idx].strip()


def _looks_like_html(text):
    if not text or not str(text).strip():
        return False
    return str(text).strip().lower().lstrip().startswith(("<!doctype html", "<html"))


def _download_toqan_file(conversation_id, file_name, headers, base_url):
    """
    Download a Toqan conversation file via GET /download_file.
    Returns decoded text, or None on failure.
    """
    try:
        response = requests.get(
            f"{base_url}/download_file",
            headers=headers,
            params={
                "conversation_id": conversation_id,
                "file_name": file_name,
            },
            timeout=120,
        )
        if not response.ok:
            logging.warning(
                f"download_file failed for {file_name!r}: "
                f"HTTP {response.status_code} {response.text[:200]!r}"
            )
            return None
        # Prefer UTF-8; fall back to response encoding / latin-1 for binary-ish payloads
        try:
            return response.content.decode("utf-8")
        except UnicodeDecodeError:
            return response.content.decode(response.encoding or "latin-1", errors="replace")
    except requests.RequestException as exc:
        logging.warning(f"download_file request error for {file_name!r}: {exc}")
        return None


def _recover_answer_from_attachments(conversation_id, attachments, headers, base_url):
    """
    If /get_answer returned empty text but listed attachments, try to pull
    HTML content from those files via /download_file.
    """
    for attachment in attachments or []:
        name = (attachment or {}).get("name") or ""
        mime = ((attachment or {}).get("mime_type") or "").lower()
        looks_html = name.lower().endswith((".html", ".htm")) or "html" in mime
        if not name or not looks_html:
            continue
        logging.warning(
            f"Recovering inline answer from attachment {name!r} "
            f"(mime={mime!r}) via /download_file"
        )
        content = _download_toqan_file(conversation_id, name, headers, base_url)
        if content and _looks_like_html(content):
            return extract_html_document(content)
        if content and len(content.strip()) >= 500:
            return content
    return None


def _recover_answer_from_conversation(conversation_id, headers, base_url):
    """
    Fallback for empty /get_answer payloads: POST /find_conversation and
    extract the latest assistant message that contains HTML (or substantial text).
    """
    try:
        response = requests.post(
            f"{base_url}/find_conversation",
            headers=headers,
            json={"conversation_id": conversation_id},
            timeout=60,
        )
    except requests.RequestException as exc:
        logging.warning(f"find_conversation request error: {exc}")
        return None

    if not response.ok:
        logging.warning(
            f"find_conversation failed: HTTP {response.status_code} "
            f"{response.text[:200]!r}"
        )
        return None

    try:
        items = response.json()
    except Exception:
        logging.warning("find_conversation returned non-JSON body")
        return None

    if not isinstance(items, list):
        logging.warning(f"find_conversation unexpected payload type: {type(items)}")
        return None

    # Walk newest → oldest; prefer HTML messages.
    for item in reversed(items):
        if not isinstance(item, dict):
            continue
        message = item.get("message") or ""
        if _looks_like_html(message):
            logging.warning(
                "Recovered HTML answer from /find_conversation "
                f"(item_id={item.get('id')!r}, len={len(message)})"
            )
            return extract_html_document(message)

        # Attachment listed on a conversation item but missing from /get_answer
        item_attachments = item.get("attachments") or []
        recovered = _recover_answer_from_attachments(
            conversation_id, item_attachments, headers, base_url
        )
        if recovered:
            return recovered

    # Last resort: longest non-user-looking message with substantial content
    candidates = [
        (item.get("message") or "")
        for item in items
        if isinstance(item, dict) and (item.get("message") or "").strip()
    ]
    # Skip the user prompt (usually the longest non-HTML instruction blob) by
    # preferring messages that look like deliverables, not prompt text.
    for message in reversed(candidates):
        stripped = message.strip()
        if len(stripped) < 500:
            continue
        if stripped.lower().startswith(("you are a senior", "html envelope", "analyze")):
            continue
        logging.warning(
            f"Recovered non-HTML answer from /find_conversation (len={len(stripped)})"
        )
        return stripped

    return None


# Stub HTML shells (header-only) from Toqan file artifacts are typically ~3–5KB.
# A real multi-section Chart.js monitoring report is much larger.
_MIN_HTML_REPORT_CHARS = 10000


def _validate_finished_answer(answer, attachments, conversation_id, require_html=True):
    """
    Ensure a finished Toqan payload is usable.
    When require_html=True, enforce a full HTML report.
    When require_html=False (e.g. prompt-briefing step), only require non-empty text.
    """
    if not answer or not str(answer).strip():
        raise RuntimeError(
            "Toqan returned status=finished but answer is empty. "
            f"attachments={attachments!r}. "
            f"conversation_id={conversation_id}"
        )

    if not require_html:
        return answer

    looks_like_html = _looks_like_html(answer)
    answer_len = len(answer.strip())

    if attachments and not looks_like_html:
        raise RuntimeError(
            f"Toqan returned status=finished but did not return a valid "
            f"inline HTML report ({answer_len} chars, "
            f"attachments={attachments!r}). "
            f"Answer: {answer.strip()[:300]!r}. "
            f"The model may have generated a downloadable file artifact "
            f"instead of returning the HTML inline. "
            f"conversation_id={conversation_id}"
        )
    if answer_len < 500 and not looks_like_html:
        raise RuntimeError(
            f"Toqan returned status=finished but did not return a valid "
            f"inline HTML report ({answer_len} chars, "
            f"attachments={attachments!r}). "
            f"Answer: {answer.strip()!r}. "
            f"conversation_id={conversation_id}"
        )
    if looks_like_html and answer_len < _MIN_HTML_REPORT_CHARS:
        raise RuntimeError(
            f"Toqan returned an HTML stub ({answer_len} chars; "
            f"minimum {_MIN_HTML_REPORT_CHARS}). Likely a header-only shell "
            f"from a file artifact instead of a full report. "
            f"attachments={attachments!r}. "
            f"conversation_id={conversation_id}. "
            f"Preview: {answer.strip()[:300]!r}"
        )
    return answer


def get_analysis(conversation_id, request_id, headers, base_url, require_html=True):
    """
    Poll GET /get_answer until status == "finished", then return the answer.

    If Toqan marks the request finished with an empty answer, fall back to:
      1) HTML attachments via GET /download_file
      2) conversation history via POST /find_conversation

    Args:
        conversation_id: Conversation ID returned by /create_conversation
        request_id:      Request ID returned by /create_conversation
                         or /continue_conversation
        headers:         API headers (must include X-Api-Key)
        base_url:        Toqan API base URL
        require_html:    If True, validate a full HTML report. If False, accept
                         any non-empty reply (used for prompt-briefing turns).

    Returns:
        str: Completed answer text

    Raises:
        RuntimeError: On status==error, empty/unrecoverable answer, or exhausted poll budget
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
            answer = data.get("answer") or ""
            attachments = data.get("attachments") or []
            if attachments:
                names = [a.get("name") for a in attachments]
                logging.warning(
                    f"[Poll {attempt}] Toqan response includes {len(attachments)} "
                    f"file attachment(s): {names}. Will recover via /download_file "
                    f"when inline answer is empty, non-HTML, or shorter than the file."
                )

            if not answer.strip():
                recovered = _recover_answer_from_attachments(
                    conversation_id, attachments, headers, base_url
                )
                if not recovered:
                    recovered = _recover_answer_from_conversation(
                        conversation_id, headers, base_url
                    )
                if recovered:
                    answer = recovered
                    logging.warning(
                        f"[Poll {attempt}] Recovered answer after empty "
                        f"/get_answer payload ({len(answer)} chars)."
                    )
                else:
                    raise RuntimeError(
                        "Toqan returned status=finished but answer is empty. "
                        f"attachments={attachments!r}. "
                        f"Tried /download_file and /find_conversation recovery. "
                        f"conversation_id={conversation_id}"
                    )

            # Prefer downloaded HTML attachment when it is longer / more complete
            if attachments:
                recovered = _recover_answer_from_attachments(
                    conversation_id, attachments, headers, base_url
                )
                if recovered and (
                    not _looks_like_html(answer) or len(recovered) > len(answer)
                ):
                    logging.warning(
                        f"[Poll {attempt}] Using /download_file content "
                        f"({len(recovered)} chars) over inline answer "
                        f"({len(answer)} chars)."
                    )
                    answer = recovered

            logging.info(
                f"[Poll {attempt}] Completed. Answer length: {len(answer)} chars. "
                f"Preview: {answer[:200]!r}"
            )
            return _validate_finished_answer(
                answer, attachments, conversation_id, require_html=require_html
            )

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
