"""Shared saved-evidence predicates. Unknown observations never imply absence."""


def exposure_class(properties):
    if properties.get('exposed') is True:
        return 'exposed'
    if (properties.get('exposed') is False
            and properties.get('assessment_status') in ('no_mapped_change_at_observed_samples', 'no_mapped_change_in_assessed_hazard_scope')
            and properties.get('assessment_complete') is not False
            and not properties.get('unavailable_hazards')):
        return 'not_exposed'
    return 'unassessed'


def connectivity_matches(properties, status):
    saved = properties.get('status')
    if status == 'all':
        return True
    if status == 'not_connected':
        return saved in ('cut_off', 'baseline_disconnected')
    if status == 'baseline_connected':
        return saved in ('connected', 'cut_off', 'uncertain_access')
    return saved == status
