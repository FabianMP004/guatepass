"""Bind existing demo drivers to real integrantes. Never creates users or resets money.

Preview by default. --apply performs the shown assignments. --integrante supplies
the owner only for drivers without an explicit integrantes.csv row.
"""
import argparse
import csv
import os
import re
from pathlib import Path
import boto3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stack', default='guatepass-dev')
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('--integrantes', default='seeds/guatepass/integrantes.csv')
    parser.add_argument('--clientes', default='seeds/guatepass/clientes.csv')
    parser.add_argument('--integrante', help='Real integrante for unassigned driver accounts')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    path = Path(args.integrantes)
    with path.open(encoding='utf-8-sig', newline='') as file:
        rows = list(csv.DictReader(file))
    if not rows or any(not all(row.get(k) for k in ('integrante', 'carnet', 'username')) for row in rows):
        raise ValueError('integrantes.csv incompleto')
    assignments = {r['username']: r for r in rows}
    if len(assignments) != len(rows):
        raise ValueError('Usernames duplicados en integrantes.csv')
    fallback = [r for r in rows if r['integrante'].casefold() == (args.integrante or '').casefold()]
    if args.integrante and (not fallback or len({r['carnet'] for r in fallback}) != 1):
        raise ValueError('El integrante debe existir con un único carné real')
    session = boto3.Session(region_name=args.region)
    stack = session.client('cloudformation').describe_stacks(StackName=args.stack)['Stacks'][0]
    params = {p['ParameterKey']: p['ParameterValue'] for p in stack['Parameters']}
    if params.get('Environment') not in ('dev', 'demo'):
        raise ValueError('Solo dev/demo')
    outputs = {o['OutputKey']: o['OutputValue'] for o in stack['Outputs']}
    users = session.resource('dynamodb').Table(outputs['UsuariosTableName'])
    cognito = session.client('cognito-idp')
    plan = []
    with open(args.clientes, encoding='utf-8-sig', newline='') as file:
        clients = list(csv.DictReader(file))
    for customer in clients:
        placa = customer['placa']
        username = 'conductor_' + re.sub(r'[^A-Za-z0-9_.-]+', '-', placa.strip()).strip('-')
        assignment = assignments.get(username)
        if assignment is None:
            if not fallback:
                raise ValueError('Falta asignación para ' + username + '; complete CSV o use --integrante')
            assignment = dict(fallback[0], username=username)
            rows.append(assignment)
        carnet = assignment['carnet']
        result = cognito.admin_get_user(UserPoolId=outputs['UserPoolId'], Username=username)
        attrs = {a['Name']: a['Value'] for a in result['UserAttributes']}
        groups = []
        group_args = {'UserPoolId': outputs['UserPoolId'], 'Username': username}
        while True:
            page = cognito.admin_list_groups_for_user(**group_args)
            groups.extend(g['GroupName'] for g in page['Groups'])
            if not page.get('NextToken'): break
            group_args['NextToken'] = page['NextToken']
        user = users.get_item(Key={'placa': placa}, ConsistentRead=True).get('Item')
        if (not user or user.get('sub') != attrs.get('sub') or
                attrs.get('custom:placa') != placa or 'conductor' not in groups):
            raise ValueError('Identidad inconsistente: ' + username)
        if user.get('carnet') not in (None, carnet) or attrs.get('custom:carnet') not in (None, carnet):
            raise ValueError('Ya tiene otro carné; revisar asignación: ' + username)
        plan.append((username, placa, carnet, attrs, user))
        print(f'{username} -> {assignment["integrante"]}, carné {carnet}')
    if not args.apply:
        print('VISTA PREVIA: no se modificó AWS ni el CSV. Añada --apply para aplicar.')
        return
    # Save explicit intended assignments first so retries use the same mapping.
    temporary = path.with_suffix('.csv.tmp')
    with temporary.open('w', encoding='utf-8', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=['integrante', 'carnet', 'username'])
        writer.writeheader(); writer.writerows(rows)
    os.replace(temporary, path)
    for username, placa, carnet, attrs, user in plan:
        if attrs.get('custom:carnet') != carnet:
            cognito.admin_update_user_attributes(UserPoolId=outputs['UserPoolId'], Username=username,
                UserAttributes=[{'Name': 'custom:carnet', 'Value': carnet}])
        if user.get('carnet') != carnet:
            users.update_item(Key={'placa': placa},
                UpdateExpression='SET carnet=:c, #v=#v+:one',
                ConditionExpression='#s=:s AND #v=:v AND attribute_not_exists(carnet)',
                ExpressionAttributeNames={'#v': 'version', '#s': 'sub'},
                ExpressionAttributeValues={':c': carnet, ':s': user['sub'], ':v': user['version'], ':one': 1})
    print('Identidades completas. Saldos sin cambios. Renueve sesiones de los conductores.')


if __name__ == '__main__':
    main()
