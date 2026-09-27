"""Compatibility entry point: old text posting has been disabled."""
import argparse
import json
from discord_images import DEFAULT, enqueue, layout

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--image', action='append', required=True)
args = parser.parse_args()
layout(DEFAULT)
for image in args.image:
    print(json.dumps(enqueue(DEFAULT, image)))
