from __future__ import annotations

import dataclasses

import httpx


@dataclasses.dataclass(frozen=True)
class TokenResponse:
    access_token: str
    token_type: str
    expires_in: int


class Reson8Auth:
    """Handles OAuth2 client_credentials token exchange for client-side use.

    For server-side use, pass the API key directly via Authorization: ApiKey <key>.
    """

    def __init__(self, api_url: str, client_id: str, client_secret: str) -> None:
        self._api_url = api_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret

    async def request_token(self) -> TokenResponse:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._api_url}/v1/auth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()

        body = resp.json()
        return TokenResponse(
            access_token=body["access_token"],
            token_type=body["token_type"],
            expires_in=body["expires_in"],
        )
