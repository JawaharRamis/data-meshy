"""Tests for aws_client — session management, cross-account assume, events, pipelines."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from datameshy.lib.aws_client import (
    AuthError,
    PipelineError,
    assume_cross_account_role,
    get_session,
    put_mesh_event,
    start_pipeline,
    wait_pipeline,
)


# ---------------------------------------------------------------------------
# get_session
# ---------------------------------------------------------------------------


class TestGetSession:
    """Tests for get_session()."""

    @mock_aws
    def test_session_creation_with_credentials(self):
        """get_session should return a valid session when credentials work."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = get_session(profile=None, region="us-east-1")
        assert isinstance(session, boto3.Session)

        # Verify STS caller identity works
        sts = session.client("sts")
        identity = sts.get_caller_identity()
        assert "Account" in identity

    @mock_aws
    def test_session_with_profile(self):
        """get_session with profile=None should create a working session."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = get_session(profile=None, region="us-east-1")
        assert session is not None

    def test_session_missing_credentials(self):
        """get_session should raise when credentials are invalid."""
        # Use a mock to simulate NoCredentialsError
        from botocore.exceptions import NoCredentialsError

        with patch("datameshy.lib.aws_client.boto3.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.client.return_value.get_caller_identity.side_effect = NoCredentialsError()
            mock_session_cls.return_value = mock_session

            with pytest.raises(AuthError, match="No AWS credentials"):
                get_session(profile=None, region="us-east-1")


# ---------------------------------------------------------------------------
# assume_cross_account_role
# ---------------------------------------------------------------------------


class TestAssumeCrossAccountRole:
    """Tests for assume_cross_account_role()."""

    @mock_aws
    def test_assume_role_success(self):
        """Should return a new session with assumed role credentials."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        sts = session.client("sts")

        # Create the role to assume
        iam = session.client("iam")
        trust_policy = json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"AWS": sts.get_caller_identity()["Arn"]},
                "Action": "sts:AssumeRole",
            }],
        })
        iam.create_role(
            RoleName="CrossAccountRole",
            AssumeRolePolicyDocument=trust_policy,
            Path="/",
        )
        role_arn = "arn:aws:iam::123456789012:role/CrossAccountRole"

        new_session = assume_cross_account_role(session, role_arn, "test-session")
        assert isinstance(new_session, boto3.Session)
        assert new_session.region_name == "us-east-1"

    @mock_aws
    def test_assume_role_session_name_truncated(self):
        """Session name longer than 64 chars should be truncated."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        sts = session.client("sts")

        iam = session.client("iam")
        trust_policy = json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"AWS": sts.get_caller_identity()["Arn"]},
                "Action": "sts:AssumeRole",
            }],
        })
        iam.create_role(
            RoleName="TestRole",
            AssumeRolePolicyDocument=trust_policy,
            Path="/",
        )
        role_arn = "arn:aws:iam::123456789012:role/TestRole"

        long_name = "a" * 100
        new_session = assume_cross_account_role(session, role_arn, long_name)
        assert isinstance(new_session, boto3.Session)


# ---------------------------------------------------------------------------
# put_mesh_event
# ---------------------------------------------------------------------------


class TestPutMeshEvent:
    """Tests for put_mesh_event()."""

    @mock_aws
    def test_put_event_success(self):
        """Should publish an event to EventBridge and return event_id."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")

        events = session.client("events")
        events.create_event_bus(Name="mesh-event-bus")
        event_bus_arn = "arn:aws:events:us-east-1:123456789012:event-bus/mesh-event-bus"

        event_id = put_mesh_event(
            session=session,
            event_bus_arn=event_bus_arn,
            event_type="DomainOnboarded",
            payload={"domain": "sales", "account_id": "123456789012"},
        )

        assert isinstance(event_id, str)
        assert len(event_id) > 0

    @mock_aws
    def test_put_event_returns_uuid(self):
        """Returned event_id should be a valid UUID string."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        events = session.client("events")
        events.create_event_bus(Name="test-bus")
        bus_arn = "arn:aws:events:us-east-1:123456789012:event-bus/test-bus"

        event_id = put_mesh_event(
            session=session,
            event_bus_arn=bus_arn,
            event_type="TestEvent",
            payload={"key": "value"},
        )
        # Should be parseable as UUID
        import uuid
        uuid.UUID(event_id)  # Will raise if not a valid UUID


# ---------------------------------------------------------------------------
# start_pipeline
# ---------------------------------------------------------------------------


class TestStartPipeline:
    """Tests for start_pipeline()."""

    @mock_aws
    def test_start_pipeline_success(self):
        """Should start a Step Functions execution and return execution ARN."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")

        iam = session.client("iam")
        iam.create_role(
            RoleName="SFNRole",
            AssumeRolePolicyDocument=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "states.us-east-1.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }],
            }),
            Path="/",
        )

        sfn = session.client("stepfunctions")
        response = sfn.create_state_machine(
            name="test-pipeline",
            definition=json.dumps({
                "Comment": "Test",
                "StartAt": "Pass",
                "States": {"Pass": {"Type": "Pass", "End": True}},
            }),
            roleArn="arn:aws:iam::123456789012:role/SFNRole",
        )
        sm_arn = response["stateMachineArn"]

        execution_arn = start_pipeline(
            session=session,
            state_machine_arn=sm_arn,
            input_json={"domain": "sales", "product_name": "orders"},
        )

        assert isinstance(execution_arn, str)
        assert "execution" in execution_arn.lower()


# ---------------------------------------------------------------------------
# wait_pipeline
# ---------------------------------------------------------------------------


class TestWaitPipeline:
    """Tests for wait_pipeline()."""

    def test_wait_pipeline_succeeded(self):
        """Should return SUCCEEDED when the execution completes."""
        session = MagicMock()
        mock_sfn = MagicMock()
        session.client.return_value = mock_sfn
        mock_sfn.describe_execution.return_value = {"status": "SUCCEEDED"}

        status = wait_pipeline(session, "arn:fake:exec", timeout_seconds=30, poll_interval=0)
        assert status == "SUCCEEDED"

    @mock_aws
    def test_wait_pipeline_timeout(self):
        """Should raise PipelineError when timeout is exceeded."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")

        iam = session.client("iam")
        iam.create_role(
            RoleName="SFNRole",
            AssumeRolePolicyDocument=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "states.us-east-1.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }],
            }),
            Path="/",
        )

        sfn = session.client("stepfunctions")
        sm = sfn.create_state_machine(
            name="test-pipeline",
            definition=json.dumps({
                "Comment": "Test",
                "StartAt": "Pass",
                "States": {"Pass": {"Type": "Pass", "End": True}},
            }),
            roleArn="arn:aws:iam::123456789012:role/SFNRole",
        )
        sm_arn = sm["stateMachineArn"]

        exec_resp = sfn.start_execution(
            stateMachineArn=sm_arn,
            input=json.dumps({"test": True}),
        )
        exec_arn = exec_resp["executionArn"]

        # Use monotonic mock to simulate timeout
        with patch("datameshy.lib.aws_client.time.monotonic", side_effect=[0.0, 999.0]):
            with pytest.raises(PipelineError, match="timed out"):
                wait_pipeline(session, exec_arn, timeout_seconds=0, poll_interval=0)
