"""Bounded read-only HTTP access; no database or model credentials."""
import json,os
from urllib.request import urlopen
from urllib.error import URLError,HTTPError


class APIError(RuntimeError):
    pass


def read(path,binary=False):
    if not path.startswith('/') or '..' in path:
        raise APIError('Unsupported data request')
    base=os.environ.get('RDP_API_BASE_URL','http://api:8000').rstrip('/')
    try:
        with urlopen(base+path,timeout=10) as response:
            data=response.read(8388609)
        if len(data)>8388608:
            raise APIError('Evidence response exceeds the dashboard limit')
        return data if binary else json.loads(data)
    except (URLError,HTTPError,ValueError,OSError) as error:
        raise APIError('The evidence service is unavailable or an integrity check failed. Start the API or inspect its data-quality findings.') from error
