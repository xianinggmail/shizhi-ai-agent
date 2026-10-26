"""Startup check: imports dependencies and fingerprints code; never reads credentials."""
from pathlib import Path
import hashlib
import sys
import pypdf
root = Path(sys.argv[1])
print(hashlib.sha256(b'\0'.join((root/name).read_bytes() for name in
    ('app.py', 'paper_lab.py', 'deepseek_ai.py', 'citations.py', 'extractor.py', 'lan_access.py'))).hexdigest())
