#!/usr/bin/env python3
"""Safely stop and remove the GuatePass demo stacks.

Default mode is a read-only plan. Destruction requires both --execute and an
exact --confirm value. Resource identifiers are collected before deletion so
retained resources can be cleaned and reported afterward.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError


NOT_FOUND = {
    "ResourceNotFoundException",
    "ResourceNotFound",
    "ValidationError",
}


def _is_missing(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") in NOT_FOUND


def _stack(cf: Any, name: str) -> dict[str, Any] | None:
    try:
        response = cf.describe_stacks(StackName=name)
    except ClientError as exc:
        if _is_missing(exc):
            return None
        raise

    stack = response["Stacks"][0]
    resources: list[dict[str, Any]] = []
    paginator = cf.get_paginator("list_stack_resources")
    for page in paginator.paginate(StackName=name):
        resources.extend(page.get("StackResourceSummaries", []))

    return {
        "name": name,
        "status": stack.get("StackStatus"),
        "outputs": {
            item["OutputKey"]: item["OutputValue"]
            for item in stack.get("Outputs", [])
        },
        "resources": resources,
    }


def _resource_ids(info: dict[str, Any], resource_type: str) -> list[str]:
    return [
        item["PhysicalResourceId"]
        for item in info.get("resources", [])
        if item.get("ResourceType") == resource_type
        and item.get("PhysicalResourceId")
    ]


def _print_plan(infos: list[dict[str, Any] | None]) -> None:
    plan = []
    for info in infos:
        if info is None:
            continue
        grouped: dict[str, list[str]] = {}
        for resource in info["resources"]:
            grouped.setdefault(resource.get("ResourceType", "UNKNOWN"), []).append(
                resource.get("PhysicalResourceId", "")
            )
        plan.append({
            "stack": info["name"],
            "status": info["status"],
            "outputs": sorted(info["outputs"]),
            "resources": {
                key: sorted(value) for key, value in sorted(grouped.items())
            },
        })
    print(json.dumps({"mode": "plan", "stacks": plan}, indent=2))


def _disable_rules(session: boto3.Session, info: dict[str, Any], actions: list[dict[str, Any]]) -> None:
    events = session.client("events")
    for physical_id in _resource_ids(info, "AWS::Events::Rule"):
        event_bus, rule = _rule_identity(physical_id)
        try:
            kwargs = {"Name": rule}
            if event_bus:
                kwargs["EventBusName"] = event_bus
            state = events.describe_rule(**kwargs).get("State")
            if state and state != "DISABLED":
                events.disable_rule(**kwargs)
                actions.append({"action": "disable_event_rule", "resource": physical_id})
        except ClientError as exc:
            if not _is_missing(exc):
                raise


def _rule_identity(physical_id: str) -> tuple[str | None, str]:
    if "|" in physical_id:
        event_bus, name = physical_id.split("|", 1)
        return event_bus, name
    return None, physical_id


def _schedule_identity(physical_id: str) -> tuple[str, str]:
    if physical_id.startswith("arn:"):
        name = physical_id.rsplit("/", 1)[-1]
        group = physical_id.split("/", 1)[1].rsplit("/", 1)[0]
        return name, group or "default"
    return physical_id, "default"


def _disable_schedules(session: boto3.Session, info: dict[str, Any], actions: list[dict[str, Any]]) -> None:
    scheduler = session.client("scheduler")
    for physical_id in _resource_ids(info, "AWS::Scheduler::Schedule"):
        name, group = _schedule_identity(physical_id)
        try:
            current = scheduler.get_schedule(Name=name, GroupName=group)
            if current.get("State") == "DISABLED":
                continue
            fields = {
                key: current[key]
                for key in (
                    "ScheduleExpression",
                    "FlexibleTimeWindow",
                    "Target",
                )
                if key in current
            }
            for key in ("StartDate", "EndDate", "Description", "ScheduleExpressionTimezone", "KmsKeyArn"):
                if key in current:
                    fields[key] = current[key]
            scheduler.update_schedule(
                Name=name,
                GroupName=group,
                State="DISABLED",
                **fields,
            )
            actions.append({"action": "disable_schedule", "resource": physical_id})
        except ClientError as exc:
            if not _is_missing(exc):
                raise


def _disable_event_sources(session: boto3.Session, info: dict[str, Any], actions: list[dict[str, Any]]) -> None:
    lambdas = session.client("lambda")
    for uuid in _resource_ids(info, "AWS::Lambda::EventSourceMapping"):
        try:
            mapping = lambdas.get_event_source_mapping(UUID=uuid)
            if mapping.get("State") != "Disabled":
                lambdas.update_event_source_mapping(UUID=uuid, Enabled=False)
                actions.append({"action": "disable_event_source_mapping", "resource": uuid})
        except ClientError as exc:
            if not _is_missing(exc):
                raise


def _stop_waits(session: boto3.Session, info: dict[str, Any], actions: list[dict[str, Any]]) -> None:
    states = session.client("stepfunctions")
    state_machine_types = {
        "AWS::StepFunctions::StateMachine",
        "AWS::Serverless::StateMachine",
    }
    for machine in [
        item["PhysicalResourceId"]
        for item in info.get("resources", [])
        if item.get("ResourceType") in state_machine_types
        and item.get("PhysicalResourceId")
    ]:
        token = None
        while True:
            kwargs = {"stateMachineArn": machine, "statusFilter": "RUNNING"}
            if token:
                kwargs["nextToken"] = token
            page = states.list_executions(**kwargs)
            for execution in page.get("executions", []):
                states.stop_execution(
                    executionArn=execution["executionArn"],
                    cause="GuatePass cleanup before stack deletion",
                )
                actions.append({
                    "action": "stop_execution",
                    "resource": execution["executionArn"],
                })
            token = page.get("nextToken")
            if not token:
                break


def _disable_distribution(session: boto3.Session, distribution_id: str, actions: list[dict[str, Any]]) -> None:
    cloudfront = session.client("cloudfront")
    try:
        current = cloudfront.get_distribution_config(Id=distribution_id)
    except ClientError as exc:
        if _is_missing(exc):
            return
        raise

    config = current["DistributionConfig"]
    if config.get("Enabled"):
        config["Enabled"] = False
        cloudfront.update_distribution(
            Id=distribution_id,
            IfMatch=current["ETag"],
            DistributionConfig=config,
        )
        actions.append({"action": "disable_cloudfront", "resource": distribution_id})

    cloudfront.get_waiter("distribution_deployed").wait(
        DistributionId=distribution_id,
        WaiterConfig={"Delay": 30, "MaxAttempts": 40},
    )


def _empty_bucket(s3: Any, bucket: str, actions: list[dict[str, Any]]) -> None:
    key_marker = None
    version_id_marker = None
    deleted = 0
    while True:
        kwargs = {"Bucket": bucket}
        if key_marker:
            kwargs["KeyMarker"] = key_marker
        if version_id_marker:
            kwargs["VersionIdMarker"] = version_id_marker
        try:
            page = s3.list_object_versions(**kwargs)
        except ClientError as exc:
            if _is_missing(exc):
                return
            raise

        objects = [
            {"Key": item["Key"], "VersionId": item["VersionId"]}
            for item in page.get("Versions", []) + page.get("DeleteMarkers", [])
        ]
        if objects:
            for offset in range(0, len(objects), 1000):
                batch = objects[offset : offset + 1000]
                s3.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": batch, "Quiet": True},
                )
                deleted += len(batch)

        if not page.get("IsTruncated"):
            break
        key_marker = page.get("NextKeyMarker")
        version_id_marker = page.get("NextVersionIdMarker")

    actions.append({"action": "empty_bucket", "resource": bucket, "objects": deleted})


def _delete_stack(cf: Any, name: str) -> None:
    try:
        cf.delete_stack(StackName=name)
    except ClientError as exc:
        if _is_missing(exc):
            return
        raise
    print(json.dumps({"action": "delete_stack_started", "stack": name}))
    cf.get_waiter("stack_delete_complete").wait(
        StackName=name,
        WaiterConfig={"Delay": 15, "MaxAttempts": 240},
    )
    print(json.dumps({"action": "delete_stack_complete", "stack": name}))


def _delete_parameter(ssm: Any, name: str, actions: list[dict[str, Any]]) -> None:
    try:
        ssm.delete_parameter(Name=name)
        actions.append({"action": "delete_parameter", "resource": name})
    except ClientError as exc:
        if not _is_missing(exc):
            raise


def _schedule_secret_deletion(secrets: Any, arn: str, actions: list[dict[str, Any]]) -> None:
    try:
        current = secrets.describe_secret(SecretId=arn)
        if current.get("DeletedDate"):
            actions.append({"action": "secret_already_scheduled", "resource": arn})
            return
        secrets.delete_secret(SecretId=arn, RecoveryWindowInDays=7)
        actions.append({
            "action": "schedule_secret_deletion",
            "resource": arn,
            "recovery_days": 7,
        })
    except ClientError as exc:
        if not _is_missing(exc):
            raise


def _cleanup_retained(session: boto3.Session, infos: list[dict[str, Any]], actions: list[dict[str, Any]]) -> None:
    s3 = session.client("s3")
    ssm = session.client("ssm")
    secrets = session.client("secretsmanager")
    logs = session.client("logs")
    dynamodb = session.client("dynamodb")
    sqs = session.client("sqs")
    cognito = session.client("cognito-idp")
    events = session.client("events")
    apigateway = session.client("apigateway")
    appsync = session.client("appsync")
    cloudfront = session.client("cloudfront")

    for info in infos:
        for bucket in _resource_ids(info, "AWS::S3::Bucket"):
            _empty_bucket(s3, bucket, actions)
            try:
                s3.delete_bucket(Bucket=bucket)
                actions.append({"action": "delete_bucket", "resource": bucket})
            except ClientError as exc:
                if not _is_missing(exc):
                    raise

        parameters = [
            value
            for key, value in info["outputs"].items()
            if key.endswith("ParameterName")
        ]
        for parameter in parameters:
            _delete_parameter(ssm, parameter, actions)

        secret_arns = [
            value
            for key, value in info["outputs"].items()
            if key.endswith("SecretArn")
        ]
        for arn in secret_arns:
            _schedule_secret_deletion(secrets, arn, actions)

        for archive in _resource_ids(info, "AWS::Events::Archive"):
            try:
                events.delete_archive(ArchiveName=archive)
                actions.append({"action": "delete_archive", "resource": archive})
            except ClientError as exc:
                if not _is_missing(exc):
                    raise

        for log_group in _resource_ids(info, "AWS::Logs::LogGroup"):
            try:
                logs.delete_log_group(logGroupName=log_group)
                actions.append({"action": "delete_log_group", "resource": log_group})
            except ClientError as exc:
                if not _is_missing(exc):
                    raise

        for table in _resource_ids(info, "AWS::DynamoDB::Table"):
            try:
                dynamodb.delete_table(TableName=table)
                actions.append({"action": "delete_table", "resource": table})
            except ClientError as exc:
                if not _is_missing(exc):
                    raise

        for pool in _resource_ids(info, "AWS::Cognito::UserPool"):
            try:
                cognito.delete_user_pool(UserPoolId=pool)
                actions.append({"action": "delete_user_pool", "resource": pool})
            except ClientError as exc:
                if not _is_missing(exc):
                    raise

        for api_id in _resource_ids(info, "AWS::ApiGateway::RestApi"):
            try:
                apigateway.delete_rest_api(restApiId=api_id)
                actions.append({"action": "delete_rest_api", "resource": api_id})
            except ClientError as exc:
                if not _is_missing(exc):
                    raise

        for api_id in (
            _resource_ids(info, "AWS::AppSync::GraphQLApi")
            + _resource_ids(info, "AWS::AppSync::Api")
        ):
            actual_id = api_id.rsplit("/apis/", 1)[-1]
            try:
                appsync.delete_graphql_api(apiId=actual_id)
                actions.append({"action": "delete_appsync_api", "resource": api_id})
            except ClientError as exc:
                if not _is_missing(exc):
                    raise

        for namespace in _resource_ids(info, "AWS::AppSync::ChannelNamespace"):
            try:
                prefix, name = namespace.rsplit("/channelNamespace/", 1)
                actual_id = prefix.rsplit("/apis/", 1)[-1]
                appsync.delete_channel_namespace(apiId=actual_id, name=name)
                actions.append({"action": "delete_appsync_namespace", "resource": namespace})
            except ClientError as exc:
                if not _is_missing(exc):
                    raise

        for distribution in _resource_ids(info, "AWS::CloudFront::Distribution"):
            _disable_distribution(session, distribution, actions)
            try:
                current = cloudfront.get_distribution_config(Id=distribution)
                cloudfront.delete_distribution(Id=distribution, IfMatch=current["ETag"])
                actions.append({"action": "delete_cloudfront", "resource": distribution})
            except ClientError as exc:
                if not _is_missing(exc):
                    raise

        for physical_id in _resource_ids(info, "AWS::Events::Rule"):
            event_bus, rule = _rule_identity(physical_id)
            try:
                kwargs = {"Name": rule, "Force": True}
                if event_bus:
                    kwargs["EventBusName"] = event_bus
                events.delete_rule(**kwargs)
                actions.append({"action": "delete_event_rule", "resource": physical_id})
            except ClientError as exc:
                if not _is_missing(exc):
                    raise

        for queue in _resource_ids(info, "AWS::SQS::Queue"):
            try:
                sqs.delete_queue(QueueUrl=queue)
                actions.append({"action": "delete_queue", "resource": queue})
            except ClientError as exc:
                if not _is_missing(exc):
                    raise

        for event_bus in _resource_ids(info, "AWS::Events::EventBus"):
            try:
                events.delete_event_bus(Name=event_bus)
                actions.append({"action": "delete_event_bus", "resource": event_bus})
            except ClientError as exc:
                if not _is_missing(exc):
                    raise

        for machine in (
            _resource_ids(info, "AWS::StepFunctions::StateMachine")
            + _resource_ids(info, "AWS::Serverless::StateMachine")
        ):
            try:
                session.client("stepfunctions").delete_state_machine(
                    stateMachineArn=machine
                )
                actions.append({"action": "delete_state_machine", "resource": machine})
            except ClientError as exc:
                if not _is_missing(exc):
                    raise

        scheduler = session.client("scheduler")
        for physical_id in _resource_ids(info, "AWS::Scheduler::Schedule"):
            name, group = _schedule_identity(physical_id)
            try:
                scheduler.delete_schedule(Name=name, GroupName=group)
                actions.append({"action": "delete_schedule", "resource": physical_id})
            except ClientError as exc:
                if not _is_missing(exc):
                    raise

        for mapping in _resource_ids(info, "AWS::Lambda::EventSourceMapping"):
            try:
                session.client("lambda").delete_event_source_mapping(UUID=mapping)
                actions.append({"action": "delete_event_source_mapping", "resource": mapping})
            except ClientError as exc:
                if not _is_missing(exc):
                    raise

        for function in _resource_ids(info, "AWS::Lambda::Function"):
            try:
                session.client("lambda").delete_function(FunctionName=function)
                actions.append({"action": "delete_lambda", "resource": function})
            except ClientError as exc:
                if not _is_missing(exc):
                    raise

        for alarm in _resource_ids(info, "AWS::CloudWatch::Alarm"):
            try:
                session.client("cloudwatch").delete_alarms(AlarmNames=[alarm])
                actions.append({"action": "delete_alarm", "resource": alarm})
            except ClientError as exc:
                if not _is_missing(exc):
                    raise

        for dashboard in _resource_ids(info, "AWS::CloudWatch::Dashboard"):
            try:
                session.client("cloudwatch").delete_dashboards(DashboardNames=[dashboard])
                actions.append({"action": "delete_dashboard", "resource": dashboard})
            except ClientError as exc:
                if not _is_missing(exc):
                    raise


def _verify_stacks(cf: Any, names: list[str]) -> list[str]:
    remaining = []
    for name in names:
        if _stack(cf, name) is not None:
            remaining.append(name)
    return remaining


def _verify_outputs(
    session: boto3.Session,
    infos: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], list[str]]:
    """Probe important physical resources after CloudFormation deletion."""
    clients = {
        "dynamodb": session.client("dynamodb"),
        "sqs": session.client("sqs"),
        "ssm": session.client("ssm"),
        "secretsmanager": session.client("secretsmanager"),
        "s3": session.client("s3"),
        "cloudfront": session.client("cloudfront"),
        "cognito": session.client("cognito-idp"),
        "apigateway": session.client("apigateway"),
        "appsync": session.client("appsync"),
        "events": session.client("events"),
        "scheduler": session.client("scheduler"),
        "states": session.client("stepfunctions"),
        "lambda": session.client("lambda"),
        "logs": session.client("logs"),
    }
    remaining: list[dict[str, str]] = []
    scheduled_secrets: list[str] = []

    def probe(kind: str, identifier: str, call: Any) -> None:
        try:
            call()
        except ClientError as exc:
            if not _is_missing(exc):
                raise
            return
        remaining.append({"kind": kind, "resource": identifier})

    for info in infos:
        for key, value in info["outputs"].items():
            if key.endswith("TableName"):
                probe(
                    "dynamodb_table",
                    value,
                    lambda value=value: clients["dynamodb"].describe_table(TableName=value),
                )
            elif key.endswith("QueueUrl"):
                probe(
                    "sqs_queue",
                    value,
                    lambda value=value: clients["sqs"].get_queue_attributes(
                        QueueUrl=value,
                        AttributeNames=["QueueArn"],
                    ),
                )
            elif key.endswith("ParameterName"):
                probe(
                    "ssm_parameter",
                    value,
                    lambda value=value: clients["ssm"].get_parameter(Name=value),
                )
            elif key.endswith("SecretArn"):
                try:
                    secret = clients["secretsmanager"].describe_secret(SecretId=value)
                except ClientError as exc:
                    if not _is_missing(exc):
                        raise
                    continue
                if secret.get("DeletedDate"):
                    scheduled_secrets.append(value)
                else:
                    remaining.append({"kind": "secret", "resource": value})
            elif key == "PortalBucketName":
                probe(
                    "s3_bucket",
                    value,
                    lambda value=value: clients["s3"].head_bucket(Bucket=value),
                )
            elif key == "PortalDistributionId":
                probe(
                    "cloudfront_distribution",
                    value,
                    lambda value=value: clients["cloudfront"].get_distribution(Id=value),
                )
            elif key == "UserPoolId":
                probe(
                    "cognito_user_pool",
                    value,
                    lambda value=value: clients["cognito"].describe_user_pool(UserPoolId=value),
                )
            elif key == "RestApiId":
                probe(
                    "api_gateway",
                    value,
                    lambda value=value: clients["apigateway"].get_rest_api(restApiId=value),
                )
            elif key == "RealtimeApiId":
                probe(
                    "appsync_api",
                    value,
                    lambda value=value: clients["appsync"].get_api(apiId=value),
                )
            elif key.endswith("ArchiveArn"):
                probe(
                    "eventbridge_archive",
                    value,
                    lambda value=value: clients["events"].describe_archive(ArchiveName=value),
                )
            elif key.endswith("ScheduleArn"):
                name, group = _schedule_identity(value)
                probe(
                    "scheduler_schedule",
                    value,
                    lambda name=name, group=group: clients["scheduler"].get_schedule(
                        Name=name,
                        GroupName=group,
                    ),
                )
            elif key.endswith("FunctionName"):
                probe(
                    "lambda_function",
                    value,
                    lambda value=value: clients["lambda"].get_function(FunctionName=value),
                )
            elif key.endswith("Arn") and ":stateMachine:" in value:
                probe(
                    "state_machine",
                    value,
                    lambda value=value: clients["states"].describe_state_machine(
                        stateMachineArn=value,
                    ),
                )

        for resource in info["resources"]:
            if resource.get("ResourceType") == "AWS::Logs::LogGroup":
                name = resource.get("PhysicalResourceId")
                if name:
                    probe(
                        "log_group",
                        name,
                        lambda name=name: clients["logs"].describe_log_groups(
                            logGroupNamePrefix=name,
                        ),
                    )

    return remaining, scheduled_secrets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack", default="guatepass-dev")
    parser.add_argument("--external-stack", default="guatepass-registro-dev")
    parser.add_argument(
        "--region",
        default=os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or "us-east-1",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Ejecuta la limpieza; sin esta opción solo muestra el plan.",
    )
    parser.add_argument(
        "--confirm",
        help="Debe ser exactamente: DELETE <stack> <external-stack>",
    )
    args = parser.parse_args()
    session = boto3.Session(region_name=args.region)
    cf = session.client("cloudformation")
    names = [args.stack, args.external_stack]
    infos = [_stack(cf, name) for name in names]

    if not args.execute:
        _print_plan(infos)
        return 0

    expected = f"DELETE {args.stack} {args.external_stack}"
    if args.confirm != expected:
        raise SystemExit(
            "Confirmación inválida. Usa exactamente: " + expected
        )

    existing = [info for info in infos if info is not None]
    actions: list[dict[str, Any]] = []
    started = time.monotonic()

    for info in existing:
        _disable_rules(session, info, actions)
        _disable_schedules(session, info, actions)
        _disable_event_sources(session, info, actions)
        _stop_waits(session, info, actions)
        for distribution in _resource_ids(info, "AWS::CloudFront::Distribution"):
            _disable_distribution(session, distribution, actions)
        for bucket in _resource_ids(info, "AWS::S3::Bucket"):
            _empty_bucket(session.client("s3"), bucket, actions)

    # The principal references the external stack's secret and API. Remove it
    # first, then remove the external registry stack.
    _delete_stack(cf, args.stack)
    _delete_stack(cf, args.external_stack)
    _cleanup_retained(session, existing, actions)

    remaining = _verify_stacks(cf, names)
    remaining_resources, scheduled_secrets = _verify_outputs(session, existing)
    result = {
        "mode": "execute",
        "stacks": names,
        "remaining_stacks": remaining,
        "remaining_resources": remaining_resources,
        "secrets_scheduled_for_deletion": scheduled_secrets,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "actions": actions,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 2 if remaining or remaining_resources else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ClientError as exc:
        print(json.dumps({
            "error": exc.response.get("Error", {}).get("Code", "ClientError"),
            "message": exc.response.get("Error", {}).get("Message", str(exc)),
        }), file=sys.stderr)
        sys.exit(1)
