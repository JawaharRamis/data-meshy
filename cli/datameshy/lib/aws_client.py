"""AWS client utilities for Data Meshy CLI.

Provides SSO session creation, cross-account role assumption, EventBridge
publishing, and Step Functions pipeline execution with Rich progress display.

Security note: Credentials are handled via boto3 sessions only. They are
never written to files or logged.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError, NoCredentialsError


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AuthError(Exception):
    """Raised when AWS credentials are missing or expired."""


class PipelineError(Exception):
    """Raised when a Step Functions pipeline execution fails."""

    def __init__(self, message: str, status: str = "", cause: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.cause = cause


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def get_session(profile: str | None, region: str) -> boto3.Session:
    """Create a boto3 session and validate credentials via STS GetCallerIdentity.

    Args:
        profile: AWS named profile (SSO or static). None uses default credential chain.
        region: AWS region name.

    Returns:
        Validated boto3.Session.

    Raises:
        AuthError: If credentials are missing, expired, or invalid.
    """
    try:
        session_kwargs: dict[str, Any] = {"region_name": region}
        if profile:
            session_kwargs["profile_name"] = profile

        session = boto3.Session(**session_kwargs)
        sts = session.client("sts")
        sts.get_caller_identity()
        return session
    except NoCredentialsError as exc:
        raise AuthError(
            "No AWS credentials found. Run `aws sso login` or configure credentials."
        ) from exc
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("ExpiredTokenException", "InvalidClientTokenId", "AuthFailure"):
            raise AuthError(
                f"AWS credentials expired or invalid ({code}). "
                "Run `aws sso login --profile <profile>` to refresh."
            ) from exc
        raise


def assume_cross_account_role(
    session: boto3.Session,
    role_arn: str,
    session_name: str,
) -> boto3.Session:
    """Assume a cross-account IAM role and return a new boto3 session.

    Args:
        session: Source boto3 session with permission to call AssumeRole.
        role_arn: ARN of the role to assume.
        session_name: Session name for audit trails (max 64 chars).

    Returns:
        New boto3 session using the assumed role credentials.

    Raises:
        ClientError: If the assume-role call fails.
    """
    sts = session.client("sts")
    response = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=session_name[:64],
    )
    creds = response["Credentials"]
    return boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=session.region_name,
    )


# ---------------------------------------------------------------------------
# EventBridge
# ---------------------------------------------------------------------------

_EVENT_SOURCE = "datameshy"
_EVENT_VERSION = "1.0"


def put_mesh_event(
    session: boto3.Session,
    event_bus_arn: str,
    event_type: str,
    payload: dict[str, Any],
) -> str:
    """Publish a mesh event to EventBridge with a standard envelope.

    The envelope adds:
    - event_id: UUID v4
    - timestamp: ISO-8601 UTC
    - version: "1.0"
    - source: "datameshy"

    Args:
        session: Authenticated boto3 session.
        event_bus_arn: ARN of the target EventBridge event bus.
        event_type: Event detail type (e.g. "DomainOnboarded", "ProductCreated").
        payload: Domain-specific event payload dict.

    Returns:
        The event_id UUID string.

    Raises:
        ClientError: On EventBridge API errors.
    """
    import json

    event_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()

    envelope: dict[str, Any] = {
        "event_id": event_id,
        "timestamp": timestamp,
        "version": _EVENT_VERSION,
        "source": _EVENT_SOURCE,
        "event_type": event_type,
        **payload,
    }

    events_client = session.client("events")
    events_client.put_events(
        Entries=[
            {
                "EventBusName": event_bus_arn,
                "Source": _EVENT_SOURCE,
                "DetailType": event_type,
                "Detail": json.dumps(envelope),
            }
        ]
    )
    return event_id


# ---------------------------------------------------------------------------
# Step Functions
# ---------------------------------------------------------------------------

def start_pipeline(
    session: boto3.Session,
    state_machine_arn: str,
    input_json: dict[str, Any],
) -> str:
    """Start a Step Functions state machine execution.

    Args:
        session: Authenticated boto3 session.
        state_machine_arn: ARN of the state machine.
        input_json: Input payload dict (will be JSON-serialised).

    Returns:
        Execution ARN string.
    """
    import json

    sf_client = session.client("stepfunctions")
    execution_name = f"datameshy-{uuid.uuid4().hex[:12]}"
    response = sf_client.start_execution(
        stateMachineArn=state_machine_arn,
        name=execution_name,
        input=json.dumps(input_json),
    )
    return response["executionArn"]


def wait_pipeline(
    session: boto3.Session,
    execution_arn: str,
    timeout_seconds: int = 7200,
    poll_interval: int = 10,
) -> str:
    """Poll a Step Functions execution until it reaches a terminal state.

    Shows a Rich spinner with the current status while polling.

    Args:
        session: Authenticated boto3 session.
        execution_arn: ARN of the execution to monitor.
        timeout_seconds: Maximum time to wait (default 2 hours).
        poll_interval: Seconds between polls (default 10).

    Returns:
        Final execution status string (e.g. "SUCCEEDED", "FAILED").

    Raises:
        PipelineError: If the execution fails or times out.
        ClientError: On unexpected AWS errors.
    """
    from rich.console import Console
    from rich.spinner import Spinner

    console = Console()
    sf_client = session.client("stepfunctions")
    terminal_states = {"SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"}
    start = time.monotonic()

    with console.status("[bold cyan]Waiting for pipeline...", spinner="dots") as status:
        while True:
            elapsed = time.monotonic() - start
            if elapsed > timeout_seconds:
                raise PipelineError(
                    f"Pipeline timed out after {timeout_seconds}s",
                    status="TIMED_OUT",
                )

            response = sf_client.describe_execution(executionArn=execution_arn)
            current_status = response["status"]

            status.update(
                f"[bold cyan]Pipeline status:[/bold cyan] [yellow]{current_status}[/yellow] "
                f"[dim]({int(elapsed)}s elapsed)[/dim]"
            )

            if current_status in terminal_states:
                if current_status != "SUCCEEDED":
                    cause = response.get("cause", "")
                    raise PipelineError(
                        f"Pipeline ended with status {current_status}",
                        status=current_status,
                        cause=cause,
                    )
                return current_status

            time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# SigV4-signed HTTP requests (API Gateway)
# ---------------------------------------------------------------------------

class APIError(Exception):
    """Raised when an API Gateway request returns a non-2xx status."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


