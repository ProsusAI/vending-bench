"""Build a portable source-and-results archive, excluding local runs and credentials.

    python3 scripts/package_bench.py
"""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'dist/prosus-vending-bench.zip'

FILES = ('README.md', 'LICENSE', 'NOTICE', 'CITATION.cff',
         'CONTRIBUTING.md', 'CODE_OF_CONDUCT.md', 'SECURITY.md')
TREES = ('tasks/vending-bench', 'scripts', 'tests', 'results')
SKIP = ('__pycache__', '.pytest_cache', '.DS_Store')


def main():
    OUT.parent.mkdir(exist_ok=True)
    include = [ROOT / name for name in FILES if (ROOT / name).exists()]
    for tree in TREES:
        include.extend(p for p in (ROOT / tree).rglob('*')
                       if p.is_file() and not any(s in p.parts or p.name == s for s in SKIP))
    with zipfile.ZipFile(OUT, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(include):
            archive.write(path, Path('prosus-vending-bench') / path.relative_to(ROOT))
    print(f'{OUT}  ({OUT.stat().st_size / 1e6:.1f} MB, {len(include)} files)')


if __name__ == '__main__':
    main()
