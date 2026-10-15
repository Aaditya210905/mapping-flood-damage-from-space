"""Keep independent activation downloads separate from each other."""
import json
from pathlib import Path
import re

from app.sources import REFERENCE_ROOT


def activation_code(value):
    code=str(value).strip().upper()
    if not re.fullmatch(r'EMSR[0-9]{1,8}',code):
        raise ValueError('Supply an explicit EMS activation code, for example EMSR927')
    return code


def reference_directory(activation,output=None):
    code=activation_code(activation)
    folder=Path(output).resolve() if output is not None else (REFERENCE_ROOT/code).resolve()
    if folder.exists():
        if not folder.is_dir():raise ValueError('Reference output must be a directory')
        identified=False
        for name in ('manifest.json','activation.json'):
            path=folder/name
            if not path.exists():continue
            try:
                data=json.loads(path.read_text())
                if not isinstance(data,dict):raise TypeError('Reference metadata is not an object')
                recorded=data.get('activation') if name=='manifest.json' else data['results'][0]['code']
                if recorded!=code:raise ValueError('Reference output belongs to a different activation; choose another directory')
                identified=True
            except (OSError,KeyError,IndexError,TypeError,json.JSONDecodeError) as error:
                raise ValueError('Existing reference output has invalid activation metadata; choose another directory') from error
        if not identified and any(folder.iterdir()):
            raise ValueError('Existing nonempty reference output has no activation identity; choose another directory')
    return folder
