from importlib.metadata import distribution, distributions
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from pathlib import Path
import sys
import sysconfig

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).resolve().parent
project = root.parent
sources = collect_data_files('marsd') + collect_data_files('pint')
runtime_names = set()
pending = ['marsd']
while pending:
    name = canonicalize_name(pending.pop())
    if name in runtime_names:
        continue
    runtime_names.add(name)
    for value in distribution(name).requires or []:
        requirement = Requirement(value)
        if requirement.marker is None or requirement.marker.evaluate({'extra': ''}):
            pending.append(requirement.name)
for package in distributions():
    name = package.metadata['Name']
    if canonicalize_name(name) in runtime_names:
        sources += copy_metadata(name)
    for entry in package.files or []:
        if '.dist-info/licenses/' in str(entry) or entry.name.upper() in {'LICENSE','LICENSE.TXT','COPYING','NOTICE','COPYRIGHT.TXT'}:
            path = Path(package.locate_file(entry))
            if path.is_file():
                sources.append((str(path), str(Path('licenses/python') / name / str(entry.parent).replace('..','_'))))
license_file = next((path for path in [Path(sysconfig.get_path('stdlib')) / 'LICENSE.txt', Path(sys.base_prefix) / 'LICENSE.txt', Path(sys.base_prefix) / 'LICENSE'] if path.is_file()), None)
if license_file is None:
    raise SystemExit('The CPython LICENSE.txt must be available before runtime distribution')
sources.append((str(license_file), 'licenses/cpython'))
sources.append((str(project / 'LICENSE'), 'licenses/marsd'))
analysis = Analysis([str(root / 'python/entry.py')], pathex=[str(project/'src')], binaries=[], datas=sources,
    hiddenimports=collect_submodules('marsd') + ['pyarrow.parquet','uvicorn.logging','uvicorn.loops.asyncio','uvicorn.protocols.http.h11_impl','uvicorn.lifespan.on'],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=['pytest','mypy','ruff','tkinter','IPython','matplotlib','scipy','pandas','PySide6','PyQt6'], noarchive=False)
archive = PYZ(analysis.pure)
executable = EXE(archive, analysis.scripts, [], exclude_binaries=True, name='marsd-backend', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=True, disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None, codesign_identity=None, entitlements_file=None)
collection = COLLECT(executable, analysis.binaries, analysis.datas, strip=False, upx=False, name='backend')
