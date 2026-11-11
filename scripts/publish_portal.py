"""Compatibility wrapper for the portal publisher."""
from publish_portals import publish
import argparse
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--stack',default='guatepass-dev');parser.add_argument('--region',default='us-east-1');args=parser.parse_args()
    publish(args.stack,args.region)
