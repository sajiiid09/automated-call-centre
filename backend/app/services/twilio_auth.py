"""X-Twilio-Signature validation.

Once PUBLIC_BASE_URL is live these webhooks are reachable by anyone who finds
the URL. Unauthenticated, they let a stranger drive campaign state through
/twilio/status, or open /twilio/media and run a voice pipeline on our
Deepgram and Gemini credit. Twilio signs every request; we verify it.

Skipped in simulated mode so the browser demo and the test suite are
unaffected.
"""

import base64
import hashlib
import hmac

from fastapi import HTTPException, Request, WebSocket

from app.config import settings
from app.services.telephony import dialing_mode


def expected_signature(url: str, params: dict[str, str]) -> str:
    """Twilio's scheme: the full URL with sorted POST params appended."""
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(settings.twilio_auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def _public_url(path: str, query: str) -> str:
    """Rebuild the URL Twilio signed — it signed its own, not ngrok's rewrite."""
    url = f"{settings.public_base_url.rstrip('/')}{path}"
    return f"{url}?{query}" if query else url


async def verify_twilio_request(request: Request) -> None:
    """FastAPI dependency for the HTTP webhooks."""
    if dialing_mode() != "twilio":
        return
    signature = request.headers.get("X-Twilio-Signature", "")
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    url = _public_url(request.url.path, request.url.query)
    if not hmac.compare_digest(signature, expected_signature(url, params)):
        raise HTTPException(403, "Invalid Twilio signature")


def verify_twilio_websocket(websocket: WebSocket) -> bool:
    """Media Streams sends no signature header, so authenticate the URL itself.

    The stream URL carries a token derived from the call id; only our own
    TwiML (built with the auth token) can produce a valid one.
    """
    if dialing_mode() != "twilio":
        return True
    call_id = websocket.query_params.get("call_id", "")
    token = websocket.query_params.get("token", "")
    return bool(token) and hmac.compare_digest(token, stream_token(call_id))


def stream_token(call_id: str) -> str:
    """Short HMAC binding a media-stream URL to one call."""
    digest = hmac.new(
        settings.twilio_auth_token.encode(), f"stream:{call_id}".encode(), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")[:32]
