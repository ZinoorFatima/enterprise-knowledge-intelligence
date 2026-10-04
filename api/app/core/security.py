"""Service-token verification.

The browser never reaches this service. Next.js verifies the user's session,
mints a short-lived HS256 token carrying {sub, org, role}, and forwards it.

Two rules make that safe:
  * Only the verified `sub` claim identifies the user. A client-supplied user id
    is never trusted, anywhere.
  * The `org` claim is a HINT that is re-validated against auth.memberships on
    every request. JWTs are stale by design -- a user removed from an org keeps
    a valid token for up to the refresh window -- and one indexed lookup closes
    that revocation gap.
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg
import jwt
from fastapi import Depends, Header, HTTPException, Request, status

from app.config import settings


@dataclass(frozen=True)
class Principal:
    user_id: str
    org_id: str
    role: str

    def require_role(self, *allowed: str) -> None:
        if self.role not in allowed:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail=f"role '{self.role}' may not perform this action",
            )


def decode_service_token(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            settings.rag_service_secret,
            algorithms=["HS256"],
            audience=settings.service_token_audience,
            issuer=settings.service_token_issuer,
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="service token expired")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=f"invalid service token: {exc}")


async def current_principal(
    request: Request,
    authorization: str = Header(default=""),
) -> Principal:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    claims = decode_service_token(authorization.split(" ", 1)[1].strip())

    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="token has no subject")

    claimed_org = claims.get("org")
    pool: asyncpg.Pool = request.app.state.pool

    # Re-validate membership rather than trusting the token's org claim.
    row = await pool.fetchrow(
        """
        SELECT m.org_id::text AS org_id, m.role::text AS role
        FROM auth.memberships m
        WHERE m.user_id = $1::uuid
          AND ($2::uuid IS NULL OR m.org_id = $2::uuid)
        ORDER BY (m.org_id = $2::uuid) DESC
        LIMIT 1
        """,
        sub,
        claimed_org,
    )
    if row is None:
        # Either the user was removed from the org, or the token names an org
        # they never belonged to. Both are 403, and neither leaks which.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="no active membership for this organization"
        )

    return Principal(user_id=str(sub), org_id=row["org_id"], role=row["role"])


PrincipalDep = Depends(current_principal)
