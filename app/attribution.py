"""Credits use observation years, never the demonstration's event year."""
from datetime import datetime

from app.models import ATTRIBUTIONS


def run_attributions(run):
    provenance = run.get('provenance') or {}
    observations = [*(provenance.get('radar_scenes') or []),
                    *(provenance.get('optical_observations') or [])]
    years = set()
    if run.get('mode') == 'live':
        for observation in observations:
            try:
                years.add(datetime.fromisoformat(observation['acquired'].replace('Z', '+00:00')).year)
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
    sentinel = 'Contains modified Copernicus Sentinel data'
    credit = sentinel + (' ' + ', '.join(map(str, sorted(years))) if years else '') + '.'
    recorded = run.get('attributions')
    others = [text for text in (recorded if isinstance(recorded, list) else [])
              if isinstance(text, str) and not text.startswith(sentinel)]
    return list(dict.fromkeys([credit, *ATTRIBUTIONS[1:], *others]))
