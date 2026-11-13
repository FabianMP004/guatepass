import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

# PyYAML does not know CloudFormation's shorthand tags. Tests that inspect
# template.yaml must read them as the equivalent long-form mappings.
import yaml

yaml.SafeLoader.add_constructor(
    '!Ref', lambda loader, node: {'Ref': loader.construct_scalar(node)})
yaml.SafeLoader.add_constructor(
    '!Sub', lambda loader, node: {'Fn::Sub': loader.construct_scalar(node)})
yaml.SafeLoader.add_constructor(
    '!GetAtt', lambda loader, node: {
        'Fn::GetAtt': loader.construct_scalar(node).split('.', 1)})
