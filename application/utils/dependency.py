import base64
from typing import Optional

import jwt
from fastapi import Header, HTTPException, status
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError

from application.config.config import Config
from application.utils.logger import log


def verify_token(authorization: Optional[str] = Header(None)) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header is missing",
        )

    try:
        token = authorization.replace("Bearer ", "", 1)

        public_key = base64.b64decode(Config.PUBLIC_KEY)

        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=Config.HOST_NAME,
            issuer=Config.SECURITY_HOST,
        )

        log.info("Authentication successful")
        return payload["sub"]

    except ExpiredSignatureError:
        log.error("Token has expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired"
        )
    except InvalidTokenError as e:
        log.error(f"Invalid token: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )
    except Exception as e:
        log.error(f"Unexpected error during token verification: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred",
        )
