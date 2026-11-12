#!/usr/bin/env python3

import argparse
import json
import os

import boto3


def stack_outputs(cloudformation, stack_name):
    response = cloudformation.describe_stacks(StackName=stack_name)
    return {
        item["OutputKey"]: item["OutputValue"]
        for item in response["Stacks"][0].get("Outputs", [])
    }


def main():
    parser = argparse.ArgumentParser(
        description="Mueve mensajes de una DLQ hacia su cola original."
    )
    parser.add_argument("--stack", required=True)
    parser.add_argument("--queue", choices=["registro"], required=True)
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1",
    )
    parser.add_argument("--rate", type=int, default=10)
    args = parser.parse_args()

    session = boto3.Session(region_name=args.region)
    cloudformation = session.client("cloudformation")
    sqs = session.client("sqs")

    outputs = stack_outputs(cloudformation, args.stack)

    queue_url = outputs["RegistroQueueUrl"]
    dlq_url = outputs["RegistroDLQUrl"]

    queue_attributes = sqs.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["QueueArn"],
    )["Attributes"]

    dlq_attributes = sqs.get_queue_attributes(
        QueueUrl=dlq_url,
        AttributeNames=[
            "QueueArn",
            "ApproximateNumberOfMessages",
        ],
    )["Attributes"]

    available = int(
        dlq_attributes.get("ApproximateNumberOfMessages", "0")
    )

    if available == 0:
        print(json.dumps({
            "stack": args.stack,
            "queue": args.queue,
            "moved": 0,
            "message": "La DLQ no contiene mensajes disponibles.",
        }))
        return

    task = sqs.start_message_move_task(
        SourceArn=dlq_attributes["QueueArn"],
        DestinationArn=queue_attributes["QueueArn"],
        MaxNumberOfMessagesPerSecond=args.rate,
    )

    print(json.dumps({
        "stack": args.stack,
        "queue": args.queue,
        "source_dlq": dlq_url,
        "destination_queue": queue_url,
        "messages_available_before_move": available,
        "task_handle": task.get("TaskHandle"),
        "max_number_of_messages_per_second": args.rate,
    }))


if __name__ == "__main__":
    main()