def make_signed_request(
    session: boto3.Session,
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Make a SigV4-signed HTTP request to an API Gateway endpoint.

    Resolves credentials from the given boto3 session and signs the request
    using AWS Signature Version 4 via ``botocore``.

    Args:
        session: Authenticated boto3 session (provides credentials and region).
        method: HTTP method (``"GET"``, ``"POST"``, etc.).
        url: Full URL of the API Gateway endpoint.
        body: Optional JSON body dict (serialised to bytes; only for POST/PUT).
        params: Optional query-string parameters dict.

    Returns:
        Parsed JSON response body as a dict.

    Raises:
        APIError: If the HTTP response status code is not 2xx.
        AuthError: If the session has no valid credentials to sign the request.
    """
    import json as _json
    from urllib.parse import urlparse, urlencode, urlunparse

    import requests
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import RefreshableCredentials
    from botocore.exceptions import NoCredentialsError as BotocoreNoCreds

    # Build URL with query parameters
    if params:
        parsed = urlparse(url)
        query = urlencode({k: v for k, v in params.items() if v is not None})
        url = urlunparse(parsed._replace(query=query))

    body_bytes = _json.dumps(body).encode() if body else b""

    # Build a botocore AWSRequest for signing
    aws_request = AWSRequest(
        method=method.upper(),
        url=url,
        data=body_bytes,
        headers={"Content-Type": "application/json"} if body_bytes else {},
    )

    # Resolve credentials from the session
    try:
        credentials = session.get_credentials()
        if credentials is None:
            raise AuthError("No AWS credentials found in the current session.")
        credentials = credentials.get_frozen_credentials()
    except BotocoreNoCreds as exc:
        raise AuthError("No AWS credentials found. Run `aws sso login`.") from exc

    region = session.region_name or "us-east-1"
    SigV4Auth(credentials, "execute-api", region).add_auth(aws_request)

    # Translate botocore AWSRequest headers → requests library dict
    prepared_headers = dict(aws_request.headers)

    response = requests.request(
        method=method.upper(),
        url=url,
        headers=prepared_headers,
        data=body_bytes if body_bytes else None,
        timeout=30,
    )

    if not response.ok:
        raise APIError(
            f"API request failed: {response.status_code} {response.text}",
            status_code=response.status_code,
        )

    if response.content:
        return response.json()
    return {}
